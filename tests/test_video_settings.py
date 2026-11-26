import unittest
from unittest.mock import Mock

from media_planning import media_plan_issue, video_confirmation_requested
from model_plugins import PROVIDERS, resolve_route


class VideoSettingsTests(unittest.TestCase):
    def setUp(self):
        self.task = {"id": "clip", "role": "engineer", "prompt": "生成约10秒的宣传视频。",
                     "video_prompt": "9:16竖屏短片。开场镜头2秒，一群人在草地上。",
                     "video_settings": {"duration": 10, "ratio": "9:16"}}

    def route(self, provider, profile=None, **changes):
        selected = {"provider": provider, "model": "example/video"}
        profiles = {"video": {"mode": "video", **selected, **profile}} if profile else {}
        return resolve_route(".", {**self.task, **changes}, isolated=False, config=(profiles, {}),
                             candidate={**selected, "reason": "selected"}, require_key=False)

    def test_each_submit_uses_the_task_settings_and_only_inherits_unspecified_options(self):
        for provider, endpoint, ratio_key in (("openrouter", "/videos", "aspect_ratio"),
                                               ("siliconflow", "/video/submit", "ratio")):
            for profile in (None, {"duration": 5, "ratio": "16:9", "resolution": "480p"}):
                with self.subTest(provider=provider, profile=profile):
                    route = self.route(provider, profile)
                    adapter = PROVIDERS[provider].video
                    api = Mock()
                    adapter.submit(api, adapter.payload(route, self.task["video_prompt"]))
                    api.request.assert_called_once()
                    self.assertEqual(api.request.call_args.args[0], endpoint)
                    payload = api.request.call_args.kwargs["payload"]
                    self.assertEqual(payload["duration"], 10)
                    self.assertEqual(payload[ratio_key], "9:16")
                    if profile:
                        self.assertEqual(payload["resolution"], "480p")
                        self.assertEqual(profile["ratio"], "16:9")

    def test_unsupported_overrides_do_not_revert_to_profile_defaults(self):
        with self.assertRaisesRegex(ValueError, "SiliconFlow video duration"):
            self.route("siliconflow", {"duration": 5}, prompt="生成宣传片", video_prompt="草地",
                       video_settings={"duration": 20})
        for settings in (None, {}, {"duration": True}, {"ratio": "portrait"}, {"duration": -1},
                         {"resolution": []}, {"seconds": 8}, ["duration"]):
            with self.subTest(settings=settings), self.assertRaisesRegex(ValueError, "video_settings"):
                self.route("openrouter", video_settings=settings)
        self.assertIn("video_prompt", media_plan_issue({"video_settings": {"duration": 7}}))

    def test_duration_ratio_and_confirmation_from_the_demo_all_require_repairs(self):
        task = dict(self.task, prompt="按统一视觉生成约10秒正式宣传视频；正式生成需先取得用户生成确认。",
                    video_prompt="约10秒9:16竖屏公司团建宣传短片。正式生成前补齐已确认报名方式，未经确认不得生成。")
        del task["video_settings"]
        issue = media_plan_issue(task)
        for fragment in ("video_settings.duration", "video_settings.ratio", "costly_change", "最终创作提示"):
            self.assertIn(fragment, issue)
        task.update(video_settings={"duration": 10, "ratio": "9:16"}, impact="costly_change")
        self.assertIn("最终创作提示", media_plan_issue(task))
        task["video_prompt"] = "9:16竖屏宣传片。最后展示「报名链接待公布」。"
        self.assertIsNone(media_plan_issue(task))
        self.assertTrue(video_confirmation_requested(task))
        self.assertIn("costly_change", media_plan_issue({**task, "impact": None}))

    def test_contradictory_plan_and_non_output_constraints(self):
        self.assertIn("10 秒", media_plan_issue(dict(self.task, video_settings={"duration": 5, "ratio": "9:16"})))
        self.assertIn("9:16", media_plan_issue(dict(self.task, video_settings={"duration": 10, "ratio": "16:9"})))
        self.assertIn("竖屏", media_plan_issue(dict(self.task, video_prompt="交付竖屏成片",
                                                   video_settings={"duration": 10, "ratio": "1:1"})))
        self.assertIsNone(media_plan_issue(dict(self.task, video_prompt="交付竖屏成片",
                                                video_settings={"duration": 10, "ratio": "3:4"})))
        self.assertIsNone(media_plan_issue({
            "prompt": "按已确认的风格生成视频。", "video_prompt": "已确认设计，开场镜头2秒展示16:9海报。"
            "倒计时5秒后结束。字幕显示活动14:30开始。", "acceptance": ["用户确认成品后发布"]}))
        self.assertIn("10 秒", media_plan_issue({"prompt": "制作约十秒宣传短片", "video_prompt": "阳光草地"}))
        self.assertFalse(video_confirmation_requested({"prompt": "用户已确认本次生成。"}))
        self.assertTrue(video_confirmation_requested({"prompt": "正式生成必须先取得用户确认。"}))
        self.assertTrue(video_confirmation_requested({
            "prompt": "Obtain user approval before generation."}))
