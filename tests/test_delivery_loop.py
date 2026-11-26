import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import collaboration_flow, dashboard_snapshot, flow_with_approvals
from pioneers import control_run, create_run, load_workflow, plan_workflow, project_workspace, run_workflow


class DeliveryLoopTest(unittest.TestCase):
    def test_legacy_flow_reports_only_recoverable_plans(self):
        job = {"role": "qa", "state": "pending", "attempt": 2, "depends_on": []}
        data = {"tasks": {"check": job}, "events": [{"task": "check", "attempt": 1, "state": "interrupted"}]}
        self.assertTrue(all("skill_usage" not in n for n in collaboration_flow(data)["nodes"]))
        plan = {"check": {"skills": ["acceptance-testing"]}}
        self.assertEqual([n["skill_usage"] for n in collaboration_flow(data, plan)["nodes"]],
                         [{"planned": ["acceptance-testing"]}] * 2)
        data["workflow_revision"] = 2
        first, current = collaboration_flow(data, plan)["nodes"]
        self.assertNotIn("skill_usage", first)
        self.assertEqual(current["skill_usage"], {"planned": ["acceptance-testing"]})

    def test_workflow_diagram_places_approvals_on_dependency_path(self):
        flow = {"nodes": [{"key": key} for key in ("plan.1", "execute.1", "release")],
                "edges": [{"from": "plan.1", "to": "execute.1", "kind": "waiting"},
                          {"from": "execute.1", "to": "release", "kind": "handoff"}]}
        decisions = {
            key: {"id": key, "kind": kind, "task": task, "attempt": 1, "state": state, "reason": "范围已记录"}
            for key, kind, task, state in (
                ("D1", "result", "plan", "approved"), ("D2", "effect", "execute", "waiting"),
                ("D3", "result", "execute", "auto"), ("D4", "release", None, "waiting"),
            )
        }
        diagram = flow_with_approvals(flow, decisions)
        self.assertEqual([node["key"] for node in diagram["nodes"]],
                         ["plan.1", "approval.D1", "approval.D2", "execute.1", "approval.D4", "release"])
        self.assertEqual({(edge["from"], edge["to"]) for edge in diagram["edges"]},
                         {("plan.1", "approval.D1"), ("approval.D1", "approval.D2"),
                          ("approval.D2", "execute.1"), ("execute.1", "approval.D4"), ("approval.D4", "release")})
        self.assertEqual(diagram["nodes"][2]["state"], "awaiting_confirmation")
        self.assertEqual(flow_with_approvals(flow, {}), flow)

    def invoke(self, *args, expected=0):
        result = subprocess.run([sys.executable, "-B", "pioneers.py", *map(str, args)],
                                cwd=ROOT, capture_output=True, text=True, timeout=10,
                                env={**os.environ, "PIONEERS_SKILLS_OFFLINE": "1"})
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def test_host_repair_and_release(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = ROOT / "examples/todo.json"
            process = subprocess.Popen([sys.executable, "-B", "pioneers.py", "--host-start", str(workflow),
                                        "--output-dir", temp, "--workspace", temp, "--require-release",
                                        "--no-open-browser"], cwd=ROOT, stdout=subprocess.PIPE, text=True,
                                       env={**os.environ, "PIONEERS_SKILLS_OFFLINE": "1"})
            self.addCleanup(process.communicate, timeout=3)
            self.addCleanup(process.terminate)
            run = Path(json.loads(process.stdout.readline())["run_dir"])
            artifact = root / "result"

            def claim(task):
                return json.loads(self.invoke("--host-claim", run, "--task", task))

            def complete(task, body, *args):
                artifact.write_text(body, encoding="utf-8")
                result = json.loads(self.invoke("--host-report", run, "--task", task, "--state", "completed",
                                               "--activity", "产物已提交", "--artifact", artifact, *args))
                assignment = result["judgment"]
                artifact.write_text('{"needs_confirmation": false, "reason": "验收证据完整"}', encoding="utf-8")
                return json.loads(self.invoke("--judge", run, "--decision", assignment["decision"],
                                              "--task", task, "--attempt", assignment["attempt"], "--artifact", artifact))

            def approve_release():
                data = json.loads((run / "status.json").read_text(encoding="utf-8"))
                self.invoke("--approve", run, "--decision", data["release"]["decision"], "--reason", "确认私有发布")

            def review(outcome, status):
                criteria = json.loads(workflow.read_text(encoding="utf-8"))["tasks"][-1]["acceptance"]
                return json.dumps({"outcome": outcome, "summary": "刷新后状态校验",
                                   "checks": [{"criterion": criterion, "status": status, "evidence": "测试记录"}
                                              for criterion in criteria]})

            self.invoke("--release", run, "--state", "publishing", "--activity", "过早发布", expected=1)
            assignment = claim("requirements")
            self.assertEqual(assignment["workspace"], str(root.resolve()))
            self.assertIn(str(root.resolve()), assignment["prompt"])
            complete("requirements", "需求")
            claim("implementation")
            complete("implementation", "第一轮实现")
            claim("verification")
            result = complete("verification", review("changes_requested", "fail"))
            self.assertEqual(result["task_state"], "pending")
            data, _ = dashboard_snapshot(run / "status.json")
            flow = collaboration_flow(data)
            self.assertIn({"from": "verification.1", "to": "implementation.2", "kind": "return"}, flow["edges"])
            self.assertIn({"from": "implementation.2", "to": "verification.2", "kind": "waiting"}, flow["edges"])
            self.invoke("--host-claim", run, "--task", "verification", expected=1)
            repair = claim("implementation")
            self.assertEqual(repair["attempt"], 2)
            self.assertIn("刷新后状态校验", repair["prompt"])
            self.invoke("--host-report", run, "--task", "implementation", "--attempt", 1,
                        "--state", "running", "--activity", "过期报告", expected=1)
            complete("implementation", "第二轮实现", "--attempt", 2)
            self.assertIn("第二轮实现", claim("verification")["prompt"])
            verified = complete("verification", review("accepted", "pass"), "--attempt", 2)
            self.assertEqual(verified["run_state"], "accepted")
            waiting = json.loads(self.invoke("--host-ready", run, "--wait", 1))
            self.assertTrue(waiting["timed_out"])
            self.assertFalse(waiting["release_ready"])
            self.invoke("--release", run, "--state", "publishing", "--activity", "未授权发布", expected=1)
            approve_release()
            ready = json.loads(self.invoke("--host-ready", run, "--wait", 3))
            self.assertTrue(ready["release_ready"])
            self.assertNotIn("timed_out", ready)
            self.invoke("--release", run, "--state", "publishing", "--activity", "开始发布")
            self.invoke("--release", run, "--state", "publish_failed", "--activity", "测试故障")
            self.invoke("--release", run, "--state", "publishing", "--activity", "未授权重试", expected=1)
            approve_release()
            self.invoke("--release", run, "--state", "publishing", "--activity", "重试发布")
            self.invoke("--release", run, "--state", "published", "--activity", "已发布",
                        "--url", "javascript:alert(1)", expected=1)
            self.invoke("--release", run, "--state", "published", "--activity", "已发布",
                        "--url", "https://example.com/")
            data, evidence = dashboard_snapshot(run / "status.json")
            self.assertEqual(data["state"], "published")
            self.assertEqual(data["release"]["url"], "https://example.com/")
            self.assertEqual(evidence, {"requirements.md", "implementation.md", "implementation.2.md",
                                        "verification.json", "verification.2.json"})
            flow = collaboration_flow(data)
            for event in data["events"]:
                event.pop("handoff", None)
            self.assertEqual(flow["edges"], collaboration_flow(data)["edges"])
            self.assertEqual([stage["key"] for stage in flow["nodes"]],
                             ["requirements.1", "implementation.1", "verification.1",
                              "implementation.2", "verification.2", "release"])
            self.assertIn({"from": "verification.2", "to": "release", "kind": "handoff"}, flow["edges"])

    def test_host_rejects_framework_paths_and_legacy_framework_run(self):
        workflow = ROOT / "examples/todo.json"
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            output = root / "runs"
            alias = root / "alias"
            alias.symlink_to(ROOT, target_is_directory=True)
            for workspace in ([], [ROOT], [ROOT / "examples"], [ROOT.parent], [alias]):
                self.invoke("--host-start", workflow, "--output-dir", output,
                            *(["--workspace", workspace[0]] if workspace else []), expected=1)
            self.assertFalse(output.exists())
            run = json.loads(self.invoke("--host-start", workflow, "--output-dir", output, "--workspace", root,
                                         "--register-only"))
            self.assertEqual(run["workspace"], str(root))
            status = Path(run["run_dir"]) / "status.json"
            self.invoke("--host-claim", run["run_dir"], "--task", "requirements", expected=1)
            data = json.loads(status.read_text())
            data["workspace"] = str(ROOT)
            status.write_text(json.dumps(data), encoding="utf-8")
            self.invoke("--host-claim", run["run_dir"], "--task", "requirements", expected=1)
            self.assertEqual(json.loads(status.read_text())["tasks"]["requirements"]["state"], "pending")

    def test_workspace_accepts_project_worktree_and_rejects_framework_worktree(self):
        def worktree(repo, destination):
            repo.mkdir()
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "commit", "-q", "--allow-empty", "-m", "initial"], check=True)
            subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "--detach", str(destination)], check=True)

        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            framework = root / "framework"
            framework_worktree = root / "framework-worktree"
            project_worktree = root / "project-worktree"
            worktree(framework, framework_worktree)
            worktree(root / "project", project_worktree)
            with patch("pioneers.ROOT", framework):
                with self.assertRaisesRegex(ValueError, "Pioneers Git repository"):
                    project_workspace(framework_worktree)
                self.assertEqual(project_workspace(project_worktree), project_worktree)

    def test_goal_planning_runs_low_risk_work_as_two_tasks(self):
        draft = json.loads((ROOT / "examples/small-fix.json").read_text(encoding="utf-8"))
        phases = []
        case = self

        class Runner:
            def run(self, role, prompt, report, *, phase, **kwargs):
                phases.append((role, phase))
                if phase == "plan":
                    case.assertIn("默认只安排一项 engineer 和一项独立 QA", prompt)
                    case.assertIn("用 architecture 或 feature_design 标记方案并让实现依赖它", prompt)
                    return json.dumps(draft)
                if phase == "approval":
                    return '{"needs_confirmation": false, "reason": "低风险且证据完整"}'
                if phase == "review":
                    case.assertIn("交付了定向测试和提交证据", prompt)
                    return json.dumps({
                        "outcome": "accepted", "summary": "空白与正常输入均通过",
                        "checks": [{"criterion": item, "status": "pass", "evidence": "定向复验"}
                                   for item in draft["tasks"][1]["acceptance"]],
                    })
                return "交付了定向测试和提交证据"

        with TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            root = Path(temp)
            runner = Runner()
            runner.workspace = root / "project"
            runner.workspace.mkdir()
            goal = draft["goal"]
            run_dir, progress = create_run(root / "runs", goal, {}, (), "live")
            planned_goal, tasks, order = plan_workflow(goal, runner, run_dir, progress)
            self.assertEqual(order, ("fix-title", "verify-title"))
            self.assertEqual(run_workflow(planned_goal, tasks, order, runner, progress), 0)
            self.assertEqual([(role, phase) for role, phase in phases if phase in ("task", "review")],
                             [("engineer", "task"), ("qa", "review")])
            snapshot = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(snapshot["state"], "completed")
            self.assertEqual({d["state"] for d in snapshot["decisions"].values()}, {"auto"})

    def test_planner_returns_misregistered_video_to_ld(self):
        with TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            root = Path(temp)
            goal = "制作团建宣传短片"
            task = {"id": "clip", "role": "pm", "prompt": goal, "acceptance": ["交付成片"],
                    "complexity": {"level": "low", "reason": "短文生视频"}, "tools": []}
            runner = Mock(workspace=root)
            drafts = [{"tasks": [task]}, {"tasks": [{**task, "video_prompt": "公园里的飞盘活动"}]}]
            runner.run.side_effect = [json.dumps(d) for d in drafts]
            run_dir, progress = create_run(root / "runs", goal, {}, (), "live")
            _, tasks, _ = plan_workflow(goal, runner, run_dir, progress)
            self.assertEqual(tasks["clip"]["video_prompt"], "公园里的飞盘活动")
            self.assertEqual([call.args[0] for call in runner.run.call_args_list], ["ld", "ld"])
            self.assertIn("video_prompt", runner.run.call_args_list[1].args[1])
            self.assertNotIn("executors", progress.data["tasks"]["clip"])

    def test_live_executor_applies_operator_replan_before_completion(self):
        goal, tasks, _ = load_workflow(ROOT / "examples/todo.json")
        note = "同步进行安卓和 ios 的开发"
        planned = [
            {**tasks["implementation"], "id": "android", "depends_on": ["requirements"]},
            {**tasks["implementation"], "id": "ios", "depends_on": ["requirements"]},
            {**tasks["verification"], "depends_on": ["android", "ios"]},
        ]
        initial = {"requirements": tasks["requirements"], **{task["id"]: task for task in planned}}
        initial["ios"] = {**initial["ios"], "depends_on": ["android"]}
        phases = []
        case = self

        class Runner:
            def run(self, role, prompt, report, *, phase, **kwargs):
                phases.append((role, phase))
                if phase == "approval":
                    return '{"needs_confirmation": false, "reason": "证据完整"}'
                if phase == "plan":
                    case.assertIn(note, prompt)
                    case.assertIn("默认只安排一项 engineer 和一项独立 QA", prompt)
                    return json.dumps({"goal": goal, "tasks": planned})
                if role == "pm":
                    control_run(run_dir, {"action": "pause", "reason": "调整任务"})
                    control_run(run_dir, {"action": "intervene", "reason": note})
                if phase == "review":
                    return json.dumps({
                        "outcome": "accepted", "summary": "双端验证",
                        "checks": [{"criterion": item, "status": "pass", "evidence": "测试"}
                                   for item in tasks["verification"]["acceptance"]],
                    })
                return "交付结果"

        with TemporaryDirectory() as temp, redirect_stdout(StringIO()):
            order = tuple(initial)
            run_dir, progress = create_run(Path(temp), goal, initial, order, "live")
            (run_dir / "workflow.json").write_text(json.dumps({"goal": goal, "tasks": list(initial.values())}))
            self.assertEqual(run_workflow(goal, initial, order, Runner(), progress), 0)
            self.assertIn(("ld", "plan"), phases)
            self.assertEqual(phases.count(("engineer", "task")), 2)
            snapshot = json.loads((run_dir / "status.json").read_text())
            self.assertEqual(snapshot["state"], "completed")
            self.assertEqual(snapshot["tasks"]["ios"]["depends_on"], ["requirements"])
            for task in planned:
                self.assertEqual(snapshot["tasks"][task["id"]]["skill_attempts"]["1"]["loaded"], task["skills"])
            self.assertTrue(all(task["state"] == "completed" for task in snapshot["tasks"].values()))

    def test_live_executor_retries_before_completion(self):
        goal, tasks, order = load_workflow(ROOT / "examples/todo.json")
        self_case = self

        class Runner:
            reviews = 0
            builds = 0

            def run(self, role, prompt, report, **kwargs):
                if kwargs["phase"] == "approval":
                    return '{"needs_confirmation": false, "reason": "验收证据完整"}'
                if role == "engineer":
                    self.builds += 1
                    if self.builds == 2:
                        self_case.assertIn("刷新回归", prompt)
                    return f"实现第 {self.builds} 轮"
                if role != "qa":
                    return "需求"
                self.reviews += 1
                first = self.reviews == 1
                if not first:
                    self_case.assertIn("实现第 2 轮", prompt)
                return json.dumps({
                    "outcome": "changes_requested" if first else "accepted",
                    "summary": "刷新回归",
                    "checks": [{"criterion": criterion, "status": "fail" if first else "pass", "evidence": "测试"}
                               for criterion in tasks["verification"]["acceptance"]],
                })

        with TemporaryDirectory() as root, redirect_stdout(StringIO()):
            _, progress = create_run(Path(root), goal, tasks, order, "live")
            runner = Runner()
            self.assertEqual(run_workflow(goal, tasks, order, runner, progress), 0)
            self.assertEqual((runner.builds, runner.reviews), (2, 2))
            self.assertEqual(progress.data["state"], "completed")


if __name__ == "__main__":
    unittest.main()
