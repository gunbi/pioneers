import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import model_worker
import pioneers
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
        update_policy(pioneers.project_registry(self.root), dict(action="select", revision=0, mode=mode,
                                                                 provider="siliconflow", model="example/" + mode))

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
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        self.claim("poster")
        req = json.loads((self.run / ".model-poster-1.json").read_text())
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

    def test_prepared_poster_receipt_reuses_original_copy(self):
        self.create(self.copy, self.poster)
        self.release_copy(self.document)
        self.claim("poster")
        req = json.loads((self.run / ".model-poster-1.json").read_text())
        with patch("generated_media.generate", return_value=(b"\x89PNG\r\n\x1a\n" + bytes(30), "image/png", {})) as submit:
            self.assertEqual(model_worker.run(req, self.root, self.root / "result.json"), 0)
            # Simulate a coordinator disconnect after publication and a later change to the source.
            with Progress.locked(self.run) as progress:
                pioneers.record_assignment_loss(progress, self.tasks, "poster", "executor stopped", executor_stopped=True)
            (self.run / "copy.md").write_text("replacement after original submission")
            self.claim("poster")
            retry = json.loads((self.run / ".model-poster-2.json").read_text())
            self.assertTrue(retry["reuse_only"])
            self.assertEqual(retry["media_prompt"], req["media_prompt"])
            self.assertEqual(model_worker.run(retry, self.root, self.root / "rerun.json"), 0)
            submit.assert_called_once()

