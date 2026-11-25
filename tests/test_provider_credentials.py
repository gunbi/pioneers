from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from model_board import ModelBoard
from model_plugins import OpenRouter, SiliconFlow
from model_selection import load_catalogs
from pioneers import create_run, execute_host, project_registry, update_host, validate_workflow
from provider_credentials import configure_provider_env, project_credentials


class ProviderCredentialsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.home = self.root / "user"
        self.home.mkdir()
        self.project = self.root / "project"
        self.project.mkdir()
        self.registry = project_registry(self.project)
        self.envfile = self.home / "account.env"
        env = patch.dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home),
                         SILICONFLOW_API_KEY="", OPENROUTER_API_KEY="")
        env.start()
        self.addCleanup(env.stop)
        self.cli = str(Path(__file__).resolve().parents[1] / "pioneers.py")

    def invoke(self, *args):
        return subprocess.run([sys.executable, "-B", self.cli, *map(str, args)], text=True, capture_output=True)

    def test_binding_not_in_project_applies_to_fresh_process_and_rotates(self):
        self.envfile.write_text("export SILICONFLOW_API_KEY='sf:first' # sample\n"
                                'OPENROUTER_API_KEY="router:first"\nOTHER=$(touch ignored)\n')
        bound = self.invoke("--configure-provider-env", "～/account.env", "--workspace", self.project)
        self.assertEqual(bound.returncode, 0, bound.stderr)
        self.assertEqual(set(json.loads(bound.stdout)["variables"]), {SiliconFlow.key_env, OpenRouter.key_env})
        self.assertNotIn(str(self.envfile), bound.stdout)
        self.assertNotIn("sf:first", bound.stdout + bound.stderr)
        self.assertEqual(list(self.project.iterdir()), [])
        binding, = list(self.home.rglob("*.json"))
        self.assertEqual(json.loads(binding.read_text()), str(self.envfile))
        if os.name == "posix":
            self.assertEqual(binding.stat().st_mode & 0o777, 0o600)
            self.assertEqual(binding.parent.stat().st_mode & 0o777, 0o700)

        with patch.dict(os.environ, SILICONFLOW_API_KEY="inherited", OPENROUTER_API_KEY="outer-router"):
            with project_credentials(self.registry):
                self.assertEqual(SiliconFlow.api_key(), "sf:first")
                self.assertEqual(OpenRouter.api_key(), "router:first")
                with project_credentials(self.root / "other-registry"):
                    self.assertEqual(SiliconFlow.api_key(), "inherited")
                self.assertEqual(SiliconFlow.api_key(), "sf:first")
            self.envfile.write_text("SILICONFLOW_API_KEY=sf:rotated\n")
            with project_credentials(self.registry):
                self.assertEqual(SiliconFlow.api_key(), "sf:rotated")
                with self.assertRaises(ValueError):
                    OpenRouter.api_key()
            self.envfile.unlink()
            with project_credentials(self.registry), self.assertRaisesRegex(ValueError, "configured provider env"):
                SiliconFlow.api_key()
            self.assertEqual(self.invoke("--clear-provider-env", "--workspace", self.project).returncode, 0)
            with project_credentials(self.registry):
                self.assertEqual(SiliconFlow.api_key(), "inherited")

    def test_bad_replacement_preserves_binding_and_reports_no_secrets(self):
        self.envfile.write_text("SILICONFLOW_API_KEY=working\n")
        configure_provider_env(self.registry, self.envfile)
        bad = self.home / "bad.env"
        bad.write_text("SILICONFLOW_API_KEY=$(do-not-print-me)\n")
        result = self.invoke("--configure-provider-env", bad, "--workspace", self.project)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("do-not-print-me", result.stdout + result.stderr)
        with project_credentials(self.registry):
            self.assertEqual(SiliconFlow.api_key(), "working")

    def test_running_board_observes_binding_and_rebind_including_background_requests(self):
        self.registry.mkdir(parents=True)
        board = ModelBoard(self.registry, "example", self.project)
        with patch.object(OpenRouter, "public_catalog", False):
            self.assertFalse(board.providers()[0]["connected"])
            with self.assertRaises(ValueError):
                board.apply({"action": "refresh", "provider": "siliconflow"})
            # Use the provider's full production fan-out. Every request must carry its own project key.
            def respond(request, timeout):
                model = {"Bearer key-one": "test/one", "Bearer key-two": "test/two"}[request.get_header("Authorization")]
                return io.BytesIO(json.dumps({"data": [{"id": model}]}).encode())
            with patch("model_plugins.build_opener") as opener:
                opener.return_value.open.side_effect = respond
                for key, model in [("key-one", "test/one"), ("key-two", "test/two")]:
                    self.envfile.write_text("SILICONFLOW_API_KEY=" + key + "\n")
                    self.assertEqual(self.invoke("--configure-provider-env", self.envfile, "--workspace", self.project).returncode, 0)
                    for _ in range(100):
                        status = board.providers()[0]
                        if status["models"]:
                            break
                        time.sleep(0.01)
                    self.assertTrue(status["connected"])
                    self.assertIsNone(status["error"])
                    self.assertEqual(status["models"][0]["id"], model)
                    self.assertIn("chat", status["models"][0]["kinds"])
                    self.assertNotIn(key, json.dumps(status))
                    self.assertNotIn(str(self.envfile), json.dumps(status))
                    self.assertEqual(load_catalogs(self.registry, cached_only=True)["siliconflow"], status["models"])

    def test_ready_claim_preflight_and_worker_use_project_account(self):
        self.envfile.write_text("SILICONFLOW_API_KEY=sf-bound\nOPENROUTER_API_KEY=router-bound\n")
        configure_provider_env(self.registry, self.envfile)
        plan = {"goal": "Describe the service", "tasks": [
            {"id": "spec", "role": "pm", "prompt": "Write a summary", "tools": [],
             "complexity": {"level": "low", "reason": "One page"}}]}
        goal, tasks, order = validate_workflow(plan)
        run, progress = create_run(self.root / "runs", goal, tasks, order, "host")
        (run / "workflow.json").write_text(json.dumps(plan))
        progress.data["workspace"] = str(self.project)
        progress.save()
        def catalog():
            self.assertEqual(SiliconFlow.api_key(), "sf-bound")
            return [{"id": "example/text", "kinds": ["chat"]}]
        with patch.object(SiliconFlow, "list_catalog", side_effect=catalog), patch.object(OpenRouter, "list_catalog", return_value=[]):
            load_catalogs(self.registry)
        ready = self.invoke("--host-ready", run)
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertEqual(json.loads(ready.stdout)["ready"][0]["model_candidates"][0]["model"], "example/text")
        def check(route):
            self.assertEqual(SiliconFlow.api_key(), "sf-bound")
        with patch("pioneers.dashboard_is_running", return_value=True), patch("pioneers.check_available", side_effect=check):
            claim = update_host(run, "spec", None, None, None, None)
        self.assertEqual(claim["executor"]["provider"], "siliconflow")
        def worker(command, workspace, renew, *, deadline_seconds, env):
            self.assertEqual(env.get(SiliconFlow.key_env), "sf-bound")
            self.assertNotIn(OpenRouter.key_env, env)
            Path(command[-1]).write_text(json.dumps({"content": "Verified"}))
            return 0
        with patch("pioneers.supervise_host_executor", side_effect=worker), redirect_stdout(io.StringIO()):
            self.assertEqual(execute_host(run, "spec", 1, None), 0)
        for path in run.glob("*"):
            if path.is_file():
                content = path.read_bytes()
                for private in ("sf-bound", "router-bound", str(self.envfile)):
                    self.assertNotIn(private.encode(), content)
