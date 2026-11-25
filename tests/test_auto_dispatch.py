import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_policy import read_policy, update_policy
from pioneers import create_run, execute_host, host_ready, main, project_registry, update_host, validate_workflow
from progress import Progress


class AutomaticDispatchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = project_registry(self.project)
        self.catalogs = {"openrouter": [
            {"id": "org/flash", "kinds": ["chat"], "pricing": {"prompt": "0.01", "completion": "0.02"}},
            {"id": "org/deep/reasoner:free", "kinds": ["chat"], "context_length": 256000},
        ]}
        patches = (patch.dict(os.environ, OPENROUTER_API_KEY="router-test"),
                   patch("pioneers.dashboard_is_running", return_value=True),
                   patch("model_selection.load_catalogs", return_value=self.catalogs),
                   patch("pioneers.check_available"))
        _, _, self.lookup, self.check = [mock.start() for mock in patches]
        for mock in patches:
            self.addCleanup(mock.stop)

    def setup_run(self, **fields):
        first = {"id": "low", "role": "pm", "prompt": "Write the note", "tools": [],
                 "complexity": {"level": "low", "reason": "One edit"}, **fields}
        second = {**first, "id": "high", "complexity": {"level": "high", "reason": "Many constraints"}}
        plan = {"goal": "Document the service", "tasks": [first, second]}
        goal, tasks, order = validate_workflow(plan)
        self.run, progress = create_run(self.root / "runs", goal, tasks, order, "host")
        (self.run / "workflow.json").write_text(json.dumps(plan))
        progress.data["workspace"] = str(self.project)
        progress.save()

    def policy(self, action, **fields):
        return update_policy(self.registry, {"revision": read_policy(self.registry)["revision"],
                                             "action": action, **fields})

    def claim(self, task="low", **kwargs):
        return update_host(self.run, task, None, None, None, None, **kwargs)

    def test_ready_shares_catalog_and_cli_selects_from_ranked_candidates(self):
        self.setup_run(skills=["requirements-design"])
        ready = host_ready(self.run)
        self.lookup.assert_called_once()
        low, high = ready["ready"]
        self.assertEqual(low["complexity"]["level"], "low")
        self.assertEqual(low["model_candidates"][0]["model"], "org/flash")
        self.assertEqual(high["model_candidates"][0]["model"], "org/deep/reasoner:free")
        argv = ["pioneers.py", "--host-claim", str(self.run), "--task", "low",
                "--model-choice", "openrouter/org/deep/reasoner:free"]
        with patch("sys.argv", argv), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        chosen = json.loads(output.getvalue())["executor"]
        request = json.loads((self.run / ".model-low-1.json").read_text())
        self.assertIn("本任务的 Skill requirements-design", request["prompt"])
        self.assertEqual(Progress.open(self.run).data["tasks"]["low"]["skill_attempts"]["1"]["loaded"],
                         ["requirements-design"])
        self.assertEqual(chosen["model"], "org/deep/reasoner:free")
        self.assertIn("宿主", chosen["reason"])
        self.assertEqual(read_policy(self.registry)["revision"], 0)
        with self.assertRaisesRegex(ValueError, "候选"):
            self.claim("high", model_choice="openrouter/unknown")

    def test_auto_checks_past_visible_shortlist_and_shares_account_request(self):
        self.catalogs["openrouter"] = [{"id": f"org/{i:02}", "kinds": ["chat"]} for i in range(12)]
        self.setup_run()
        self.assertEqual(len(host_ready(self.run)["ready"][0]["model_candidates"]), 8)
        # Use the real provider preflight. Only the ninth public model is enabled on this account.
        from model_plugins import check_available
        self.check.side_effect = check_available
        allowed = {"data": [{"id": "org/08", "architecture": {
            "input_modalities": ["text"], "output_modalities": ["text"]}}]}
        with patch("model_plugins.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(json.dumps(allowed).encode())
            result = self.claim()
        self.assertEqual(result["executor"]["model"], "org/08")
        self.assertIn("预检失败", result["executor"]["reason"])
        self.assertEqual(opener.return_value.open.call_count, 1)

    def test_auto_falls_back_but_explicit_and_fixed_fail_before_claim(self):
        self.setup_run()
        self.check.side_effect = ValueError("account rejected model")
        fallback = self.claim()["executor"]
        self.assertEqual(fallback["provider"], "host")
        self.assertIn("预检全部失败", fallback["reason"])
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.claim("high", model_choice="openrouter/org/flash")
        self.policy("select", mode="text", provider="openrouter", model="org/flash")
        with self.assertRaisesRegex(ValueError, "rejected"):
            self.claim("high")
        self.assertEqual(Progress.open(self.run).data["tasks"]["high"]["state"], "pending")

    def test_hard_budget_applies_to_fixed_profile_and_again_before_execution(self):
        self.setup_run()
        self.policy("select", mode="text", provider="openrouter", model="org/flash")
        self.assertEqual(self.claim()["executor"]["model"], "org/flash")
        self.policy("preference", value="仅免费")
        with patch("pioneers.supervise_host_executor") as generate, redirect_stdout(io.StringIO()):
            self.assertEqual(execute_host(self.run, "low", 1, None), 1)
        generate.assert_not_called()
        with self.assertRaisesRegex(ValueError, "仅免费"):
            self.claim("high")
        self.policy("clear", mode="text")
        self.assertEqual(self.claim("high")["executor"]["model"], "org/deep/reasoner:free")

    def test_quality_preset_claim_fallback_and_policy_overrides(self):
        preferred = "deepseek-ai/DeepSeek-V4-Pro"
        self.catalogs["siliconflow"] = [{"id": model, "kinds": ["chat"]} for model in (preferred, "v/alternate")]
        self.setup_run()
        self.policy("preset", value="siliconflow-quality")
        with patch.dict(os.environ, SILICONFLOW_API_KEY="sf-test"):
            ready = host_ready(self.run)["ready"][0]
            self.assertEqual(ready["model_candidates"][0]["model"], preferred)
            self.check.side_effect = [ValueError("offline"), None]
            self.assertEqual(self.claim()["executor"]["model"], "v/alternate")
            self.policy("preference", value="仅免费")
            self.check.side_effect = None
            second = self.claim("high")["executor"]
            self.assertEqual((second["provider"], second["model"]), ("openrouter", "org/deep/reasoner:free"))

    def test_named_default_is_fixed_soft_named_preference_is_not(self):
        self.setup_run()
        self.policy("preset", value="siliconflow-quality")
        config = self.project / ".pioneers" / "models.json"
        config.parent.mkdir(exist_ok=True)
        profiles = {"quick": {"provider": "openrouter", "model": "org/flash", "tags": ["preferred"], "mode": "text"}}
        config.write_text(json.dumps({"profiles": profiles, "defaults": {"types": {"text": "quick"}}}))
        self.policy("preference", value="仅免费；选择 preferred")
        with self.assertRaisesRegex(ValueError, "仅免费"):
            self.claim()
        config.write_text(json.dumps({"profiles": profiles}))
        self.assertEqual(self.claim()["executor"]["model"], "org/deep/reasoner:free")

    def test_host_only_and_unsafe_patch_do_not_discover(self):
        self.setup_run(role="engineer", write_paths=["app.py"])
        self.policy("preset", value="siliconflow-quality")
        self.assertEqual(host_ready(self.run)["ready"][0]["model_candidates"], [])
        self.assertEqual(self.claim()["executor"]["provider"], "host")
        self.lookup.assert_not_called()
        self.check.assert_not_called()

    def test_independent_qa_is_host_only_despite_project_model_default(self):
        self.setup_run(role="qa", review=True, acceptance=["Snapshot matches"])
        self.policy("preset", value="siliconflow-quality")
        self.policy("select", mode="text", provider="openrouter", model="org/flash")
        self.assertEqual(host_ready(self.run)["ready"][0]["model_candidates"], [])
        self.assertEqual(self.claim()["executor"], {"provider": "host"})
        self.lookup.assert_not_called()
        self.check.assert_not_called()

    def test_checkpoint_stays_with_original_host(self):
        self.setup_run()
        self.claim(profile="host")
        event = self.root / "event.json"
        event.write_text(json.dumps({"type": "checkpoint", "summary": "Notes prepared", "session_id": "old"}))
        update_host(self.run, "low", "checkpoint", None, event, 1)
        update_host(self.run, "low", "lost", "worker gone", None, 1)
        self.assertEqual(self.claim()["executor"], {"provider": "host"})
        self.lookup.assert_not_called()

    def test_video_continues_prior_receipt_even_if_selection_changed(self):
        self.setup_run(video_prompt="A river")
        self.policy("select", provider="openrouter", model="org/video", mode="video")
        self.claim()
        (self.run / ".video-low-1.json").write_text(json.dumps({"state": "submitted", "request_id": "v1"}))
        update_host(self.run, "low", "lost", "worker gone", None, 1)
        self.policy("select", provider="openrouter", model="org/different", mode="video")
        self.policy("preference", value="仅免费")
        with self.assertRaisesRegex(ValueError, "回执"):
            self.claim(profile="host")
        self.check.reset_mock()
        result = self.claim()
        self.lookup.assert_not_called()
        self.check.assert_not_called()
        self.assertEqual(result["executor"]["model"], "org/video")
        self.assertTrue(result["executor"]["reuse_only"])
        request = json.loads((self.run / ".model-low-2.json").read_text())
        self.assertTrue(request["route"]["reuse_only"])
        self.assertEqual(request["video_receipt"], str(self.run / ".video-low-1.json"))


    def test_missing_receipt_with_old_record_never_starts_a_new_submission(self):
        self.setup_run(video_prompt="A river")
        self.policy("select", provider="openrouter", model="org/video", mode="video")
        self.claim()
        with Progress.locked(self.run) as progress:
            progress.data["tasks"]["low"]["executors"]["1"]["video"] = {"request_id": "old-job", "state": "submitted"}
        update_host(self.run, "low", "lost", "worker gone", None, 1)
        self.check.reset_mock()
        with self.assertRaisesRegex(ValueError, "回执文件缺失"):
            self.claim()
        self.check.assert_not_called()
        self.lookup.assert_not_called()
