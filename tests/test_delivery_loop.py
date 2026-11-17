import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dashboard import collaboration_flow, dashboard_snapshot, flow_with_approvals
from pioneers import create_run, load_workflow, run_workflow


class DeliveryLoopTest(unittest.TestCase):
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
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result.stdout

    def test_host_repair_and_release(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            workflow = ROOT / "examples/todo.json"
            run = Path(json.loads(self.invoke("--host-start", workflow, "--output-dir", root,
                                              "--require-release"))["run_dir"])
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
            claim("requirements")
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
            self.invoke("--release", run, "--state", "publishing", "--activity", "未授权发布", expected=1)
            approve_release()
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
