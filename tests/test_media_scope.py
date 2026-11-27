import json
import os
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import model_worker
import pioneers
from iterations import register_run
from model_board import run_usage
from model_plugins import SiliconFlow
from model_policy import update_policy
from progress import Progress


class MediaScopeTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.video = dict(id="clip", role="engineer", prompt="生成约10秒的活动短片，生成前须用户确认。",
                          video_prompt="约10秒9:16竖屏，合上电脑走向草地。", tools=[], impact="costly_change",
                          video_settings={"duration": 10, "ratio": "9:16"})
        self.poster = dict(id="poster", role="engineer", prompt="制作预热海报", tools=[],
                           media={"type": "image", "prompt": "手机竖版，使用草地绿。"},
                           media_copy_from="copy", depends_on=["copy"])
        self.copy = dict(id="copy", role="pm", prompt="确认预热文案")
        self.public = "时间待定 · 地点待定\n报名链接待公布"
        self.document = "internal attendee roster\n```media-copy\n" + self.public + "\n```\n预算未公开"
        for mock in (patch.dict(os.environ, SILICONFLOW_API_KEY="test-key"),
                     patch("pioneers.check_available"), patch("pioneers.dashboard_is_running", return_value=True)):
            mock.start()
            self.addCleanup(mock.stop)

    def create(self, *definitions):
        for task in definitions:
            task["complexity"] = {"level": "low", "reason": "isolated media test"}
        plan = dict(goal="Campaign deliverables", tasks=list(definitions))
        goal, tasks, order = pioneers.validate_workflow(plan)
        self.run, progress = pioneers.create_run(self.root / "runs", goal, tasks, order, "host")
        self.tasks = tasks
        (self.run / "workflow.json").write_text(json.dumps(plan))
        progress.data["workspace"] = str(self.root)
        pioneers.reconcile(progress, tasks)
        progress.save()
        mode = "video" if "clip" in tasks else "image"
        model = "example/video" if mode == "video" else "OpenMOSS-Team/MOSS-Image"
        update_policy(pioneers.project_registry(self.root), dict(action="select", revision=0, mode=mode,
                                                                 provider="siliconflow", model=model))

    def claim(self, task):
        return pioneers.update_host(self.run, task, None, None, None, None)

    def state(self):
        return Progress.open(self.run).data

    def release_copy(self, document):
        with Progress.locked(self.run) as progress:
            cell = pioneers.stage_result(progress, self.tasks, "copy", document, "定稿")
            pioneers.record_judgment(progress, self.tasks, cell["decision"],
                                     json.dumps(dict(needs_confirmation=False, reason="copy accepted")),
                                     task_id="copy", attempt=1)

    def test_only_current_video_approval_unlocks_one_submission(self):
        self.create(self.video)
        decision = self.state()["tasks"]["clip"]["authorization"]
        entry = self.state()["decisions"][decision]
        self.assertEqual(entry["media_scope"]["video_settings"], self.video["video_settings"])
        self.assertIn(self.video["video_prompt"], entry["content"])
        with patch.object(SiliconFlow, "request") as provider:
            with self.assertRaisesRegex(ValueError, "authorization"):
                self.claim("clip")
            provider.assert_not_called()
            self.assertFalse(list(self.run.glob(".model-*")))
            pioneers.control_run(self.run, dict(action="approve", decisions=[decision], reason="同意本批规格"))
            assignment = self.claim("clip")
            self.assertEqual(assignment["executor"]["video_settings"]["duration"], 10)
            req = json.loads((self.run / ".model-clip-1.json").read_text())
            provider.side_effect = [{"requestId": "one"}, {"status": "Succeed", "results": {
                "videos": [{"url": "https://media.example/clip.mp4"}]}}]
            self.assertEqual(model_worker.run(req, self.root, self.root / "out.json"), 0)
            self.assertEqual(model_worker.run(req, self.root, self.root / "out.json"), 0)
            submissions = [call for call in provider.call_args_list if call.args[0] == "/video/submit"]
            self.assertEqual(len(submissions), 1)
            body = submissions[0].kwargs["payload"]
            self.assertEqual((body["duration"], body["ratio"]), (10, "9:16"))

    def test_editing_generation_approval_returns_to_ld(self):
        self.create(self.video)
        decision = self.state()["tasks"]["clip"]["authorization"]
        pioneers.control_run(self.run, dict(action="revise", decision=decision, reason="换画幅", content="改为横屏"))
        self.assertEqual(self.state()["decisions"][decision]["state"], "superseded")
        self.assertIn("clip", self.state()["replan"]["media_tasks"])
        with self.assertRaises(ValueError):
            self.claim("clip")
        self.assertFalse(list(self.run.glob(".model-*")))

    def test_changed_claim_payload_is_stopped_before_start(self):
        self.create(self.video)
        decision = self.state()["tasks"]["clip"]["authorization"]
        pioneers.control_run(self.run, dict(action="approve", decisions=[decision], reason="本批视频"))
        self.claim("clip")
        path = self.run / ".model-clip-1.json"
        request = json.loads(path.read_text())
        request["route"]["ratio"] = "16:9"
        path.write_text(json.dumps(request))
        with patch.object(SiliconFlow, "request") as provider:
            with Progress.locked(self.run) as progress:
                self.assertEqual(pioneers.model_launch_gate(progress, "clip"), "pending")
            provider.assert_not_called()
        self.assertIn("clip", self.state()["replan"]["media_tasks"])
        self.assertEqual(self.state()["decisions"][decision]["state"], "superseded")

    def test_poster_worker_receives_only_released_copy(self):
        self.poster["media"]["aspect_ratio"] = "3:4"
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        self.claim("poster")
        req = json.loads((self.run / ".model-poster-1.json").read_text())
        self.assertEqual(req["route"]["image_size"], "896x1152")
        self.assertIn(self.public, req["media_prompt"])
        self.assertIn("草地绿", req["media_prompt"])
        for private in ["internal attendee roster", "预算未公开"]:
            self.assertNotIn(private, str(req))

    def test_missing_or_changed_public_copy_returns_to_ld(self):
        for valid_at_release in [False, True]:
            with self.subTest(valid_at_release=valid_at_release):
                self.root = self.root / "case"
                self.root.mkdir()
                self.create(self.copy, self.poster)
                self.release_copy(self.document if valid_at_release else "internal attendee roster")
                if valid_at_release:
                    (self.run / "copy.md").write_text(self.document.replace(self.public, "new date") + "\n")
                with self.assertRaises(ValueError):
                    self.claim("poster")
                self.assertIn("poster", self.state()["replan"]["media_tasks"])
                self.assertFalse(list(self.run.glob(".model-*")))

    def test_unsubmitted_old_moss_size_returns_to_ld_before_worker_starts(self):
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        self.claim("poster")
        path = self.run / ".model-poster-1.json"
        req = json.loads(path.read_text())
        req["route"]["image_size"] = "960x1280"
        path.write_text(json.dumps(req))
        with Progress.locked(self.run) as progress:
            pioneers.current_executor(progress.data["tasks"]["poster"])["image_size"] = "960x1280"
        with patch("pioneers.supervise_host_executor") as worker, redirect_stdout(StringIO()):
            pioneers.execute_host(self.run, "poster", 1, None)
            worker.assert_not_called()
        self.assertIn("poster", self.state()["replan"]["media_tasks"])
        self.assertFalse(list(self.run.glob(".media-poster-*")))

    def test_legacy_unsubmitted_brief_with_unsupported_ratio_returns_to_ld(self):
        media = {"type": "image", "prompt": "制作4:5竖版海报。"}
        self.create(dict(id="poster", role="engineer", prompt="制作预热海报", tools=[], media=media))
        with patch("siliconflow_media.image_size", return_value="1024x1280"):
            self.claim("poster")
        path = self.run / ".model-poster-1.json"
        req = json.loads(path.read_text())
        del req["route"]["image_size"]
        path.write_text(json.dumps(req))
        with Progress.locked(self.run) as progress:
            del pioneers.current_executor(progress.data["tasks"]["poster"])["image_size"]
            self.assertEqual(pioneers.model_launch_gate(progress, "poster"), "pending")
        self.assertIn("poster", self.state()["replan"]["media_tasks"])
        self.assertFalse(list(self.run.glob(".media-poster-*")))

    def test_prepared_poster_receipt_reuses_original_copy(self):
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        self.claim("poster")
        # Simulate an existing paid result with the size saved by the previous release.
        path = self.run / ".model-poster-1.json"
        req = json.loads(path.read_text())
        req["route"]["image_size"] = "960x1280"
        path.write_text(json.dumps(req))
        with Progress.locked(self.run) as progress:
            pioneers.current_executor(progress.data["tasks"]["poster"])["image_size"] = "960x1280"
        with patch("generated_media.generate", return_value=(b"\x89PNG\r\n\x1a\n" + bytes(30), "image/png", {})) as submit:
            self.assertEqual(model_worker.run(req, self.root, self.root / "result.json"), 0)
            # Simulate a coordinator disconnect after publication and a later change to the source.
            with Progress.locked(self.run) as progress:
                pioneers.record_assignment_loss(progress, self.tasks, "poster", "executor stopped", executor_stopped=True)
            (self.run / "copy.md").write_text("replacement after original submission")
            self.claim("poster")
            retry = json.loads((self.run / ".model-poster-2.json").read_text())
            self.assertTrue(retry["reuse_only"])
            self.assertEqual(retry["route"]["image_size"], "960x1280")
            with Progress.locked(self.run) as progress:
                self.assertIsNone(pioneers.model_launch_gate(progress, "poster"))
            self.assertEqual(retry["media_prompt"], req["media_prompt"])
            self.assertEqual(model_worker.run(retry, self.root, self.root / "rerun.json"), 0)
            submit.assert_called_once()

    def test_failed_siliconflow_poster_exposes_only_safe_diagnostics(self):
        self.poster["media"]["prompt"] = "制作3:4竖版活动海报。"
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        registry = pioneers.project_registry(self.root)
        model = "OpenMOSS-Team/MOSS-Image"
        update_policy(registry, dict(action="select", revision=1, mode="image", provider="siliconflow", model=model))
        register_run(registry, self.run)
        self.claim("poster")
        posts = []

        def respond(request, **options):
            if request.get_method() == "GET":
                return BytesIO(json.dumps({"data": [{"id": model}]}).encode())
            self.assertEqual(request.full_url, "https://api.siliconflow.cn/v1/images/generations")
            payload = json.loads(request.data)
            posts.append(payload)
            body = {"code": 20012, "message": "Invalid image_size. " + payload["prompt"],
                    "data": {"authorization": "Bearer secret-canary"}}
            raise HTTPError(request.full_url, 400, "bad request", {"x-siliconcloud-trace-id": "trace-poster-42"},
                            BytesIO(json.dumps(body).encode()))

        def worker(command, workspace, renew, **options):
            renew()
            request, out = map(Path, command[-2:])
            return model_worker.run(json.loads(request.read_text()), Path(workspace), out)

        with patch("model_plugins.build_opener") as opener, patch("pioneers.supervise_host_executor", worker), redirect_stdout(StringIO()):
            opener.return_value.open.side_effect = respond
            self.assertEqual(pioneers.execute_host(self.run, "poster", 1, None), 1)
        self.assertEqual([p["image_size"] for p in posts], ["896x1152"])
        attempt = self.state()["tasks"]["poster"]["executors"]["1"]
        details = attempt["provider_error"]
        self.assertEqual(attempt["error_code"], "request_rejected")
        self.assertEqual(details["http_status"], 400)
        self.assertEqual(str(details["code"]), "20012")
        self.assertIn("image_size", details["reason"])
        self.assertEqual(details["trace_id"], "trace-poster-42")
        usage = next(row for row in run_usage(registry) if row["task"] == "poster")
        self.assertEqual(usage["provider_error"], details)
        for stored in [str(attempt), str(usage)]:
            self.assertNotIn("报名链接待公布", stored)
            self.assertNotIn("secret-canary", stored)
