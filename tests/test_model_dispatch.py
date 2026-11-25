import json
import os
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import model_worker
from dashboard import dashboard_snapshot
from model_plugins import InsufficientCreditError, OpenRouter, ProviderError
from model_policy import read_policy, update_policy
from pioneers import create_run, execute_host, handle_judgment, host_ready, project_registry, update_host, validate_workflow
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

    def invoke_worker(self, command, workspace, renew, *, deadline_seconds, env):
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

    def test_openrouter_project_selection_uses_its_own_key_and_persists_returned_model(self):
        registry = project_registry(self.project)
        update_policy(registry, {"action": "select", "revision": 0, "mode": "text",
                                 "provider": "openrouter", "model": "openrouter/free"})
        account = {"id": "openrouter/free", "architecture": {"input_modalities": ["text"],
                                                            "output_modalities": ["text"]}}
        response = {"model": "other/model", "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}}

        def worker(command, workspace, renew, *, deadline_seconds, env):
            self.assertNotIn("SILICONFLOW_API_KEY", env)
            self.assertEqual(env["OPENROUTER_API_KEY"], "router-test")
            return self.invoke_worker(command, workspace, renew, deadline_seconds=deadline_seconds, env=env)

        with patch.dict(os.environ, OPENROUTER_API_KEY="router-test"), patch.object(
                OpenRouter, "request", side_effect=[{"data": [account]}, response]), patch(
                "pioneers.supervise_host_executor", worker), redirect_stdout(StringIO()):
            assignment = self.claim()
            self.assertEqual(assignment["executor"]["provider"], "openrouter")
            self.assertEqual(execute_host(self.run, "spec", 1, None), 0)
        data, _ = dashboard_snapshot(self.run / "status.json")
        receipt = data["tasks"]["spec"]["executors"]["1"]
        self.assertEqual(receipt["actual_model"], "other/model")
        self.assertEqual(receipt["model"], "openrouter/free")
        self.assertEqual(receipt["usage"]["total_tokens"], 4)

    @patch("pioneers.check_available")
    @patch.dict(os.environ, OPENROUTER_API_KEY="router-test")
    def test_credit_rejection_requeues_and_completes_on_second_provider(self, _preflight):
        registry = project_registry(self.project)
        update_policy(registry, {"action": "select", "revision": 0, "mode": "text",
                                 "provider": "openrouter", "model": "org/pinned"})
        catalog = {"siliconflow": [{"id": "org/alternative", "kinds": ["chat"]}]}
        exhausted = HTTPError("https://openrouter.ai/api/v1/chat/completions", 402, "Payment Required", {},
                              BytesIO(b'{"error":{"code":402,"message":"Insufficient credits"}}'))
        delivered = BytesIO(b'{"choices":[{"finish_reason":"stop","message":{"content":"Delivery on second provider"}}]}')
        with (patch("model_selection.load_catalogs", return_value=catalog),
              patch("pioneers.supervise_host_executor", self.invoke_worker), redirect_stdout(StringIO()),
              patch("model_plugins.build_opener") as opener):
            send = opener.return_value.open
            send.side_effect = [exhausted, delivered]
            self.assertEqual(self.claim()["executor"]["provider"], "openrouter")
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
            ready = host_ready(self.run)
            self.assertEqual(ready["ready"][0]["attempt"], 2)
            self.assertEqual(ready["model_policy"]["provider_ban_reasons"], {"openrouter": "insufficient_credit"})
            self.assertEqual(self.claim()["executor"]["provider"], "siliconflow")
            self.assertEqual(execute_host(self.run, "spec", 2, None), 0)
            self.assertEqual([call.args[0].host for call in send.call_args_list],
                             ["openrouter.ai", "api.siliconflow.cn"])
        job = Progress.open(self.run).data["tasks"]["spec"]
        self.assertEqual(job["state"], "judging")
        self.assertEqual(job["executors"]["1"]["error_code"], "insufficient_credit")
        self.assertEqual((self.run / "spec.2.md").read_text().strip(), "Delivery on second provider")
        policy = update_policy(registry, {"action": "unban_provider", "revision": 2, "provider": "openrouter"})
        self.assertEqual(policy["provider_ban_reasons"], {})
        self.assertEqual(policy["selected"]["text"]["model"], "org/pinned")

    @patch("pioneers.check_available")
    def test_credit_failure_with_checkpoint_requires_reconciliation(self, _preflight):
        self.claim()
        event = self.run / "checkpoint.json"
        event.write_text(json.dumps({"type": "checkpoint", "summary": "original work"}))
        update_host(self.run, "spec", "checkpoint", None, event, 1)
        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker),
              patch("model_worker.complete", side_effect=InsufficientCreditError("account empty"))):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
        job = Progress.open(self.run).data["tasks"]["spec"]
        self.assertEqual(job["state"], "recovery_required")
        self.assertEqual(job["attempt"], 1)
        self.assertEqual(read_policy(project_registry(self.project))["banned_providers"], ["siliconflow"])

    def claim_video(self):
        workflow = self.run / "workflow.json"
        plan = json.loads(workflow.read_text())
        plan["tasks"][0]["video_prompt"] = "A sailboat"
        workflow.write_text(json.dumps(plan))
        update_policy(project_registry(self.project), {"action": "select", "revision": 0, "mode": "video",
                                                       "provider": "siliconflow", "model": "example/video"})
        self.claim()

    @patch("pioneers.check_available")
    def test_initial_rejected_video_is_safe_to_assign_to_host(self, _preflight):
        self.claim_video()
        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker),
              patch("siliconflow_media.Video.submit", side_effect=InsufficientCreditError("account empty")),
              patch("model_selection.load_catalogs", return_value={})):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
            self.assertFalse((self.run / ".video-spec-1.json").exists())
            replacement = self.claim()
        self.assertEqual(replacement["attempt"], 2)
        self.assertEqual(replacement["executor"]["provider"], "host")

    @patch("pioneers.check_available")
    def test_credit_failure_after_video_submission_preserves_original_job(self, _preflight):
        self.claim_video()
        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker),
              patch("siliconflow_media.Video.submit", return_value="known-job") as submit,
              patch("siliconflow_media.Video.poll", side_effect=InsufficientCreditError("account empty"))):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
            submit.assert_called_once()
        self.assertEqual(Progress.open(self.run).data["tasks"]["spec"]["state"], "recovery_required")
        self.assertEqual(json.loads((self.run / ".video-spec-1.json").read_text())["request_id"], "known-job")
        with self.assertRaises(ValueError):
            self.claim("host")

    @patch("pioneers.check_available")
    def test_timeout_allows_host_retry_and_fences_old_attempt(self, _catalog):
        self.claim()
        with redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker), patch(
                "model_worker.complete", side_effect=ProviderError("SiliconFlow timed out")):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
        self.assertEqual(read_policy(project_registry(self.project))["banned_providers"], [])
        retry = self.claim("host")
        self.assertEqual(retry["attempt"], 2)
        with self.assertRaisesRegex(ValueError, "active --attempt 2"):
            execute_host(self.run, "spec", 1, None)
        data, _ = dashboard_snapshot(self.run / "status.json")
        history = data["tasks"]["spec"]["executors"]
        self.assertEqual(history["1"]["error"], "SiliconFlow timed out")
        self.assertEqual(history["2"]["provider"], "host")

    @patch("pioneers.check_available")
    def test_ban_after_claim_blocks_started_call(self, _catalog):
        self.claim()
        registry = project_registry(self.project)
        update_policy(registry, {"action": "ban", "revision": 0, "provider": "siliconflow", "model": "example/text"})
        with redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor") as supervise:
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
        supervise.assert_not_called()
        pending = host_ready(self.run)
        self.assertEqual(pending["model_policy"]["banned"][0]["model"], "example/text")
        self.assertEqual(pending["ready"][0]["attempt"], 2)
        with self.assertRaisesRegex(ValueError, "禁用"):
            self.claim("writer")

    @patch("pioneers.check_available")
    def test_video_receipt_shared_only_with_recovery(self, _catalog):
        workflow = self.run / "workflow.json"
        plan = json.loads(workflow.read_text())
        plan["tasks"][0]["video_prompt"] = "A red sailboat on a lake"
        workflow.write_text(json.dumps(plan))
        update_policy(project_registry(self.project), {"action": "select", "revision": 0, "mode": "video",
                      "provider": "siliconflow", "model": "example/video"})
        self.assertEqual(self.claim()["executor"]["mode"], "video")
        first = json.loads((self.run / ".model-spec-1.json").read_text())
        self.assertEqual(first["video_prompt"], "A red sailboat on a lake")
        update_host(self.run, "spec", "lost", "executor exited", None, 1)
        self.assertEqual(self.claim()["executor"]["video_generation"], 1)
        second = json.loads((self.run / ".model-spec-2.json").read_text())
        self.assertEqual(first["video_receipt"], second["video_receipt"])
