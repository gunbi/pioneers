from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from model_policy import update_policy
from pioneers import project_registry, update_host


class FirstProviderSetupTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.workflow = self.root / "plan.json"
        self.workflow.write_text(json.dumps({"goal": "Prepare a summary", "tasks": [
            {"id": "draft", "role": "pm", "prompt": "Summarize the request", "tools": [], "acceptance": ["Summary"],
             "complexity": {"level": "low", "reason": "One short document"}}]}))
        self.cli = Path(__file__).resolve().parents[1] / "pioneers.py"
        self.env = {**os.environ, "HOME": str(self.root), "USERPROFILE": str(self.root),
                    "PIONEERS_SKILLS_OFFLINE": "1", "OPENROUTER_API_KEY": "", "SILICONFLOW_API_KEY": ""}

    def invoke(self, *args, env=None, expected=0):
        proc = subprocess.run([sys.executable, "-B", str(self.cli), *map(str, args)], text=True,
                              env=self.env if env is None else env, capture_output=True, timeout=15,
                              stdin=subprocess.DEVNULL)
        self.assertEqual(proc.returncode, expected, proc.stderr)
        return json.loads(proc.stdout) if expected == 0 else proc.stderr

    def start(self, project=None, env=None):
        return self.invoke("--host-start", self.workflow, "--workspace", project or self.project,
                           "--output-dir", self.root / "runs", "--register-only", env=env)

    def test_first_offer_skip_and_next_session(self):
        self.invoke("--bind-project", "--workspace", self.project)
        history = self.invoke("--project-history", "--workspace", self.project, "--output-dir", self.root / "runs")
        self.assertNotIn("provider_setup", history)
        started = self.start()
        self.assertEqual(started["provider_setup"]["action"], "ask")
        self.assertIn("文件路径", started["provider_setup"]["question"])
        reply = self.invoke("--skip-provider-setup", "--workspace", self.project)
        self.assertEqual(reply, {"provider_setup": "skipped"})
        ready = self.invoke("--host-ready", started["run_dir"])
        self.assertNotIn("provider_setup", ready)
        self.assertEqual(ready["ready"][0]["model_candidates"], [])
        self.assertNotIn("provider_setup", self.start())
        other = self.root / "other"
        other.mkdir()
        self.assertIn("provider_setup", self.start(other))

    def test_supplied_file_is_bound_and_clear_does_not_repeat_prompt(self):
        self.assertIn("provider_setup", self.start())
        envfile = self.root / "account.env"
        envfile.write_text("OPENROUTER_API_KEY='invalid key'\n")
        error = self.invoke("--configure-provider-env", envfile, "--workspace", self.project, expected=1)
        self.assertNotIn("invalid key", error)
        envfile.write_text("OPENROUTER_API_KEY=sample-only-test-key\n")
        result = self.invoke("--configure-provider-env", envfile, "--workspace", self.project)
        self.assertEqual(result, {"configured": True, "variables": ["OPENROUTER_API_KEY"]})
        self.assertNotIn("provider_setup", self.start())
        self.invoke("--clear-provider-env", "--workspace", self.project)
        self.assertNotIn("provider_setup", self.start())
        for doc in (self.project / ".pioneers" / "iterations").glob("*.json"):
            self.assertNotIn("sample-only-test-key", doc.read_text())
            self.assertNotIn(str(envfile), doc.read_text())

    def test_existing_credentials_and_proactive_skip(self):
        self.assertNotIn("provider_setup", self.start(env={**self.env, "SILICONFLOW_API_KEY": "already-exported"}))
        self.assertNotIn("provider_setup", self.start())
        envfile = self.root / "account.env"
        envfile.write_text("SILICONFLOW_API_KEY=bound-only-for-test\n")
        project = self.root / "bound"
        project.mkdir()
        self.invoke("--configure-provider-env", envfile, "--workspace", project)
        self.assertNotIn("provider_setup", self.start(project))
        project = self.root / "skipped"
        project.mkdir()
        self.invoke("--skip-provider-setup", "--workspace", project)
        self.assertNotIn("provider_setup", self.start(project))
        self.assertTrue(self.invoke("--configure-provider-env", envfile, "--workspace", project)["configured"])

    def test_skip_allows_implicit_remote_defaults_and_pins_to_fall_back_to_host(self):
        for fixed in ("default", "pin"):
            with self.subTest(fixed=fixed):
                project = self.root / fixed
                project.mkdir()
                config = project / ".pioneers"
                config.mkdir()
                model = {"provider": "siliconflow", "model": "test/writer", "mode": "text"}
                (config / "models.json").write_text(json.dumps({"profiles": {"writer": model}, "defaults":
                    {"roles": {"pm": "writer"}} if fixed == "default" else {}}))
                envfile = project.parent / (fixed + ".env")
                if fixed == "pin":
                    update_policy(project_registry(project), {"action": "select", "revision": 0, **model})
                    envfile.write_text("SILICONFLOW_API_KEY=synthetic\n")
                    self.invoke("--configure-provider-env", envfile, "--workspace", project)
                    envfile.unlink()  # A lost binding file also leaves the remote default unusable.
                started = self.start(project)
                run = Path(started["run_dir"])
                self.assertIn("provider_setup", started)
                self.invoke("--skip-provider-setup", "--workspace", project)
                ready = self.invoke("--host-ready", run)["ready"][0]
                self.assertIn("由宿主执行", ready["model_routing_reason"])
                with patch.dict(os.environ, self.env), patch("pioneers.dashboard_is_running", return_value=True):
                    with self.assertRaises(ValueError):
                        update_host(run, "draft", None, None, None, None, profile="writer")
                    with self.assertRaisesRegex(ValueError, "model-choice"):
                        update_host(run, "draft", None, None, None, None, model_choice="siliconflow/test/writer")
                    claim = update_host(run, "draft", None, None, None, None)
                self.assertEqual(claim["executor"]["provider"], "host")
                envfile.write_text("SILICONFLOW_API_KEY=synthetic\n")
                self.invoke("--configure-provider-env", envfile, "--workspace", project)
                later = self.start(project)
                self.assertNotIn("provider_setup", later)
                candidates = self.invoke("--host-ready", later["run_dir"])["ready"][0]["model_candidates"]
                self.assertEqual(candidates[0]["model"], "test/writer")

    def test_concurrent_start_and_legacy_history(self):
        self.invoke("--bind-project", "--workspace", self.project)
        with ThreadPoolExecutor(max_workers=4) as threads:
            starts = list(threads.map(lambda _: self.start(), range(4)))
        self.assertEqual(sum("provider_setup" in result for result in starts), 1)
        # Simulate a registry from before onboarding without changing its real run history.
        registry = self.project / ".pioneers" / "iterations"
        (registry / "provider-setup.json").unlink()
        self.assertNotIn("provider_setup", self.start())

    def test_sibling_worktree_uses_same_first_run_decision(self):
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Test", "-c", "user.email=test@example.org",
                        "commit", "--allow-empty", "-qm", "initial"], check=True)
        sibling = self.root / "sibling"
        subprocess.run(["git", "-C", str(self.project), "worktree", "add", "-q", "--detach", str(sibling)],
                       check=True)
        self.assertIn("provider_setup", self.start())
        self.invoke("--skip-provider-setup", "--workspace", sibling)
        self.assertNotIn("provider_setup", self.start(sibling))
