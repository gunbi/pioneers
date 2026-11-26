import unittest

from media_planning import media_plan_issue, required_media_types


class MediaPlanningTests(unittest.TestCase):
    def task(self, prompt, **fields):
        return {"id": "deliver", "role": "engineer", "prompt": prompt, **fields}

    def test_actual_generation_instructions_and_registration(self):
        examples = {
            "image": ["按已确认的活动信息与视觉设计制作手机群宣传海报。保存最终图片。",
                      "Generate a mobile-friendly event poster for Friday.", "设计一张招贴画"],
            "video": ["以已完成海报作为视觉参考制作约10秒宣传短片，输出实际可播放视频。",
                      "Produce a ten-second promo video using the approved poster.",
                      "制作约 10 秒的活动宣传片", "制作风格和海报一致的视频"],
        }
        for kind, prompts in examples.items():
            registration = {"video_prompt": "brief", "video_settings": {"duration": 10}} if kind == "video" else {"media": {"type": "image", "prompt": "brief"}}
            for prompt in prompts:
                with self.subTest(prompt=prompt):
                    task = self.task(prompt, role="pm")
                    self.assertEqual(required_media_types(task), {kind})
                    self.assertIn("LD", media_plan_issue(task))
                    self.assertIsNone(media_plan_issue({**task, **registration}))

    def test_mismatch_and_mixed_deliverables(self):
        for prompt in ("制作一张海报和一段视频", "Produce a poster and a 10-second film"):
            with self.subTest(prompt=prompt):
                task = self.task(prompt, video_prompt="brief")
                self.assertEqual(required_media_types(task), {"image", "video"})
                self.assertIn("拆成", media_plan_issue(task))
        self.assertIn("video_prompt", media_plan_issue(
            self.task("生成视频", media={"type": "image", "prompt": "brief"})))
        self.assertIn("media.type=image", media_plan_issue(self.task("制作海报", video_prompt="brief")))
        self.assertIn("write_paths", media_plan_issue(self.task(
            "opaque assignment", deliverable_type="image", media={"type": "image", "prompt": "brief"},
            write_paths=["media/art"])))

    def test_explicit_marker_and_output_in_acceptance(self):
        self.assertEqual(required_media_types(self.task("Execute the creative brief", deliverable_type="video")), {"video"})
        self.assertEqual(required_media_types(self.task("完成周五活动宣传", acceptance=["实际可播放视频约10秒"])), {"video"})
        self.assertEqual(required_media_types(self.task("Follow the art direction", acceptance=["Deliver the final poster"])), {"image"})
        self.assertIn("deliverable_type", media_plan_issue(self.task("work", deliverable_type="clip")))
        self.assertIsNone(media_plan_issue(self.task("other", deliverable_type=None)))

    def test_non_generation_work(self):
        for prompt in [
            "准备视频分镜并编写海报文案，禁止生成海报或视频。",
            "开发支持生成视频的工具，用户点击后生成视频。",
            "实现视频播放器和海报生成功能，核对视频时长。",
            "制作视频播放器，交付海报生成器及模板。", "制作海报和视频的模板。",
            "归档现有视频、核验海报和视频。", "生成视频字幕，制作宣传视频的分镜。",
            "Implement a service that allows users to generate videos.",
            "Create a video player. Deliver a storyboard for the promo video.",
            "Build an app that can automatically generate videos.", "测试能否正确生成海报",
            "设计新海报生成流程", "创建用于编辑视频的程序", "输出海报的验收报告",
            "开发名为“生成海报”的按钮。请勿真正生成海报。",
            "Generate a poster template and video captions.", "Do not generate a video.",
            "独立核验实际海报和视频文件，逐条核对已确认活动信息。",
        ]:
            with self.subTest(prompt=prompt):
                self.assertIsNone(media_plan_issue(self.task(prompt)))
        self.assertIsNone(media_plan_issue(self.task(
            "Review the approved creative", review=True, acceptance=["Final playable video is provided."])))
        self.assertIsNotNone(media_plan_issue(self.task("生成海报", role="qa", review=True)))
        self.assertIsNone(media_plan_issue(self.task("做按钮", id="video", write_paths=["media/poster"])))
