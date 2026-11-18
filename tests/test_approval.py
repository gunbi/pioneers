from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from progress import Progress

ROOT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-B", str(ROOT / "pioneers.py")]
OFFLINE_ENV = {**os.environ, "PIONEERS_SKILLS_OFFLINE": "1"}


class ApprovalTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.workflow = self.root / "workflow.json"
        self.artifact = self.root / "artifact.txt"

    def cli(self, *args, code=0):
        process = subprocess.run([*CLI, *map(str, args)], capture_output=True, text=True, timeout=10, env=OFFLINE_ENV)
        self.assertEqual(process.returncode, code, process.stdout + process.stderr)
        return json.loads(process.stdout) if code == 0 else process.stderr

    def write_workflow(self, tasks):
        self.workflow.write_text(json.dumps({"goal": "团队交付", "tasks": tasks}), encoding="utf-8")

    def task(self, key, deps=(), **fields):
        return {"id": key, "role": "pm", "prompt": f"{key} 的决策和交付",
                "complexity": {"level": "medium", "reason": "涉及协作"}, "depends_on": list(deps),
                "tools": [], "skills": [], "acceptance": ["记录且验证"], **fields}

    def git(self, workspace, *args):
        return subprocess.run(["git", "-C", str(workspace), *args], check=True, capture_output=True, text=True).stdout.strip()

    def git_project(self):
        path = self.root / "project"
        path.mkdir()
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Pioneers Test")
        self.git(path, "config", "user.email", "test@example.com")
        self.git(path, "config", "core.hooksPath", str(self.root / "test-hooks"))
        (path / "module").mkdir()
        for file in ("module/a.txt", "module/b.txt", "outside.txt"):
            (path / file).write_text("original", encoding="utf-8")
        self.git(path, "add", ".")
        self.git(path, "commit", "-qm", "baseline")
        return path

    def host(self, tasks, workspace=None):
        self.write_workflow(tasks)
        self.host_process = subprocess.Popen(
            [*CLI, "--host-start", str(self.workflow), "--workspace", str(workspace or self.root),
             "--output-dir", str(self.root), "--no-open-browser"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV,
        )
        self.addCleanup(self.stop_host, self.host_process)
        self.started = json.loads(self.host_process.stdout.readline())
        self.run_dir = Path(self.started["run_dir"])

    def stop_host(self, process):
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=3)

    def claim(self, key, *, code=0):
        return self.cli("--host-claim", self.run_dir, "--task", key, code=code)

    def submit(self, key, body, attempt=1):
        self.artifact.write_text(body, encoding="utf-8")
        return self.cli("--host-report", self.run_dir, "--task", key, "--attempt", attempt, "--state", "completed",
                        "--activity", "提交产物", "--artifact", self.artifact)["judgment"]

    def judge(self, assignment, required=False, impact=None, *, code=0):
        self.artifact.write_text(json.dumps({"needs_confirmation": required, "reason": "结果存在待定项" if required else "证据充分",
                                             "impact": impact}), encoding="utf-8")
        return self.cli("--judge", self.run_dir, "--decision", assignment["decision"], "--task", assignment["task"],
                        "--attempt", assignment["attempt"], "--artifact", self.artifact, code=code)

    def state(self):
        return json.loads((self.run_dir / "status.json").read_text(encoding="utf-8"))

    def remove_source_from_snapshot(self):
        path = self.run_dir / "skill-sources.json"
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        source = next(iter(snapshot))
        del snapshot[source]
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        return path, source

    def expire(self, key):
        with Progress.locked(self.run_dir) as current:
            current.data["tasks"][key]["lease_expires_at"] = time.time() - 1

    def test_host_recovery_fences_old_attempt_and_keeps_worktree(self):
        project = self.git_project()
        self.host([
            self.task("fix", role="engineer", write_paths=["module/a.txt"]),
            self.task("qa", ["fix"], role="qa", review=True),
        ], project)
        old = self.claim("fix")
        heartbeat = self.cli("--host-heartbeat", self.run_dir, "--task", "fix", "--attempt", 1)
        self.assertGreater(heartbeat["lease_expires_at"], old["lease_expires_at"])
        draft = Path(old["workspace"]) / "module/a.txt"
        draft.write_text("old executor draft", encoding="utf-8")
        self.expire("fix")
        self.assertIn("expired lease", self.cli("--host-heartbeat", self.run_dir, "--task", "fix", "--attempt", 1, code=1))
        dispatch = self.cli("--host-ready", self.run_dir, "--wait", 1)
        self.assertEqual([(row["task"], row["attempt"]) for row in dispatch["ready"]], [("fix", 2)])
        self.assertEqual(self.state()["tasks"]["qa"]["state"], "pending")
        retry = self.claim("fix")
        self.assertNotEqual(retry["workspace"], old["workspace"])
        self.assertEqual(draft.read_text(), "old executor draft")
        self.assertEqual((Path(retry["workspace"]) / "module/a.txt").read_text(), "original")
        self.artifact.write_text("迟到产物", encoding="utf-8")
        self.assertIn("active --attempt 2", self.cli("--host-report", self.run_dir, "--task", "fix", "--attempt", 1,
                                                     "--state", "completed", "--activity", "迟到", "--artifact", self.artifact,
                                                     code=1))
        self.assertFalse((self.run_dir / "fix.md").exists())
        failed = self.cli("--host-report", self.run_dir, "--task", "fix", "--attempt", 2,
                          "--state", "lost", "--activity", "执行端进程退出")
        self.assertEqual((failed["state"], failed["attempt"]), ("pending", 3))
        self.claim("fix")
        self.expire("fix")
        board = self.cli("--host-ready", self.run_dir)
        lost = next(row for row in board["waiting"] if row["task"] == "fix")
        self.assertEqual(lost["state"], "recovery_required")
        history = self.state()["tasks"]["fix"]["recoveries"]
        self.assertEqual([item["automatic"] for item in history], [True, True, False])
        self.assertEqual(history[0]["git"]["workspace"], old["workspace"])

    def checkpoint(self, task, attempt, **event):
        self.artifact.write_text(json.dumps(event), encoding="utf-8")
        return self.cli("--host-checkpoint", self.run_dir, "--task", task, "--attempt", attempt,
                        "--artifact", self.artifact)

    def test_host_prefers_original_member_and_carries_execution_history(self):
        self.host([self.task("analysis"), self.task("other"), self.task("handoff")])
        claim = lambda task, *member: self.cli("--host-claim", self.run_dir, "--task", task, *member)
        claim("analysis", "--member", "alice", "--member-name", "Alice")
        self.checkpoint("analysis", 1, type="checkpoint", summary="已定位到解析器", session_id="private-session")
        self.checkpoint("analysis", 1, type="operation", id="inspect", kind="read", status="completed", summary="已检查配置")
        self.checkpoint("analysis", 1, type="operation", id="query", kind="tool", status="started", summary="结果尚未回报")
        self.expire("analysis")
        ready = self.cli("--host-ready", self.run_dir)["ready"]
        assignment = next(job for job in ready if job["task"] == "analysis")
        self.assertEqual(assignment["preferred_member"]["id"], "alice")
        recovered = claim("analysis")
        self.assertEqual(recovered["member"]["id"], "alice")
        continuation = recovered["continuation"]
        self.assertEqual(continuation["checkpoint"]["summary"], "已定位到解析器")
        self.assertEqual({item["id"]: item["status"] for item in continuation["operations"]},
                         {"inspect": "completed", "query": "unknown"})
        self.assertIn("已检查配置", recovered["prompt"])
        self.assertNotIn("resume_session_id", continuation)
        self.assertNotIn("private-session", recovered["prompt"])
        self.assertIn("active --attempt 2", self.cli("--host-checkpoint", self.run_dir, "--task", "analysis",
                                                     "--attempt", 1, "--artifact", self.artifact, code=1))
        for path in ("status.json", "dashboard-data.json"):
            with urlopen(self.started["dashboard_url"] + path, timeout=3) as response:
                payload = response.read()
                self.assertNotIn(b"private-session", payload)
                self.assertIn(b"inspect", payload)

        for task in ("other", "handoff"):
            claim(task, "--member", "alice")
            self.checkpoint(task, 1, type="checkpoint", summary="尚待整理", session_id=task + "-session")
            self.cli("--host-report", self.run_dir, "--task", task, "--attempt", 1,
                     "--state", "lost", "--activity", "旧执行端已退出")
        self.assertEqual(claim("other")["continuation"]["resume_session_id"], "other-session")
        handoff = claim("handoff", "--member", "bob")
        self.assertEqual(handoff["member"]["id"], "bob")
        self.assertEqual(handoff["continuation"]["previous_member"]["id"], "alice")
        self.assertNotIn("resume_session_id", handoff["continuation"])

    def test_external_receipt_is_required_before_recovery_and_prevents_replay(self):
        self.host([self.task("send", effect="send"), self.task("next", ["send"]),
                   self.task("publish", ["send"], effect="publish")])
        self.cli("--approve", self.run_dir, "--all", "--reason", "授权发送")
        self.claim("send")
        self.checkpoint("send", 1, type="operation", id="mail", kind="external", status="started",
                        summary="已调用发送，回执未返回")
        self.expire("send")
        self.cli("--host-ready", self.run_dir)
        self.assertIn("Verify unresolved external operations", self.cli("--recover", self.run_dir, "--task", "send",
                                                                         "--attempt", 1, "--reason", "查看回执", code=1))
        checks = self.root / "checks.json"
        checks.write_text(json.dumps([{"id": "mail", "outcome": "completed", "reason": "外部系统有邮件回执"}]))
        self.assertIn("adopt verified evidence", self.cli("--recover", self.run_dir, "--task", "send", "--attempt", 1,
                                                            "--reason", "核对回执", "--recovery-checks", checks, code=1))
        self.artifact.write_text("已投递，外部系统显示回执。")
        adopted = self.cli("--recover", self.run_dir, "--task", "send", "--attempt", 1, "--reason", "核对回执",
                           "--recovery-checks", checks, "--artifact", self.artifact)
        self.judge(adopted["judgment"])
        result = self.state()["tasks"]["send"]["execution"]["1"]["operations"]["mail"]
        self.assertEqual(result["verification"]["outcome"], "completed")
        self.claim("next")
        self.cli("--approve", self.run_dir, "--all", "--reason", "授权发布")
        self.claim("publish")
        self.checkpoint("publish", 1, type="operation", id="publish-1", kind="external", status="started",
                        summary="已准备发布，连接中断")
        self.cli("--host-report", self.run_dir, "--task", "publish", "--attempt", 1,
                 "--state", "lost", "--activity", "执行端退出")
        checks.write_text(json.dumps([{"id": "publish-1", "outcome": "not_executed", "reason": "发布系统没有此记录"}]))
        recovered = self.cli("--recover", self.run_dir, "--task", "publish", "--attempt", 1, "--reason", "已确保旧执行关闭",
                             "--recovery-checks", checks)
        self.assertEqual(recovered["state"], "awaiting_confirmation")
        self.claim("publish", code=1)

    def test_live_adapter_receives_checkpoint_and_session_on_retry(self):
        workspace = self.git_project()
        self.write_workflow([self.task("analysis")])
        runner = self.root / "runner"
        runner.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "job = json.load(sys.stdin)\n"
            "def emit(**event): print(json.dumps(event), flush=True)\n"
            "if job['phase'] == 'approval':\n"
            "    emit(type='result',message=json.dumps({'needs_confirmation':False,'reason':'通过'}))\n"
            "elif job['attempt'] == 1:\n"
            "    emit(type='checkpoint',summary='已读取项目材料',session_id='session-1')\n"
            "    emit(type='operation',id='read',kind='read',status='completed',summary='已读取文档')\n"
            "    sys.exit(17)\n"
            "else:\n"
            "    resume = job['continuation']\n"
            "    assert resume['resume_session_id'] == 'session-1' and resume['operations'][0]['id'] == 'read'\n"
            "    assert resume['checkpoint']['summary'] == '已读取项目材料'\n"
            "    emit(type='result',message='已续接完成')\n", encoding="utf-8",
        )
        runner.chmod(0o755)
        result = subprocess.run([*CLI, str(self.workflow), "--live", "--runner", str(runner),
                                 "--workspace", str(workspace), "--output-dir", str(self.root), "--no-open-browser"],
                                capture_output=True, text=True, env=OFFLINE_ENV, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.run_dir = next(self.root.glob("run-*"))
        self.assertEqual(self.state()["tasks"]["analysis"]["attempt"], 2)
        self.assertIn("已续接完成", (self.run_dir / "analysis.2.md").read_text())

    def test_host_requires_review_of_shared_writes_and_external_effects(self):
        self.host([
            self.task("writer", role="engineer"),
            self.task("next-writer", role="engineer"),
            self.task("qa", ["writer", "next-writer"], role="qa", review=True),
            self.task("send", effect="send"),
            self.task("publish", ["send"], effect="publish"),
        ])
        self.claim("writer")
        self.cli("--approve", self.run_dir, "--all", "--reason", "授权执行发送")
        self.claim("send")
        self.expire("writer")
        self.expire("send")
        self.cli("--host-ready", self.run_dir)
        self.assertIn("shared workspace", self.claim("next-writer", code=1))
        self.assertIn("waiting for: send", self.claim("publish", code=1))
        self.assertEqual(self.state()["tasks"]["send"]["state"], "recovery_required")

        url = self.started["dashboard_url"]
        body = json.dumps({"action": "recover", "task": "writer", "attempt": 1, "reason": "旧进程已停止，残留已核对"}).encode()
        request = Request(url + "human-action", data=body,
                          headers={"Content-Type": "application/json", "Origin": url.rstrip("/")})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.load(response)["attempt"], 2)
        self.assertEqual(self.claim("writer")["attempt"], 2)
        self.artifact.write_text("邮件已投递。凭证：外部收件记录。", encoding="utf-8")
        recovered = self.cli("--recover", self.run_dir, "--task", "send", "--attempt", 1,
                             "--artifact", self.artifact, "--reason", "核对外部收件记录")
        self.judge(recovered["judgment"])
        self.assertEqual(self.state()["tasks"]["send"]["state"], "completed")
        self.assertEqual(self.state()["tasks"]["publish"]["state"], "awaiting_confirmation")
        self.cli("--approve", self.run_dir, "--all", "--reason", "授权发布")
        self.claim("publish")
        self.expire("publish")
        self.cli("--host-ready", self.run_dir)
        previous = self.state()["tasks"]["publish"]["authorization"]
        self.cli("--recover", self.run_dir, "--task", "publish", "--attempt", 1, "--reason", "已核对发布未发生")
        status = self.state()
        new = status["tasks"]["publish"]["authorization"]
        self.assertNotEqual(previous, new)
        self.assertEqual(status["decisions"][previous]["state"], "superseded")
        self.assertEqual(status["decisions"][new]["state"], "waiting")
        self.claim("publish", code=1)

    def test_continue_recovers_lost_live_worker_and_preserves_completed_tasks(self):
        self.write_workflow([self.task("first"), self.task("second", ["first"])])
        project = self.git_project()
        runner = self.root / "runner"
        runner.write_text(
            "#!/usr/bin/env python3\nimport json, sys, time\nfrom pathlib import Path\n"
            "job = json.load(sys.stdin)\n"
            "if job['phase'] == 'approval':\n"
            "    result = json.dumps({'needs_confirmation': False, 'reason': '证据完整'})\n"
            "elif 'first 的决策和交付' in job['prompt'].split('当前任务：', 1)[1].splitlines()[0]:\n"
            "    Path('first-count').write_text(Path('first-count').read_text() + 'X' if Path('first-count').exists() else 'X')\n"
            "    result = '第一个任务完成'\n"
            "else:\n"
            "    if not Path('worker-started').exists():\n"
            "        Path('worker-started').touch()\n"
            "        until = time.monotonic() + 12\n"
            "        while not Path('stop-old-worker').exists() and time.monotonic() < until:\n"
            "            time.sleep(0.02)\n"
            "    result = '第二个任务完成'\n"
            "try:\n"
            "    print(json.dumps({'type': 'result', 'message': result}), flush=True)\n"
            "except BrokenPipeError:\n"
            "    pass\n", encoding="utf-8",
        )
        runner.chmod(0o755)
        process = subprocess.Popen([*CLI, str(self.workflow), "--live", "--runner", str(runner),
                                    "--workspace", str(project), "--output-dir", str(self.root), "--no-open-browser"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        self.addCleanup(self.stop_host, process)
        self.addCleanup((project / "stop-old-worker").touch)
        self.assertEqual(process.stdout.readline().strip(), "Mode: live")
        self.run_dir = Path(process.stdout.readline().strip().split("Run output: ")[1])
        started = project / "worker-started"
        end = time.monotonic() + 8
        while not started.exists() and time.monotonic() < end:
            time.sleep(0.025)
        self.assertTrue(started.exists())
        self.assertIn("already active", self.cli("--continue", self.run_dir, "--no-open-browser", code=1))
        process.kill()
        process.communicate(timeout=3)
        original = (self.run_dir / "first.md").read_bytes()
        resumed = subprocess.run([*CLI, "--continue", str(self.run_dir), "--no-open-browser"],
                                 capture_output=True, text=True, env=OFFLINE_ENV, timeout=10)
        self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
        self.assertEqual(self.state()["state"], "completed")
        self.assertEqual(self.state()["tasks"]["second"]["attempt"], 2)
        self.assertEqual((self.run_dir / "first.md").read_bytes(), original)
        self.assertEqual((project / "first-count").read_text(), "X")
        self.assertTrue((self.run_dir / "second.2.md").exists())

    def test_project_board_keeps_iteration_history_and_routes_actions(self):
        workspace = self.root / "project"
        workspace.mkdir()
        output = self.root / "iterations"
        command = [*CLI, "--project-dashboard", "--workspace", str(workspace), "--no-open-browser"]

        def start_board(port):
            process = subprocess.Popen([*command, "--port", str(port), "--output-dir", str(output)],
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
            self.addCleanup(self.stop_host, process)
            return process, process.stdout.readline().strip().removeprefix("Dashboard: ")

        def get(path):
            with urlopen(url + path, timeout=3) as response:
                return json.load(response)

        def next_iteration(*tasks):
            self.write_workflow(tasks)
            result = self.cli("--host-start", self.workflow, "--workspace", workspace,
                              "--output-dir", output, "--register-only")
            self.assertEqual(result["project_dashboard_url"], url)
            self.run_dir = Path(result["run_dir"])
            self.assertEqual(result["project_run_url"], url + f"runs/{self.run_dir.name}/")
            return self.run_dir

        board, url = start_board(0)
        self.assertEqual(get("project-data.json")["runs"], [])
        first = next_iteration(self.task("requirements"))
        self.claim("requirements")
        self.judge(self.submit("requirements", "已明确离线保存规则"))
        second = next_iteration(self.task("design", impact="architecture"),
                                self.task("implementation", ["design"], role="engineer"),
                                self.task("qa", ["implementation"], role="qa", review=True))
        self.claim("design")
        decision = self.submit("design", "# 选定存储方案")
        self.judge(decision)
        self.assertEqual(self.state()["tasks"]["implementation"]["state"], "pending")
        first_prefix = f"runs/{first.name}/"
        second_prefix = f"runs/{second.name}/"
        state = get("project-data.json")
        self.assertEqual([run["id"] for run in state["runs"]], [second.name, first.name])
        self.assertEqual([(item["run"], item["id"]) for item in state["pending"]], [(second.name, decision["decision"])])
        with urlopen(url + second_prefix + "?decision=" + decision["decision"], timeout=3) as response:
            self.assertIn('fetch("human-action"'.encode(), response.read())
        with urlopen(url + first_prefix + "summary.md", timeout=3) as response:
            self.assertIn("[查看产物](evidence/requirements.md)", response.read().decode())
        with urlopen(url + first_prefix + "evidence/requirements.md", timeout=3) as response:
            self.assertIn("已明确离线保存规则", response.read().decode())
        self.assertEqual(get(first_prefix + "workflow.json")["tasks"][0]["id"], "requirements")
        self.assertEqual(get(first_prefix + "dashboard-data.json")["summary"]["results"][0]["result"], "已明确离线保存规则")
        with self.assertRaises(HTTPError) as missing:
            get("runs/run-unknown/dashboard-data.json")
        self.assertEqual(missing.exception.code, 404)
        with self.assertRaises(HTTPError) as unfinished:
            get(second_prefix + "summary.md")
        self.assertEqual(unfinished.exception.code, 404)

        body = json.dumps({"action": "approve", "all": True, "reason": "设计已确认"}).encode()
        request = Request(url + second_prefix + "human-action", data=body,
                          headers={"Content-Type": "application/json", "Origin": url.rstrip("/")})
        with urlopen(request, timeout=3) as response:
            self.assertEqual(json.load(response)["approved"], [decision["decision"]])
        self.assertEqual(get("project-data.json")["pending"], [])
        self.assertEqual(self.claim("implementation")["task"], "implementation")
        self.assertEqual(get(first_prefix + "status.json")["state"], "completed")

        other = self.root / "other"
        other.mkdir()
        result = self.cli("--host-start", self.workflow, "--workspace", other,
                          "--output-dir", output, "--register-only")
        with self.assertRaises(HTTPError) as isolated:
            get("runs/" + Path(result["run_dir"]).name + "/status.json")
        self.assertEqual(isolated.exception.code, 404)
        history = self.cli("--project-history", "--workspace", workspace, "--output-dir", output)
        self.assertEqual(history["latest_delivery"]["run"], first.name)
        self.assertEqual(len(history["runs"]), 2)

        port = url.split(":")[-1].strip("/")
        self.stop_host(board)
        _, restarted_url = start_board(port)
        self.assertEqual(restarted_url, url)
        self.assertEqual(len(get("project-data.json")["runs"]), 2)
        self.judge(self.submit("implementation", "已实现存储与读取"))
        self.claim("qa")
        verdict = {"outcome": "accepted", "summary": "已验证离线保存", "repair_task": None,
                   "checks": [{"criterion": "记录且验证", "status": "pass", "evidence": "离线用例通过"}]}
        self.judge(self.submit("qa", json.dumps(verdict)))
        summary = get(second_prefix + "dashboard-data.json")["summary"]
        self.assertEqual(summary["results"][-1]["evidence"], "qa.json")
        self.assertEqual(summary["approvals"][0]["operator_reason"], "设计已确认")
        self.cli("--pause", self.run_dir, "--reason", "准备重新验证")
        self.cli("--intervene", self.run_dir, "--from", "implementation", "--reason", "补充测试")
        with self.assertRaises(HTTPError) as reopened:
            get(second_prefix + "summary.md")
        self.assertEqual(reopened.exception.code, 404)

    def test_host_start_serves_dashboard_before_first_claim_by_default(self):
        self.write_workflow([self.task("next", ["plan"]), self.task("plan", impact="architecture"), self.task("side")])
        process = subprocess.Popen([*CLI, "--host-start", str(self.workflow), "--workspace", str(self.root),
                                    "--output-dir", str(self.root), "--no-open-browser"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        try:
            result = json.loads(process.stdout.readline())
            self.run_dir = Path(result["run_dir"])
            self.assertTrue((self.run_dir / "skill-sources.json").is_file())
            self.assertGreater(sum(result["skill_sources"].values()), 0)
            url = result["dashboard_url"]
            with urlopen(url, timeout=3) as page:
                self.assertIn("团队协作流程", page.read().decode())
            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                page = json.load(response)
                self.assertEqual(page["tasks"]["plan"]["state"], "pending")
                self.assertEqual(page["dispatch"], self.cli("--host-ready", self.run_dir))
            self.claim("plan")
            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                page = json.load(response)
                self.assertEqual(page["tasks"]["plan"]["state"], "running")
                self.assertEqual(page["dispatch"], self.cli("--host-ready", self.run_dir))
            result = self.submit("plan", "架构交接")
            self.judge(result)
            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                page = json.load(response)
                self.assertEqual(page["dispatch"], self.cli("--host-ready", self.run_dir))
                downstream = next(item for item in page["dispatch"]["waiting"] if item["task"] == "next")
                self.assertEqual(downstream["blockers"][0]["decision"], result["decision"])
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)

    def test_dashboard_pause_through_forwarded_loopback_port_stops_new_claims(self):
        self.host([self.task("running"), self.task("remaining")])
        self.claim("running")
        self.stop_host(self.host_process)
        self.assertIn("Start the run dashboard", self.claim("remaining", code=1))
        reported = self.cli("--host-report", self.run_dir, "--task", "running", "--state", "running",
                            "--activity", "看板关闭期间仍可回报")
        self.assertEqual(reported["state"], "running")
        snapshot, source = self.remove_source_from_snapshot()
        process = subprocess.Popen([*CLI, "--dashboard", str(self.run_dir), "--port", "0", "--no-open-browser"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        try:
            url = process.stdout.readline().strip().split("Dashboard: ")[1]
            self.assertIn(source, json.loads(snapshot.read_text(encoding="utf-8")))
            other_port = 18080 if not url.endswith(":18080/") else 18081
            local_host = f"127.0.0.1:{other_port}"
            body = json.dumps({"action": "pause", "reason": "暂停以检查依赖"}).encode()
            headers = {"Content-Type": "application/json", "Host": local_host}
            for host, origin in [(local_host, "http://localhost:18082"),
                                 ("preview.example", "https://preview.example")]:
                with self.assertRaises(HTTPError) as rejected:
                    urlopen(Request(url + "human-action", data=body,
                                    headers={**headers, "Host": host, "Origin": origin}), timeout=3)
                with rejected.exception as response:
                    self.assertEqual(response.code, 403)
                    self.assertIn("SSH", json.load(response)["error"])
                self.assertFalse(self.state().get("paused"))

            with urlopen(Request(url + "human-action", data=body,
                                headers={**headers, "Origin": f"http://{local_host}"}), timeout=3) as response:
                self.assertEqual(json.load(response)["paused"], "暂停以检查依赖")
            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                page = json.load(response)
            self.assertEqual(page["paused"], "暂停以检查依赖")
            self.assertEqual(page["dispatch"]["ready"], [])
            self.assertIn("paused", self.claim("remaining", code=1))
            self.assertEqual(self.submit("running", "已启动任务的产物")["task"], "running")
            self.cli("--resume", self.run_dir, "--reason", "继续派工")
            self.assertEqual(self.claim("remaining")["task"], "remaining")
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)

    def test_cloud_controls_share_host_state_and_require_run_credential(self):
        self.write_workflow([self.task("plan", impact="architecture"), self.task("build", ["plan"]),
                             self.task("independent")])
        origin = "https://preview.example"
        process = subprocess.Popen([*CLI, "--host-start", str(self.workflow), "--serve-dashboard",
                                    "--workspace", str(self.root), "--output-dir", str(self.root),
                                    "--dashboard-origin", origin + "/"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        try:
            started = json.loads(process.stdout.readline())
            self.run_dir = Path(started["run_dir"])
            url = started["dashboard_url"]
            key = process.stderr.readline().strip().split(": ", 1)[1]
            self.assertGreaterEqual(len(key), 40)

            def action(name, *, request_origin=origin, credential=key, **fields):
                body = json.dumps({"action": name, "reason": name, **fields}).encode()
                headers = {"Origin": request_origin, "Content-Type": "application/json"}
                if credential is not None:
                    headers["X-Pioneers-Control"] = credential
                return urlopen(Request(url + "human-action", data=body, headers=headers), timeout=3)

            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                public_data = response.read()
                self.assertTrue(json.loads(public_data)["cloud_control"])
                self.assertTrue(key.encode() not in public_data)
            for request_origin, credential, status in [
                (url.rstrip("/"), None, 403),
                ("https://other.example", key, 403),
                (origin, None, 401),
                (origin, "wrong-key", 401),
            ]:
                with self.assertRaises(HTTPError) as rejected:
                    action("pause", request_origin=request_origin, credential=credential)
                with rejected.exception as response:
                    self.assertEqual(response.code, status)
                    self.assertIn("error", json.load(response))
                self.assertFalse(self.state().get("paused"))

            self.claim("plan")
            judgment = self.submit("plan", "架构方案")
            self.judge(judgment)
            with action("pause") as response:
                self.assertEqual(json.load(response)["paused"], "pause")
            self.assertIn("paused", self.claim("independent", code=1))
            with action("intervene", **{"from": "build"}) as response:
                self.assertEqual(json.load(response)["paused"], "pause")
            with action("approve", decisions=[judgment["decision"]]) as response:
                self.assertEqual(json.load(response)["approved"], [judgment["decision"]])
            self.assertIn("paused", self.claim("build", code=1))
            with action("resume") as response:
                self.assertIsNone(json.load(response)["paused"])
            self.assertEqual(self.claim("build")["task"], "build")
            state = self.state()
            self.assertEqual([item["action"] for item in state["operator_log"]], ["pause", "intervene", "approve", "resume"])
            self.assertTrue(key not in (self.run_dir / "status.json").read_text())
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)

    def test_dashboard_approval_wakes_waiting_host_coordinator(self):
        self.host([self.task("requirements", impact="feature_design"), self.task("next", ["requirements"])])
        self.claim("requirements")
        judgment = self.submit("requirements", "确认功能范围")
        self.judge(judgment)
        waiting = self.cli("--host-ready", self.run_dir, "--wait", "1")
        self.assertTrue(waiting["timed_out"])
        self.assertEqual(waiting["ready"], [])

        waiter = subprocess.Popen([*CLI, "--host-ready", str(self.run_dir), "--wait", "8"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        self.addCleanup(self.stop_host, waiter)
        with self.assertRaises(subprocess.TimeoutExpired):
            waiter.communicate(timeout=0.35)
        url = self.started["dashboard_url"]
        decision = judgment["decision"]
        body = json.dumps({"action": "approve", "decisions": [decision], "reason": "通过"}).encode()
        with urlopen(Request(url + "human-action", data=body,
                             headers={"Origin": url.rstrip("/"), "Content-Type": "application/json"}), timeout=3) as response:
            self.assertEqual(json.load(response)["approved"], [decision])
        stdout, stderr = waiter.communicate(timeout=3)
        self.assertEqual(waiter.returncode, 0, stderr)
        result = json.loads(stdout)
        self.assertNotIn("timed_out", result)
        self.assertEqual([item["task"] for item in result["ready"]], ["next"])
        self.assertEqual(self.claim("next")["task"], "next")

    def test_selective_approval_and_forced_design_impact(self):
        self.host([self.task("architecture", impact="architecture"), self.task("feature"),
                   self.task("build-a", ["architecture"]), self.task("build-b", ["feature"])])
        self.assertEqual(self.claim("architecture")["sandbox"], "read-only")
        architecture = self.submit("architecture", "重大架构方案")
        self.assertIn("waiting for", self.claim("build-a", code=1))
        self.assertEqual(self.judge(architecture)["task_state"], "awaiting_confirmation")
        self.claim("feature")
        feature = self.submit("feature", "重要功能方案")
        self.assertEqual(self.judge(feature, impact="feature_design")["task_state"], "awaiting_confirmation")
        self.cli("--approve", self.run_dir, "--decision", architecture["decision"], "--reason", "确认架构边界")
        self.claim("build-a")
        self.assertIn("waiting for", self.claim("build-b", code=1))
        self.assertEqual(self.state()["decisions"][feature["decision"]]["state"], "waiting")
        self.cli("--approve", self.run_dir, "--all", "--reason", "确认剩余功能方案")
        self.claim("build-b")

    def test_host_ready_exposes_parallel_work_and_approval_blockers(self):
        self.host([
            self.task("independent"),
            self.task("contract", impact="architecture"),
            self.task("backend", ["contract"], role="engineer", write_paths=["backend"]),
            self.task("frontend", ["contract"], role="engineer", write_paths=["frontend"]),
            self.task("integration", ["backend", "frontend"], role="engineer"),
            self.task("verification", ["integration"], role="qa", review=True),
        ], self.git_project())

        def queue():
            return self.cli("--host-ready", self.run_dir)

        def ready(result):
            return [item["task"] for item in result["ready"]]

        self.assertEqual(ready(queue()), ["contract", "independent"])
        self.claim("contract")
        approval = self.submit("contract", "共享接口已定义")
        self.judge(approval)
        result = queue()
        self.assertEqual(ready(result), ["independent"])
        waiting = {item["task"]: item["blockers"] for item in result["waiting"]}
        self.assertEqual(waiting["backend"][0]["state"], "awaiting_confirmation")
        self.assertEqual(waiting["frontend"][0]["decision"], approval["decision"])
        self.cli("--approve", self.run_dir, "--decision", approval["decision"], "--reason", "确认契约")
        self.assertEqual(ready(queue()), ["backend", "frontend", "independent"])
        state_before = self.state()
        queue()
        self.assertEqual(self.state(), state_before)
        with ThreadPoolExecutor(max_workers=2) as workers:
            assignments = list(workers.map(self.claim, ["backend", "frontend"]))
        self.assertEqual({item["task"] for item in assignments}, {"backend", "frontend"})
        result = queue()
        self.assertEqual({item["task"] for item in result["active"]}, {"backend", "frontend"})
        waiting = {item["task"]: item["blockers"] for item in result["waiting"]}
        self.assertEqual({item["task"] for item in waiting["integration"]}, {"backend", "frontend"})
        self.cli("--pause", self.run_dir, "--reason", "暂停派工")
        result = queue()
        self.assertEqual(ready(result), [])
        self.assertTrue(all(item["blockers"][0]["code"] == "paused" for item in result["waiting"]))

    def test_revision_invalidates_downstream_and_preserves_inputs(self):
        self.host([self.task("plan"), self.task("build", ["plan"]), self.task("verify", ["build"]), self.task("unrelated")])
        self.claim("plan")
        old = self.submit("plan", "原方案")
        self.judge(old)
        self.claim("build")
        self.judge(self.submit("build", "旧交付物"))
        self.artifact.write_text("新方案", encoding="utf-8")
        revised = self.cli("--revise", self.run_dir, "--decision", old["decision"], "--reason", "支持更大的流量",
                           "--artifact", self.artifact)["judgment"]
        self.assertIn("支持更大的流量", revised["prompt"])
        self.assertIn("新方案", revised["prompt"])
        self.assertEqual((self.run_dir / "plan.md").read_text(encoding="utf-8").strip(), "原方案")
        self.assertEqual(self.state()["decisions"][old["decision"]]["state"], "superseded")
        self.assertEqual(self.state()["tasks"]["build"]["attempt"], 2)
        self.judge(old, code=1)
        self.claim("build", code=1)
        self.claim("verify", code=1)
        self.claim("unrelated")
        self.judge(revised, required=True)
        self.cli("--approve", self.run_dir, "--decision", revised["decision"], "--reason", "接受新方案")
        self.assertIn("新方案", self.claim("build")["prompt"])

    def test_dashboard_instruction_replans_after_inflight_work(self):
        self.host([self.task("contract")], self.git_project())
        self.claim("contract")
        url = self.started["dashboard_url"]

        def action(name, reason):
            body = json.dumps({"action": name, "reason": reason}).encode()
            with urlopen(Request(url + "human-action", data=body, headers={
                "Origin": url.rstrip("/"), "Content-Type": "application/json",
            }), timeout=3) as response:
                return json.load(response)

        action("pause", "调整后续范围")
        note = "同步进行安卓和 ios 的开发"
        result = action("intervene", note)
        self.assertIsNone(result["paused"])
        self.assertEqual(result["replan"]["notes"], [note])
        self.assertFalse(self.cli("--host-ready", self.run_dir)["replan"]["ready"])
        self.assertIn("Wait for running tasks", self.cli("--replan", self.run_dir, code=1))
        judgment = self.submit("contract", "已定接口")
        self.judge(judgment)
        snapshot = self.state()
        self.assertEqual(snapshot["state"], "running")
        original = snapshot["tasks"]["contract"]
        with urlopen(url + "dashboard-data.json", timeout=3) as response:
            self.assertTrue(json.load(response)["dispatch"]["replan"]["ready"])
        request = self.cli("--replan", self.run_dir)
        self.assertEqual(request["role"], "ld")
        self.assertIn(note, request["prompt"])
        self.assertIn(original["evidence"], request["prompt"])
        revision = request["revision"]

        def submit_plan(tasks, *, version=revision, code=0):
            self.artifact.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
            return self.cli("--replan", self.run_dir, "--revision", version, "--artifact", self.artifact, code=code)

        android = self.task("android", ["contract"], role="engineer", skills=["android-development"], write_paths=["android"])
        ios = self.task("ios", ["contract"], role="engineer", skills=["ios-development"], write_paths=["ios"])
        qa = self.task("verify", ["android", "ios"], role="qa", review=True)
        self.assertIn("started or authorized", submit_plan([self.task("contract")], code=1))
        self.assertIn("independent QA", submit_plan([android, ios], code=1))
        self.assertIn("stale", submit_plan([android, ios, qa], version=revision + 1, code=1))
        self.assertEqual(submit_plan([android, ios, qa])["tasks"], ["android", "ios", "verify"])
        self.assertEqual(self.state()["tasks"]["contract"], original)
        with urlopen(url + "dashboard-data.json", timeout=3) as response:
            page = json.load(response)
        self.assertEqual({item["task"] for item in page["dispatch"]["ready"]}, {"android", "ios"})
        self.assertEqual(page["replan"]["state"], "applied")
        self.assertEqual(self.claim("android")["task"], "android")
        self.assertEqual(self.claim("ios")["task"], "ios")

    def test_host_isolates_fixes_blocks_overlaps_and_passes_commits_to_qa(self):
        project = self.git_project()
        self.host([
            self.task("fix-a", role="engineer", write_paths=["module/a.txt"]),
            self.task("fix-b", role="engineer", write_paths=["module/b.txt"]),
            self.task("overlap", role="engineer", write_paths=["module"]),
            self.task("verify", ["fix-a", "fix-b", "overlap"], role="qa", review=True),
        ], project)
        with ThreadPoolExecutor(max_workers=2) as workers:
            a, b = workers.map(self.claim, ["fix-a", "fix-b"])
        first, second = Path(a["workspace"]), Path(b["workspace"])
        self.assertNotEqual(first, second)
        self.assertNotEqual(a["git"]["branch"], b["git"]["branch"])
        self.assertIn(str(first), a["prompt"])
        self.assertIn("overlapping write paths", self.claim("overlap", code=1))
        board = self.cli("--host-ready", self.run_dir)
        overlap = next(item for item in board["waiting"] if item["task"] == "overlap")
        self.assertEqual({item["task"] for item in overlap["blockers"]}, {"fix-a", "fix-b"})

        def report_a_error():
            self.artifact.write_text("修复证据", encoding="utf-8")
            return self.cli("--host-report", self.run_dir, "--task", "fix-a", "--state", "completed",
                            "--activity", "提交产物", "--artifact", self.artifact, code=1)

        (first / "module/a.txt").write_text("fixed a")
        self.assertIn("Commit the task changes", report_a_error())
        (first / "outside.txt").write_text("outside the scope")
        self.git(first, "add", ".")
        self.git(first, "commit", "-qm", "fix A and stray change")
        self.assertIn("outside the assigned write paths: outside.txt", report_a_error())
        (first / "outside.txt").write_text("original")
        self.git(first, "add", "outside.txt")
        self.git(first, "commit", "-qm", "restore outside path")
        self.assertEqual((second / "module/a.txt").read_text(), "original")
        judgment_a = self.submit("fix-a", "A 已修复")
        self.judge(judgment_a, required=True)

        (second / "module/b.txt").write_text("fixed b")
        self.git(second, "add", "module/b.txt")
        self.git(second, "commit", "-qm", "fix B")
        self.judge(self.submit("fix-b", "B 已修复"))
        self.assertIn("overlapping write paths", self.claim("overlap", code=1))
        self.cli("--approve", self.run_dir, "--decision", judgment_a["decision"], "--reason", "确认修复")
        self.claim("overlap")
        self.judge(self.submit("overlap", "同模块复查，无新增代码"))
        qa = self.claim("verify")
        checkout = Path(qa["workspace"])
        self.assertEqual(qa["sandbox"], "read-only")
        self.assertEqual((checkout / "module/a.txt").read_text(), "fixed a")
        self.assertEqual((checkout / "module/b.txt").read_text(), "fixed b")
        self.assertEqual(self.git(project, "status", "--porcelain"), "?? AGENTS.md")

    def test_project_binding_preserves_rules_and_worktrees_across_runs(self):
        project = self.git_project()
        rules = project / "AGENTS.md"
        original = "# 项目原有规则\n\n用项目自己的测试命令。\n"
        rules.write_text(original, encoding="utf-8")
        self.git(project, "add", "AGENTS.md")
        self.git(project, "commit", "-qm", "project instructions")
        baseline = self.git(project, "rev-parse", "HEAD")

        self.host([self.task("first")], project)
        self.assertEqual(self.started["project_rules"], str(rules))
        content = rules.read_text(encoding="utf-8")
        self.assertTrue(content.startswith(original))
        self.assertIn("用户无需重复指定", content)
        self.assertIn("不另起运行", content)
        self.assertEqual(self.state()["git_base"], baseline)
        self.assertEqual(self.git(project, "config", "--local", "--get", "pioneers.framework"), str(ROOT))
        self.assertEqual(self.cli("--bind-project", "--workspace", project)["status"], "existing")
        self.host([self.task("second")], project)
        self.assertEqual(self.state()["git_base"], baseline)
        self.assertEqual(rules.read_text(encoding="utf-8"), content)
        rules.write_text(content + "\n# 尚未提交的项目约定\n", encoding="utf-8")
        self.host([self.task("third")], project)
        self.assertNotIn("git_base", self.state())

    def test_bind_project_can_enroll_an_existing_project_without_starting_run(self):
        original = "# 已交付项目\n"
        rules = self.root / "AGENTS.md"
        rules.write_text(original, encoding="utf-8")
        result = self.cli("--bind-project", "--workspace", self.root)
        self.assertEqual(result["status"], "bound")
        self.assertEqual(result["project_rules"], str(rules))
        self.assertTrue(rules.read_text(encoding="utf-8").startswith(original))
        self.assertEqual(self.cli("--bind-project", "--workspace", self.root)["status"], "existing")
        self.assertEqual(list(self.root.iterdir()), [rules])

    def test_host_serializes_writes_in_shared_workspace(self):
        self.host([
            self.task("one", role="engineer", write_paths=["a"]),
            self.task("two", role="engineer", write_paths=["b"]),
            self.task("verify", ["one", "two"], role="qa", review=True),
        ])
        self.assertEqual(self.claim("one")["workspace"], str(self.root))
        self.assertIn("shared workspace", self.claim("two", code=1))
        self.judge(self.submit("one", "已交接"))
        self.assertEqual(self.claim("two")["workspace"], str(self.root))

    def test_intervention_pauses_and_scopes_rerun(self):
        self.host([self.task("plan"), self.task("build", ["plan"]), self.task("other")])
        self.claim("plan")
        self.judge(self.submit("plan", "需求"))
        self.cli("--pause", self.run_dir, "--reason", "需要补充约束")
        self.assertIn("paused", self.claim("build", code=1))
        self.cli("--intervene", self.run_dir, "--from", "plan", "--reason", "保留旧数据")
        self.assertIn("paused", self.claim("plan", code=1))
        self.cli("--resume", self.run_dir, "--reason", "恢复执行")
        plan = self.claim("plan")
        self.assertEqual(plan["attempt"], 2)
        self.assertIn("保留旧数据", plan["prompt"])
        self.assertNotIn("保留旧数据", self.claim("other")["prompt"])

    def test_members_are_distinct_and_reruns_keep_original_owner(self):
        self.host([self.task("first"), self.task("second")])
        alice = self.cli("--host-claim", self.run_dir, "--task", "first",
                         "--member", "alice", "--member-name", "成员 A")
        bob = self.cli("--host-claim", self.run_dir, "--task", "second",
                       "--member", "bob", "--member-name", "成员 B")
        self.assertEqual(alice["member"]["role"], bob["member"]["role"])
        self.assertNotEqual(alice["member"]["id"], bob["member"]["id"])
        old = self.submit("first", "产物")
        self.judge(old, required=True)
        self.cli("--approve", self.run_dir, "--decision", old["decision"],
                 "--reason", "确认第一轮", "--approver", "审批员")
        self.assertEqual(self.state()["decisions"][old["decision"]]["approved_by"], "审批员")
        self.cli("--pause", self.run_dir, "--reason", "调整分工")
        self.cli("--intervene", self.run_dir, "--from", "first", "--reason", "重跑")
        self.assertNotIn("member", self.state()["tasks"]["first"])
        self.cli("--resume", self.run_dir, "--reason", "由成员 B 接手")
        new = self.cli("--host-claim", self.run_dir, "--task", "first", "--member", "bob")
        self.assertEqual((new["attempt"], new["member"]["name"]), (2, "成员 B"))
        events = self.state()["events"]
        self.assertTrue(any(x["task"] == "first" and x["attempt"] == 1 and x["member"]["id"] == "alice" for x in events))

    def test_hard_gate_for_effects_and_costly_execution(self):
        actions = ("send", "publish", "payment", "permissions", "delete", "irreversible")
        self.host([self.task(action, effect=action) for action in actions] + [self.task("costly", impact="costly_change")])
        for action in (*actions, "costly"):
            self.claim(action, code=1)
        waiting = self.state()["decisions"]
        self.assertEqual(len(waiting), 7)
        self.assertTrue(all(item["state"] == "waiting" for item in waiting.values()))
        first = next(iter(waiting))
        self.cli("--judge", self.run_dir, "--decision", first, code=1)
        proposed = self.cli("--revise", self.run_dir, "--decision", first, "--reason", "改为只发内部接收人")["decision"]
        self.assertEqual(proposed["state"], "waiting")
        self.claim("send", code=1)
        self.cli("--approve", self.run_dir, "--decision", proposed["id"], "--reason", "确认变更后的范围")
        self.assertIn("只发内部接收人", self.claim("send")["prompt"])
        self.claim("payment", code=1)

    def test_live_cli_uses_cloud_dashboard_approval_and_blocks_dependency(self):
        self.write_workflow([self.task("plan", impact="feature_design"), self.task("next", ["plan"], role="engineer")])
        runner = self.root / "runner"
        runner.write_text(
            "#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n"
            "job = json.load(sys.stdin)\n"
            "assert Path.cwd() == Path(job['workspace'])\n"
            "assert 'GIT_DIR' not in os.environ\n"
            "if job['phase'] == 'task' and job['role'] == 'engineer':\n"
            "    Path('delivery.txt').write_text('implementation')\n"
            "result = json.dumps({'needs_confirmation': False, 'reason': '完整'})"
            " if job['phase'] == 'approval' else '方案或实现'\n"
            "print(json.dumps({'type': 'result', 'message': result}))\n", encoding="utf-8",
        )
        runner.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        origin = "https://preview.example"
        process = subprocess.Popen([*CLI, str(self.workflow), "--live", "--runner", str(runner),
                                    "--dashboard-origin", origin,
                                    "--workspace", str(self.root), "--output-dir", str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   env={**OFFLINE_ENV, "GIT_DIR": str(ROOT / ".git")})
        try:
            self.assertEqual(process.stdout.readline().strip(), "Mode: live")
            self.run_dir = Path(process.stdout.readline().strip().split("Run output: ")[1])
            url = process.stdout.readline().strip().split("Dashboard: ")[1]
            key_header = process.stderr.readline().strip().split(": ", 1)[1]
            for _ in range(100):
                snapshot = self.state()
                if snapshot["tasks"]["plan"]["state"] == "awaiting_confirmation":
                    break
                time.sleep(.05)
            self.assertEqual(snapshot["tasks"]["plan"]["state"], "awaiting_confirmation")
            self.assertEqual(snapshot["tasks"]["next"]["state"], "pending")
            key = snapshot["tasks"]["plan"]["decision"]
            body = json.dumps({"action": "approve", "all": True, "reason": "确认设计"}).encode()
            headers = {"Content-Type": "application/json"}
            with self.assertRaises(HTTPError) as rejected:
                urlopen(Request(url + "human-action", data=body, headers=headers), timeout=3)
            self.assertEqual(rejected.exception.code, 403)
            with urlopen(Request(url + "dashboard-data.json"), timeout=3) as response:
                visible = json.load(response)
                self.assertEqual(visible["decisions"][key]["affected"], ["next"])
            with urlopen(url + "evidence/plan.md", timeout=3) as response:
                self.assertEqual(response.status, 200)
            headers.update({"Origin": origin, "X-Pioneers-Control": key_header})
            with urlopen(Request(url + "human-action", data=body, headers=headers), timeout=3) as response:
                self.assertEqual(json.load(response)["approved"], [key])
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(self.state()["tasks"]["next"]["state"], "completed")
            self.assertEqual({result["task"] for result in self.state()["summary"]["results"]}, {"plan", "next"})
            project = self.cli("--project-history", "--workspace", self.root, "--output-dir", self.root)
            self.assertEqual(project["latest_delivery"]["run"], self.run_dir.name)
            self.assertEqual((self.root / "delivery.txt").read_text(), "implementation")
            snapshot, source = self.remove_source_from_snapshot()
            resumed = subprocess.run([*CLI, "--continue", str(self.run_dir), "--no-open-browser"], env=OFFLINE_ENV,
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(resumed.returncode, 0, resumed.stdout + resumed.stderr)
            self.assertIn(source, json.loads(snapshot.read_text(encoding="utf-8")))
            status = self.state()
            status["workspace"] = str(ROOT)
            (self.run_dir / "status.json").write_text(json.dumps(status), encoding="utf-8")
            self.assertIn("separate from the Pioneers framework", self.cli("--continue", self.run_dir, code=1))
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
