from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from model_board import ModelBoard
from model_policy import read_policy, update_policy
from pioneers import host_ready, print_host_start, project_registry, update_host
from provider_credentials import PROVIDER_SETUP


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
        self.assertNotIn("model_preference_setup", started)
        self.assertEqual(started["provider_setup"]["action"], "ask")
        self.assertIn("文件路径", started["provider_setup"]["question"])
        reply = self.invoke("--skip-provider-setup", "--workspace", self.project)
        self.assertEqual(reply, {"provider_setup": "skipped"})
        ready = self.invoke("--host-ready", started["run_dir"])
        self.assertNotIn("provider_setup", ready)
        self.assertEqual(ready["ready"][0]["model_candidates"], [])
        later = self.start()
        self.assertNotIn("provider_setup", later)
        self.assertNotIn("model_preference_setup", later)
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
        self.assertEqual(result.pop("model_preference_setup")["action"], "ask")
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
        self.assertIn("model_preference_setup", self.invoke("--configure-provider-env", envfile, "--workspace", project))

    def test_bound_preference_reaches_board_ready_and_claim(self):
        started = self.start()
        envfile = self.root / "models.env"
        envfile.write_text("OPENROUTER_API_KEY=test-token\n")
        bind = self.invoke("--configure-provider-env", envfile, "--workspace", self.project)
        offer = bind["model_preference_setup"]
        self.assertEqual(offer["action"], "ask")
        self.assertIn("收费", offer["question"])
        policy = self.invoke("--set-model-preference", "仅免费", "--policy-revision", offer["revision"],
                             "--workspace", self.project)
        registry = project_registry(self.project)
        board = ModelBoard(registry, "test", self.project)
        with patch.object(board, "providers", return_value=[]):
            self.assertEqual(board.snapshot()["policy"], policy)
        catalogs = {"openrouter": [{"id": "v/flash-paid", "kinds": ["chat"]},
                                   {"id": "v/slow:free", "kinds": ["chat"]}]}
        run = Path(started["run_dir"])
        with patch.dict(os.environ, self.env), patch("model_selection.load_catalogs", return_value=catalogs), \
                patch("pioneers.dashboard_is_running", return_value=True), patch("pioneers.check_available"):
            ready = host_ready(run)["ready"][0]
            self.assertEqual([c["model"] for c in ready["model_candidates"]], ["v/slow:free"])
            self.assertEqual(update_host(run, "draft", None, None, None, None)["executor"]["model"], "v/slow:free")
        self.assertNotIn("model_preference_setup", self.start())
        self.assertNotIn("model_preference_setup", self.invoke("--configure-provider-env", envfile, "--workspace", self.project))

    def test_existing_policy_default_and_stale_reply(self):
        registry = project_registry(self.project)
        board = ModelBoard(registry, "test", self.project)
        pin = {"provider": "openrouter", "model": "test/chosen"}
        ban = {"provider": "openrouter", "model": "test/blocked"}
        update_policy(registry, {"action": "select", "revision": 0, "mode": "video", **pin})
        board.apply({"action": "ban", "revision": 1, **ban})
        env = {**self.env, "OPENROUTER_API_KEY": "test-token"}
        started = self.start(env=env)
        self.assertNotIn("provider_setup", started)
        revision = started["model_preference_setup"]["revision"]
        self.assertEqual(revision, 2)
        board.apply({"action": "preference", "value": "优先质量", "revision": revision})
        args = ("--set-model-preference", "", "--workspace", self.project, "--policy-revision")
        self.assertIn("刷新", self.invoke(*args, revision, expected=1))
        self.assertEqual(read_policy(registry)["preference"], "优先质量")
        saved = self.invoke(*args, revision + 1)
        self.assertEqual(saved, {"revision": 4, "preference": "", "selected": {"video": pin}, "banned": [ban]})
        self.assertNotIn("model_preference_setup", self.start(env=env))

    def test_preference_saved_before_binding_reuses_legacy_text_or_dashboard_default(self):
        envfile = self.root / "account.env"
        envfile.write_text("SILICONFLOW_API_KEY=fixture\n")
        for value in ("", "低成本"):
            with self.subTest(value=value):
                project = self.root / ("default" if not value else "legacy")
                project.mkdir()
                registry = project_registry(project)
                if value:
                    registry.mkdir(parents=True)
                    with closing(sqlite3.connect(registry / "model-policy.sqlite3")) as db, db:
                        db.execute("CREATE TABLE policy (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
                        db.execute("INSERT INTO policy VALUES (1, ?)", (json.dumps(
                            {"revision": 3, "selected": {}, "banned": [], "preference": value}),))
                else:
                    ModelBoard(registry, "test", project).apply({"action": "preference", "value": "", "revision": 0})
                self.assertNotIn("model_preference_setup", self.invoke("--configure-provider-env", envfile, "--workspace", project))
                self.assertNotIn("model_preference_setup", self.start(project))
                self.assertEqual(read_policy(registry)["preference"], value)

    def test_preference_offer_survives_broken_pipe_and_racing_bind_start(self):
        self.invoke("--bind-project", "--workspace", self.project)
        env = {**self.env, "SILICONFLOW_API_KEY": "test-token"}
        with patch.dict(os.environ, env), patch("builtins.print", side_effect=BrokenPipeError):
            with self.assertRaises(BrokenPipeError):
                print_host_start({"workspace": str(self.project)})
        envfile = self.root / "account.env"
        envfile.write_text("SILICONFLOW_API_KEY=fixture\n")
        with ThreadPoolExecutor(max_workers=2) as threads:
            bind = threads.submit(self.invoke, "--configure-provider-env", envfile, "--workspace", self.project)
            start = threads.submit(self.start, env=env)
        self.assertEqual(sum("model_preference_setup" in item.result() for item in (bind, start)), 1)
        self.assertNotIn("model_preference_setup", self.start())

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
        (registry / PROVIDER_SETUP).unlink()
        self.assertNotIn("provider_setup", self.start())

    def test_failed_start_or_startup_output_keeps_question_for_retry(self):
        command = [sys.executable, "-B", str(self.cli), "--host-start", str(self.workflow),
                   "--workspace", str(self.project), "--output-dir", str(self.root / "runs"), "--no-open-browser"]
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            failed = subprocess.run([*command, "--port", str(occupied.getsockname()[1])], text=True, env=self.env,
                                    capture_output=True, stdin=subprocess.DEVNULL, timeout=10)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(failed.stdout.strip(), "")

        # A broken coordinator pipe must roll back the in-flight response too.
        with patch.dict(os.environ, self.env), patch("builtins.print", side_effect=BrokenPipeError):
            with self.assertRaises(BrokenPipeError):
                print_host_start({"workspace": str(self.project)})
        with subprocess.Popen([*command, "--port", "0", "--serve-dashboard"], text=True, env=self.env,
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as retry:
            try:
                started = json.loads(retry.stdout.readline())
                self.assertEqual(started["provider_setup"]["action"], "ask")
                self.assertTrue(started["dashboard_url"])
                # Query before stopping the server so it commits its callback normally.
                self.assertNotIn("provider_setup", self.start())
            finally:
                retry.terminate()
                retry.communicate(timeout=5)

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
