import json
import os
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from threading import Event, Thread
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import generated_media
import model_worker
from model_plugins import OpenRouter, ProviderError, check_available, resolve_route
from model_policy import update_policy
from pioneers import create_run, execute_host, project_registry, resolve_recovery, update_host, validate_workflow
from progress import Progress


PNG = b"\x89PNG\r\n\x1a\n" + bytes(30)
MP3 = b"ID3" + bytes(30)


class MediaDispatchTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        self.task = {"id": "art", "role": "engineer", "prompt": "Prepare the media", "tools": [],
                     "media": {"type": "image", "prompt": "Sunrise over a river"},
                     "complexity": {"level": "low", "reason": "One deliverable"}}
        self.route = {"profile": "dashboard", "provider": "openrouter", "mode": "image", "model": "example/image"}
        for mock in (patch.dict(os.environ, SILICONFLOW_API_KEY="other-test-key", OPENROUTER_API_KEY="router-test-key"),
                     patch("pioneers.dashboard_is_running", return_value=True)):
            mock.start()
            self.addCleanup(mock.stop)

    def setup_run(self, mode="image"):
        self.task["media"]["type"] = mode
        if mode == "speech":
            self.task["media"]["voice"] = "alice"
        plan = {"goal": "Project context that must stay private", "tasks": [self.task]}
        goal, tasks, order = validate_workflow(plan)
        self.run, progress = create_run(self.root / "runs", goal, tasks, order, "host")
        (self.run / "workflow.json").write_text(json.dumps(plan))
        progress.data["workspace"] = str(self.project)
        progress.save()
        update_policy(project_registry(self.project), {"action": "select", "revision": 0, "mode": mode,
                                                       "provider": "openrouter", "model": "example/" + mode})

    def claim(self, profile=None):
        with patch("pioneers.check_available"):
            return update_host(self.run, "art", None, None, None, None, profile=profile)

    def executor(self, command, workspace, renew, *, env, deadline_seconds):
        self.assertNotIn("SILICONFLOW_API_KEY", env)
        renew()
        request, output = map(Path, command[-2:])
        data = json.loads(request.read_text())
        if data.get("reuse_only"):
            self.assertNotIn("OPENROUTER_API_KEY", env)
        else:
            self.assertEqual(env["OPENROUTER_API_KEY"], "router-test-key")
        return model_worker.run(data, Path(workspace), output)

    def test_validation_rejects_combined_effects_and_noncreative_inputs(self):
        for change in ({"type": []}, {"voice": "alice"}, {"prompt": ""}, {"prompt": "a" * 10001}, {"url": "x"},
                       {"type": "speech", "voice": "bad\nvoice"}, {"type": "audio"}):
            task = {**self.task, "media": {**self.task["media"], **change}}
            with self.subTest(change=str(change)[:40]), self.assertRaisesRegex(ValueError, "media|voice"):
                validate_workflow({"goal": "produce", "tasks": [task]})
        for field in ({"video_prompt": "two modes"}, {"write_paths": ["app.py"]}, {"effect": "send"},
                      {"role": "qa", "review": True}, {"impact": "architecture"}):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "dedicated task"):
                validate_workflow({"goal": "produce", "tasks": [{**self.task, **field}]})

    def test_project_choice_voice_and_bans(self):
        for mode in generated_media.MEDIA_MODES:
            task = {**self.task, "media": {"type": mode, "prompt": "brief"}}
            if mode == "speech":
                task["media"]["voice"] = "alice"
            selected = {"provider": "openrouter", "model": "example/model"}
            policy = {"selected": {mode: selected}}
            with self.subTest(mode=mode):
                self.assertIsNone(resolve_route(self.project, task, isolated=False))
                route = resolve_route(self.project, task, isolated=False, policy=policy)
                self.assertEqual(route["mode"], mode)
                self.assertEqual(route.get("voice"), task["media"].get("voice"))
                with self.assertRaisesRegex(ValueError, "禁用"):
                    resolve_route(self.project, task, isolated=False, policy={**policy, "banned": [selected]})
                adapter = Mock()
                with patch.dict("sys.modules", media_adapters=adapter):
                    check_available(route)
                adapter.check_available.assert_called_once_with(OpenRouter, "example/model", mode, route)

    def test_unrelated_role_default_leaves_media_with_host(self):
        config = self.project / ".pioneers"
        config.mkdir()
        (config / "models.json").write_text(json.dumps({
            "profiles": {"coder": {"provider": "openrouter", "model": "example/coder", "mode": "patch"}},
            "defaults": {"roles": {"engineer": "coder"}},
        }))
        self.assertIsNone(resolve_route(self.project, self.task, isolated=False))
        with self.assertRaisesRegex(ValueError, "requires mode image"):
            resolve_route(self.project, self.task, "coder", isolated=False)

    def test_paid_speech_dispatch_excludes_context_and_publishes_usage_and_bytes(self):
        self.task["skills"] = ["engineering-design"]
        self.setup_run("speech")
        self.assertEqual(self.claim()["sandbox"], "read-only")
        request = json.loads((self.run / ".model-art-1.json").read_text())
        self.assertNotIn("Project context", json.dumps(request))
        usage = Progress.open(self.run).data["tasks"]["art"]["skill_attempts"]["1"]
        self.assertEqual(usage, {"planned": ["engineering-design"], "loaded": [], "reported": []})
        with self.assertRaisesRegex(ValueError, "native host"):
            update_host(self.run, "art", "running", "生成中", None, 1, used_skills=["engineering-design"])
        self.assertEqual(request["route"]["voice"], "alice")
        with patch("pioneers.supervise_host_executor", self.executor), redirect_stdout(StringIO()), patch(
                "generated_media.generate", return_value=(MP3, "audio/mpeg", {"prompt_tokens": 7, "extra": "omit"})) as submit:
            self.assertEqual(execute_host(self.run, "art", 1, None), 0)
        submit.assert_called_once_with(OpenRouter, request["route"], self.task["media"]["prompt"])
        record = Progress.open(self.run).data["tasks"]["art"]["executors"]["1"]
        self.assertEqual(record["media"], {"filename": "art.1.mp3", "mime": "audio/mpeg", "bytes": len(MP3)})
        self.assertEqual(record["usage"], {"audio_files": 1, "prompt_tokens": 7})
        self.assertEqual((self.run / "media" / "art.1.mp3").read_bytes(), MP3)
        self.assertFalse((self.project / "media").exists())

    def test_prepared_receipt_resumes_without_a_second_charge(self):
        self.setup_run()
        self.claim()
        with patch("generated_media.generate", return_value=(PNG, "image/png", {})) as submit:
            with patch("generated_media.os.link", side_effect=OSError("worker stopped before publication")), patch(
                    "pioneers.supervise_host_executor", self.executor), redirect_stdout(StringIO()):
                self.assertEqual(execute_host(self.run, "art", 1, None), 1)
            with self.assertRaisesRegex(ValueError, "回执"):
                self.claim("host")
            with patch.dict(os.environ, OPENROUTER_API_KEY=""), patch("pioneers.check_available") as lookup:
                assignment = update_host(self.run, "art", None, None, None, None)
                self.assertEqual(assignment["executor"]["media_generation"], 1)
                lookup.assert_not_called()
                with patch("pioneers.supervise_host_executor", self.executor), redirect_stdout(StringIO()):
                    self.assertEqual(execute_host(self.run, "art", 2, None), 0)
            submit.assert_called_once()
        self.assertEqual((self.run / "media" / "art.1.png").read_bytes(), PNG)

    def test_uncertain_submission_requires_explicit_external_verification(self):
        self.setup_run()
        self.claim()
        with patch("pioneers.supervise_host_executor", self.executor), redirect_stdout(StringIO()), patch(
                "generated_media.generate", side_effect=ProviderError("network timed out")) as submit:
            self.assertEqual(execute_host(self.run, "art", 1, None), 1)
            submit.assert_called_once()
        job = Progress.open(self.run).data["tasks"]["art"]
        self.assertEqual(job["state"], "recovery_required")
        request = {"task": "art", "attempt": 1}
        tasks = {"art": self.task}
        with self.assertRaisesRegex(ValueError, "recovery-checks"), Progress.locked(self.run) as progress:
            resolve_recovery(progress, tasks, request, "checked")
        checks = [{"id": "media-generation", "outcome": "not_executed", "reason": "Provider confirmed no submission"}]
        with Progress.locked(self.run) as progress:
            resolve_recovery(progress, tasks, {**request, "checks": checks}, "confirmed stopped")
        self.assertEqual(self.claim()["executor"]["media_generation"], 2)

    def test_concurrent_workers_only_pay_once(self):
        submitted, release = Event(), Event()
        args = (OpenRouter, self.route, "sun", self.root, "art", 1)
        errors = []

        def generate(*_args):
            submitted.set()
            if not release.wait(3):
                raise RuntimeError("test worker did not release mock provider")
            return PNG, "image/png", {}

        def worker():
            try:
                generated_media.media_job(*args)
            except Exception as error:
                errors.append(error)

        with patch("generated_media.generate", side_effect=generate) as submit:
            thread = Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(submitted.wait(3))
                with self.assertRaisesRegex(ValueError, "人工核对"):
                    generated_media.media_job(*args)
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(errors)
            self.assertFalse(thread.is_alive())
            self.assertEqual(generated_media.media_job(*args)["usage"], {"images": 1})
            submit.assert_called_once()

    def test_binary_transport_size_limit_and_content_type(self):
        response = BytesIO(b"abcdef")
        response.headers = {"Content-Type": "audio/pcm; rate=16000"}
        with patch("model_plugins.build_opener") as opener:
            opener.return_value.open.return_value = response
            self.assertEqual(OpenRouter.request("/audio/speech", timeout=5, raw=True, with_content_type=True,
                                                max_bytes=6), (b"abcdef", "audio/pcm; rate=16000"))
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, OpenRouter.base_url + "/audio/speech")
            self.assertEqual(request.get_header("Authorization"), "Bearer router-test-key")
            opener.return_value.open.return_value = BytesIO(b"abcdefg")
            with self.assertRaisesRegex(ProviderError, "exceeds"):
                OpenRouter.request("/audio/speech", timeout=5, raw=True, max_bytes=6)

    def test_reuse_verifies_bytes_and_parameters(self):
        with patch("generated_media.generate", return_value=(PNG, "image/png", {})) as submit:
            args = (OpenRouter, self.route, "river", self.root, "art", 1)
            self.assertEqual(generated_media.media_job(*args), generated_media.media_job(*args))
            with self.assertRaisesRegex(ValueError, "参数"):
                generated_media.media_job(OpenRouter, self.route, "sea", self.root, "art", 1)
            (self.root / "media" / "art.1.png").write_bytes(PNG[:-1] + b"x")
            with self.assertRaisesRegex(ValueError, "文件与回执"):
                generated_media.media_job(*args)
            submit.assert_called_once()

    def test_reject_symlinks_and_unexpected_files(self):
        (self.root / "media").symlink_to(self.project, target_is_directory=True)
        with patch("generated_media.generate") as submit, self.assertRaisesRegex(ValueError, "目录"):
            generated_media.media_job(OpenRouter, self.route, "river", self.root, "art", 1)
        submit.assert_not_called()
        (self.root / "media").unlink()
        (self.root / "media").mkdir()
        target = self.root / "media" / "art.1.png"
        target.write_bytes(b"owned")
        with patch("generated_media.generate", return_value=(PNG, "image/png", {})), self.assertRaisesRegex(ValueError, "回执"):
            generated_media.media_job(OpenRouter, self.route, "river", self.root, "art", 1)
        self.assertEqual(target.read_bytes(), b"owned")
