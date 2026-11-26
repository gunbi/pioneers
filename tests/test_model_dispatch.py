from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event, Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import model_worker
import pioneers
from dashboard import dashboard_snapshot
from model_plugins import InsufficientCreditError, OpenRouter, ProviderError
from model_policy import read_policy, update_policy
from pioneers import control_run, create_run, execute_host, handle_judgment, host_ready, project_registry, update_host, validate_workflow
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

    def claim(self, profile=None, **kwargs):
        return update_host(self.run, "spec", None, None, None, None, profile=profile, **kwargs)

    def invoke_worker(self, command, workspace, renew, *, deadline_seconds, env):
        self.assertGreater(deadline_seconds, 0)
        renew()
        request, output = map(Path, command[-2:])
        return model_worker.run(json.loads(request.read_text()), Path(workspace), output)

    def offline_cli(self):
        # Use the real CLI, detached supervisor, worker, and HTTP adapter in fresh
        # processes. Only the provider address points at this test's loopback API.
        sent, release = Event(), Event()
        posts = []
        run = self.run

        class Provider(BaseHTTPRequestHandler):
            def reply(self, body):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/health/" + run.name:
                    self.reply(run.name.encode())
                elif self.path.startswith("/v1/models"):
                    self.reply(b'{"data":[{"id":"example/text"}]}')
                else:
                    self.send_error(404)

            def do_POST(self):
                if self.path != "/v1/chat/completions":
                    return self.send_error(404)
                posts.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                sent.set()
                release.wait(10)
                self.reply(b'{"choices":[{"finish_reason":"stop","message":{"content":"Offline delivery"}}],'
                           b'"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}')

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
        Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(release.set)
        with Progress.locked(run) as progress:
            progress.data["dashboard_port"] = server.server_port
        framework = self.project.parent / "framework"
        framework.mkdir()
        for source in (*pioneers.ROOT.glob("*.py"), *pioneers.ROOT.glob("*.json")):
            shutil.copyfile(source, framework / source.name)
        with (framework / "model_plugins.py").open("a") as module:
            module.write(f'\nSiliconFlow.base_url = OpenRouter.base_url = "http://127.0.0.1:{server.server_port}/v1"\n')

        def cli(*args, check=True):
            env = {**os.environ, "HOME": str(self.project.parent), "NO_PROXY": "127.0.0.1"}
            env.pop("OPENROUTER_API_KEY", None)
            result = subprocess.run([sys.executable, str(framework / "pioneers.py"), *map(str, args)],
                                    capture_output=True, text=True, env=env, timeout=8)
            if check:
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                return json.loads(result.stdout)
            return result
        return cli, sent, release, posts

    def test_cli_claim_launches_once_and_survives_claim_process_exit(self):
        cli, sent, release, posts = self.offline_cli()
        claim = cli("--host-claim", self.run, "--task", "spec")
        self.assertEqual(claim["launch_status"], "scheduled")
        self.assertNotIn("start_command", claim)
        self.assertTrue(sent.wait(8))
        with ThreadPoolExecutor(max_workers=4) as pool:
            ready = list(pool.map(lambda _: cli("--host-ready", self.run), range(4)))
        self.assertTrue(all(view["active"][0]["execution_phase"] == "running" for view in ready))
        duplicate = cli("--host-exec", self.run, "--task", "spec", "--attempt", 1, check=False)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("already active", duplicate.stderr)
        release.set()
        view = cli("--host-ready", self.run, "--wait", 5)
        self.assertEqual(view["active"][0]["state"], "judging")
        data, artifacts = dashboard_snapshot(self.run / "status.json")
        executor = data["tasks"]["spec"]["executors"]["1"]
        self.assertEqual(executor["usage"]["total_tokens"], 6)
        self.assertEqual(executor["launch_count"], 1)
        self.assertIn("spec.md", artifacts)
        self.assertEqual((self.run / "spec.md").read_text().strip(), "Offline delivery")
        self.assertEqual([call["model"] for call in posts], ["example/text"])

    @patch("pioneers.check_available")
    def test_multiple_coordinators_resume_one_unstarted_claim_after_pause(self, _preflight):
        cli, sent, release, posts = self.offline_cli()
        self.claim()  # Simulates a coordinator exiting just after its claim was saved.
        control_run(self.run, {"action": "pause", "reason": "inspect"})
        self.assertEqual(cli("--host-ready", self.run)["active"][0]["execution_phase"], "awaiting_start")
        self.assertEqual(cli("--host-exec", self.run, "--task", "spec", "--attempt", 1, check=False).returncode, 0)
        control_run(self.run, {"action": "resume", "reason": "continue"})
        data, _ = dashboard_snapshot(self.run / "status.json")
        self.assertNotIn("launch_count", data["tasks"]["spec"]["executors"]["1"])
        self.assertFalse(sent.is_set())
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: cli("--host-ready", self.run), range(4)))
        self.assertTrue(sent.wait(8))
        release.set()
        self.assertEqual(cli("--host-ready", self.run, "--wait", 5)["active"][0]["state"], "judging")
        self.assertEqual(len(posts), 1)
        self.assertEqual(Progress.open(self.run).data["tasks"]["spec"]["executors"]["1"]["launch_count"], 1)

    @patch("pioneers.check_available")
    def test_crashed_inference_requires_reconciliation(self, _preflight):
        self.claim()
        with Progress.locked(self.run) as progress:
            job = progress.data["tasks"]["spec"]
            job["executors"]["1"]["started_at"] = "2026-10-10"
            job["lease_expires_at"] = 1
        with patch("pioneers.subprocess.Popen") as spawn:
            ready = host_ready(self.run, launch_models=True)
            spawn.assert_not_called()
        self.assertEqual(ready["waiting"][0]["state"], "recovery_required")

    @patch("pioneers.check_available")
    def test_failed_spawn_is_recorded_once(self, _preflight):
        self.claim()
        with patch("pioneers.subprocess.Popen", side_effect=OSError("no process slots")) as spawn:
            self.assertEqual(pioneers.schedule_model(self.run, "spec", 1), "recovery_required")
            ready = host_ready(self.run, launch_models=True)
            spawn.assert_called_once()
        self.assertEqual(ready["waiting"][0]["state"], "recovery_required")
        self.assertIn("无法启动", Progress.open(self.run).data["tasks"]["spec"]["executors"]["1"]["error"])

    @patch("pioneers.check_available")
    def test_crash_before_start_has_a_bounded_retry_budget(self, _preflight):
        self.claim()
        with patch("pioneers.subprocess.Popen") as spawn:
            for _ in range(pioneers.MAX_MODEL_STARTS):
                self.assertEqual(pioneers.schedule_model(self.run, "spec", 1), "scheduled")
                self.assertEqual(pioneers.schedule_model(self.run, "spec", 1), "scheduled")
                with Progress.locked(self.run) as progress:
                    progress.data["tasks"]["spec"]["executors"]["1"]["launch_requested_at"] = 1
            self.assertEqual(pioneers.schedule_model(self.run, "spec", 1), "recovery_required")
            self.assertEqual(spawn.call_count, pioneers.MAX_MODEL_STARTS)

    @patch("pioneers.check_available")
    def test_revoked_authorization_prevents_model_start(self, _preflight):
        self.claim()
        with Progress.locked(self.run) as progress:
            progress.data["tasks"]["spec"]["authorization"] = "revoked"
        with patch("pioneers.subprocess.Popen") as spawn:
            self.assertEqual(pioneers.schedule_model(self.run, "spec", 1), "recovery_required")
            spawn.assert_not_called()

    def test_supervisor_passes_only_selected_provider_and_runtime_environment(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "other-provider",
                                     "UNRELATED_SECRET": "private", "GIT_DIR": "/other"}):
            env = pioneers.model_supervisor_environment("siliconflow")
        self.assertEqual(env["SILICONFLOW_API_KEY"], "local-test-only")
        self.assertIn("PATH", env)
        self.assertFalse({"OPENROUTER_API_KEY", "UNRELATED_SECRET", "GIT_DIR"}.intersection(env))

    def test_preflight_failure_does_not_claim(self):
        with patch("pioneers.check_available", side_effect=ProviderError("catalog unavailable")):
            with self.assertRaisesRegex(ProviderError, "catalog unavailable"):
                self.claim()
        self.assertEqual(Progress.open(self.run).data["tasks"]["spec"]["state"], "pending")
        host = self.claim("host", host_reason="用户要求在本机处理")["executor"]
        self.assertEqual(host, {"provider": "host", "reason": "本轮指定宿主执行 · 用户要求在本机处理"})

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
    def test_unstarted_remote_claim_cannot_be_reported_as_work(self, _preflight):
        assignment = self.claim()
        self.assertEqual(assignment["execution_phase"], "awaiting_start")
        self.assertNotIn("lease_expires_at", assignment)
        # Earlier releases wrote an execution deadline even before a worker started.
        with Progress.locked(self.run) as progress:
            progress.data["tasks"]["spec"]["lease_expires_at"] = 1
        ready = host_ready(self.run, observed_active=[("spec", 1)])
        self.assertFalse(ready["observations"][0]["renewed"])
        row = ready["active"][0]
        self.assertEqual(row["execution_phase"], "awaiting_start")
        self.assertIsNone(row["lease_expires_at"])
        self.assertNotIn("start_command", row)
        self.assertNotIn("start_command", assignment)
        for state in ("running", "checkpoint", "completed", "failed", "blocked"):
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, "model executor"):
                update_host(self.run, "spec", state, None, None, 1)
        def run_worker(*args, **kwargs):
            started = host_ready(self.run)["active"][0]
            self.assertEqual(started["execution_phase"], "running")
            self.assertGreater(started["lease_expires_at"], 1)
            self.assertNotIn("start_command", started)
            with self.assertRaisesRegex(ValueError, "still active"):
                update_host(self.run, "spec", "lost", "late coordinator", None, 1)
            with Progress.locked(self.run) as progress:
                progress.data["tasks"]["spec"]["lease_expires_at"] = 1
            with self.assertRaisesRegex(ValueError, "still active"):
                control_run(self.run, {"action": "recover", "task": "spec", "attempt": 1, "reason": "check worker"})
            self.assertEqual(host_ready(self.run)["active"][0]["attempt"], 1)
            with self.assertRaisesRegex(ValueError, "already active"):
                execute_host(self.run, "spec", 1, None)
            return self.invoke_worker(*args, **kwargs)

        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", run_worker),
              patch("model_worker.complete", return_value=("result", {})) as generate):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 0)
            generate.assert_called_once()
        with self.assertRaisesRegex(ValueError, "claimed and running"):
            execute_host(self.run, "spec", 1, None)

    @patch("pioneers.check_available")
    def test_release_unstarted_attempt_fences_its_old_command(self, _preflight):
        self.claim()
        result = control_run(self.run, {"action": "recover", "task": "spec", "attempt": 1, "reason": "release reservation"})
        self.assertEqual(result["attempt"], 2)
        self.claim()
        with patch("pioneers.supervise_host_executor") as worker:
            with self.assertRaisesRegex(ValueError, "active --attempt 2"):
                execute_host(self.run, "spec", 1, None)
            worker.assert_not_called()
        self.assertEqual(host_ready(self.run)["active"][0]["execution_phase"], "awaiting_start")

    @patch("pioneers.check_available")
    def test_credit_failure_with_checkpoint_requires_reconciliation(self, _preflight):
        self.claim()
        event = self.run / "checkpoint.json"
        event.write_text(json.dumps({"type": "checkpoint", "summary": "original work"}))
        def checkpoint_then_run(*args, **kwargs):
            update_host(self.run, "spec", "checkpoint", None, event, 1)
            return self.invoke_worker(*args, **kwargs)

        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", checkpoint_then_run),
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
    def test_timeout_requires_reconciliation_before_host_retry_and_fences_old_attempt(self, _catalog):
        self.claim()
        with (redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", self.invoke_worker),
              patch("model_plugins.build_opener") as opener):
            opener.return_value.open.side_effect = TimeoutError("socket timed out")
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
            self.assertEqual(host_ready(self.run, launch_models=True)["ready"], [])
            opener.return_value.open.assert_called_once()
        job = Progress.open(self.run).data["tasks"]["spec"]
        self.assertEqual(job["state"], "recovery_required")
        self.assertEqual(job["executors"]["1"]["error_code"], "outcome_unknown")
        control_run(self.run, {"action": "recover", "task": "spec", "attempt": 1, "reason": "verified at provider"})
        self.assertEqual(read_policy(project_registry(self.project))["banned_providers"], [])
        retry = self.claim("host", host_reason="用户核对后选择本机接续")
        self.assertEqual(retry["attempt"], 2)
        with self.assertRaisesRegex(ValueError, "active --attempt 2"):
            execute_host(self.run, "spec", 1, None)
        data, _ = dashboard_snapshot(self.run / "status.json")
        history = data["tasks"]["spec"]["executors"]
        self.assertEqual(history["1"]["error"], "SiliconFlow timed out")
        self.assertEqual(history["2"]["provider"], "host")

    @patch("pioneers.check_available")
    def test_missing_worker_response_requires_reconciliation(self, _preflight):
        self.claim()
        with redirect_stdout(StringIO()), patch("pioneers.supervise_host_executor", return_value=1):
            self.assertEqual(execute_host(self.run, "spec", 1, None), 1)
        job = Progress.open(self.run).data["tasks"]["spec"]
        self.assertEqual(job["state"], "recovery_required")
        self.assertEqual(job["executors"]["1"]["error_code"], "outcome_unknown")

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
