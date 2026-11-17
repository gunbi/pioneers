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

    def host(self, tasks):
        self.write_workflow(tasks)
        self.run_dir = Path(self.cli("--host-start", self.workflow, "--workspace", self.root,
                                     "--output-dir", self.root)["run_dir"])

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

    def test_host_start_can_serve_dashboard_before_first_claim(self):
        self.write_workflow([self.task("plan")])
        process = subprocess.Popen([*CLI, "--host-start", str(self.workflow), "--workspace", str(self.root),
                                    "--output-dir", str(self.root), "--serve-dashboard"],
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
                self.assertEqual(json.load(response)["tasks"]["plan"]["state"], "pending")
            self.claim("plan")
            with urlopen(url + "dashboard-data.json", timeout=3) as response:
                self.assertEqual(json.load(response)["tasks"]["plan"]["state"], "running")
        finally:
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=3)

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

    def test_live_cli_uses_dashboard_approval_and_blocks_dependency(self):
        self.write_workflow([self.task("plan", impact="feature_design"), self.task("next", ["plan"])])
        runner = self.root / "runner"
        runner.write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            "job = json.load(sys.stdin)\n"
            "result = json.dumps({'needs_confirmation': False, 'reason': '完整'})"
            " if job['phase'] == 'approval' else '方案或实现'\n"
            "print(json.dumps({'type': 'result', 'message': result}))\n", encoding="utf-8",
        )
        runner.chmod(0o755)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        process = subprocess.Popen([*CLI, str(self.workflow), "--live", "--runner", str(runner),
                                    "--workspace", str(self.root), "--output-dir", str(self.root)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=OFFLINE_ENV)
        try:
            self.assertEqual(process.stdout.readline().strip(), "Mode: live")
            self.run_dir = Path(process.stdout.readline().strip().split("Run output: ")[1])
            url = process.stdout.readline().strip().split("Dashboard: ")[1]
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
            headers["Origin"] = url.rstrip("/")
            with urlopen(Request(url + "human-action", data=body, headers=headers), timeout=3) as response:
                self.assertEqual(json.load(response)["approved"], [key])
            stdout, stderr = process.communicate(timeout=10)
            self.assertEqual(process.returncode, 0, stdout + stderr)
            self.assertEqual(self.state()["tasks"]["next"]["state"], "completed")
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=3)
