import hashlib
import json
import unittest
from unittest.mock import Mock

from generated_media import fingerprint
from media_adapters import generate
from media_brief import poster_prompt
from media_planning import media_plan_issue
from model_plugins import resolve_route


MOSS = "OpenMOSS-Team/MOSS-Image"


class ImageSettingsTest(unittest.TestCase):
    def task(self, brief, **options):
        return {"id": "poster", "role": "engineer", "prompt": "制作预热海报", "tools": [],
                "media": {"type": "image", "prompt": brief, **options}}

    def route(self, task, model=MOSS, provider="siliconflow"):
        return resolve_route(".", task, isolated=False, require_key=False, config=({}, {}),
                             candidate={"provider": provider, "model": model, "reason": "test"})

    def test_saved_legacy_creative_ratio_reaches_paid_post(self):
        brief = "制作一张完整3:4竖版手机公司群活动预热海报。活动时间待定。"
        route = self.route(self.task(brief))
        self.assertEqual(route["image_size"], "960x1280")
        provider = Mock(name="test provider")
        provider.name = "SiliconFlow"
        provider.request.side_effect = [{"data": [{"id": MOSS}]}, RuntimeError("captured generation")]
        with self.assertRaisesRegex(RuntimeError, "captured generation"):
            generate(provider, route, brief + "\n\n请将以下已确认的公开文案原样呈现在海报上：\n16:9 横版海报")
        path, = provider.request.call_args.args
        self.assertEqual(path, "/images/generations")
        self.assertEqual(provider.request.call_args.kwargs["payload"]["image_size"], "960x1280")

    def test_unstarted_legacy_request_ignores_ratios_in_released_public_text(self):
        provider = Mock()
        provider.name = "SiliconFlow"
        provider.request.side_effect = [{"data": [{"id": MOSS}]}, RuntimeError("captured generation")]
        route = {"provider": "siliconflow", "model": MOSS, "mode": "image"}
        with self.assertRaisesRegex(RuntimeError, "captured generation"):
            generate(provider, route, "绿色海报\n\n请将以下已确认的公开文案原样呈现在海报上：\n3:4竖版海报设计展")
        self.assertEqual(provider.request.call_args.kwargs["payload"]["image_size"], "1024x1024")

    def test_model_specific_sizes_and_validation(self):
        for model, ratio, size in [(MOSS, "16:9", "1280x720"), (MOSS, "4:5", "1024x1280"),
                                   (MOSS, "5:4", "1280x1024"), ("Qwen/Qwen-Image", "3:4", "1140x1472"),
                                   ("Kwai-Kolors/Kolors", "9:16", "720x1280")]:
            with self.subTest(model=model):
                task = self.task("Green artwork", aspect_ratio=ratio)
                self.assertIsNone(media_plan_issue(task))
                self.assertEqual(self.route(task, model)["image_size"], size)
        self.assertEqual(self.route(self.task("A landscape painting of a meadow at 15:30."))["image_size"], "1024x1024")
        self.assertEqual(self.route(self.task("竖版海报"))["image_size"], "960x1280")
        self.assertIn("aspect_ratio", media_plan_issue(self.task("3:4竖版海报", aspect_ratio="16:9")))
        self.assertIn("aspect_ratio", media_plan_issue(self.task("A poster", aspect_ratio=None)))
        with self.assertRaisesRegex(ValueError, "画幅"):
            self.route(self.task("Draw a poster", aspect_ratio="23:1"), "Kwai-Kolors/Kolors")
        self.assertNotIn("image_size", self.route(self.task("3:4竖版海报"), "some/image", "openrouter"))
        self.assertIn("画幅：3:4", poster_prompt(self.task("Green artwork", aspect_ratio="3:4"), {}, None))

    def test_new_size_is_part_of_receipt_and_old_digest_still_matches(self):
        route = {"provider": "siliconflow", "model": MOSS, "mode": "image"}
        legacy = json.dumps(["siliconflow", MOSS, "image", None, "brief"], ensure_ascii=False).encode()
        self.assertEqual(fingerprint(route, "brief"), hashlib.sha256(legacy).hexdigest())
        self.assertNotEqual(fingerprint(dict(route, image_size="960x1280"), "brief"),
                            fingerprint(dict(route, image_size="1280x960"), "brief"))
