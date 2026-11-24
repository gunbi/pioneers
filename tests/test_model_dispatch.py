import json
import os
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import model_worker
from dashboard import dashboard_snapshot
from model_plugins import ProviderError
from pioneers import create_run, execute_host, handle_judgment, update_host, validate_workflow
from progress import Progress


class ModelDispatchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name).resolve()
        self.project = root / "project"
        config = self.project / ".pioneers"
        config.mkdir(parents=True)
        (config / "models.json").write_text(json.dumps({
            "profiles": {"writer": {"provider": "siliconflow", "model": "example/text", "mode": "text"}},
            "defaults": {"roles": {"pm": "writer"}},
        }))
        plan = {"goal": "Normalize labels", "tasks": [
            {"id": "spec", "role": "pm", "prompt": "Specify the behavior", "tools": [],
             "complexity": {"level": "low", "reason": "One rule"}}
        ]}
        goal, tasks, order = validate_workflow(plan)
        self.run, progress = create_run(root / "runs", goal, tasks, order, "host")
        (self.run / "workflow.json").write_text(json.dumps(plan))
        progress.data["workspace"] = str(self.project)
        progress.save()
        for mock in (patch.dict(os.environ, SILICONFLOW_API_KEY="local-test-only"),
                     patch("pioneers.dashboard_is_running", return_value=True)):
            mock.start()
            self.addCleanup(mock.stop)

    def claim(self, profile=None):
        return update_host(self.run, "spec", None, None, None, None, profile=profile)

    def invoke_worker(self, command, workspace, renew, *, deadline_seconds):
        self.assertGreater(deadline_seconds, 0)
        renew()
        request, output = map(Path, command[-2:])
        return model_worker.run(json.loads(request.read_text()), Path(workspace), output)

    def test_preflight_failure_does_not_claim(self):
        with patch("pioneers.check_available", side_effect=ProviderError("catalog unavailable")):
            with self.assertRaisesRegex(ProviderError, "catalog unavailable"):
                self.claim()
        self.assertEqual(Progress.open(self.run).data["tasks"]["spec"]["state"], "pending")
        self.assertEqual(self.claim("host")["executor"], {"provider": "host"})

    @patch("pioneers.check_available")
    def test_plugin_handoff_and_dashboard(self, catalog):
        assignment = self.claim()
        catalog.assert_called_once()
        self.assertEqual(assignment["executor"]["model"], "example/text")
        with redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker), patch(
                "model_worker.complete", return_value=("Examples delivered", {"prompt_tokens": 7})):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 0)
        self.assertEqual((self.run / "spec.md").read_text().strip(), "Examples delivered")
        data, artifacts = dashboard_snapshot(self.run / "status.json")
        job = data["tasks"]["spec"]
        self.assertEqual(job["state"], "judging")
        self.assertEqual(job["executors"]["1"]["usage"], {"prompt_tokens": 7})
        self.assertNotIn("local-test-only", json.dumps(data))
        self.assertNotIn(".model-spec-1.json", artifacts)
        verdict = self.run / "verdict.json"
        verdict.write_text('{"needs_confirmation":false,"reason":"Verified examples"}')
        self.assertEqual(handle_judgment(self.run, job["decision"], "spec", 1, verdict)["run_state"], "completed")

    @patch("pioneers.check_available")
    def test_timeout_allows_host_retry_and_fences_old_attempt(self, _catalog):
        self.claim()
        with redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker), patch(
                "model_worker.complete", side_effect=ProviderError("SiliconFlow timed out")):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
        retry = self.claim("host")
        self.assertEqual(retry["attempt"], 2)
        with self.assertRaisesRegex(ValueError, "active --attempt 2"):
            execute_host(self.run, "spec", 1, None)
        data, _ = dashboard_snapshot(self.run / "status.json")
        history = data["tasks"]["spec"]["executors"]
        self.assertEqual(history["1"]["error"], "SiliconFlow timed out")
        self.assertEqual(history["2"]["provider"], "host")
