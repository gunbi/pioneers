import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from model_board import ModelBoard
from model_plugins import OpenRouter, check_available, resolve_route
from model_policy import update_policy
from model_worker import run
from openrouter_media import kinds


TEXT = {"id": "vendor/writer", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}
VIDEO = {"id": "vendor/video", "description": "Supports text-to-video and optional reference images.",
         "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["video"]}}


class OpenRouterTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.out = self.root / "result.json"

    def test_public_directory_and_token_boundary(self):
        def respond(req, timeout):
            self.assertIsNone(req.get_header("Authorization"))
            base = OpenRouter.base_url
            if req.full_url == base + "/models?output_modalities=all":
                data = [TEXT, VIDEO, {**TEXT, "id": "~vendor/latest"}, {**TEXT, "id": "vendor/batch",
                        "endpoints": ["/api/v1/batches"]}, {"id": "voice/tts",
                        "architecture": {"input_modalities": ["text"], "output_modalities": ["speech"]}}]
            else:
                self.assertEqual(req.full_url, base + "/videos/models")
                data = [VIDEO, {"id": "video/other"}]
            return io.BytesIO(json.dumps({"data": data}).encode())
        with patch.dict(os.environ, {"SILICONFLOW_API_KEY": "sf-only"}, clear=True), patch(
                "model_plugins.build_opener") as builder:
            builder.return_value.open.side_effect = respond
            board = ModelBoard(self.root / "registry", "test", self.root)
            board.apply({"action": "refresh", "provider": "openrouter"})
            with patch.dict(os.environ, {}, clear=True):
                provider = next(x for x in board.providers() if x["id"] == "openrouter")
            self.assertFalse(provider["connected"])
            self.assertTrue(provider["public_catalog"])
            self.assertEqual({r["id"]: r["kinds"] for r in provider["models"]}, {
                "vendor/writer": ["chat"], "vendor/video": ["video", "text-to-video"], "~vendor/latest": ["chat"],
                "vendor/batch": ["text"], "voice/tts": ["audio"], "video/other": ["video"]})
            self.assertEqual(kinds(VIDEO, {"upscale_factor": {"min": 1}}), ["video"])
            self.assertEqual(kinds({**VIDEO, "description":"Requires reference footage."}, VIDEO | {"description":""}), ["video"])
            with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
                OpenRouter.api_key()

    def test_chat_uses_scoped_catalog_and_records_actual_model(self):
        requested = "openrouter/free"
        with patch.dict(os.environ, {"OPENROUTER_API_KEY":"router-only", "SILICONFLOW_API_KEY":"different"}), patch(
                "model_plugins.build_opener") as builder:
            calls = []
            def respond(req, timeout):
                self.assertEqual(req.get_header("Authorization"), "Bearer router-only")
                calls.append(req.full_url)
                if req.get_method() == "GET":
                    body = {"data": [{**TEXT, "id": requested}]}
                else:
                    self.assertEqual(json.loads(req.data)["model"], requested)
                    body = {"model": "vendor/writer", "choices": [{"message":{"content":"ok"}, "finish_reason":"stop"}],
                            "usage":{"prompt_tokens":6,"completion_tokens":1,"total_tokens":7}}
                return io.BytesIO(json.dumps(body).encode())
            builder.return_value.open.side_effect = respond
            route = {"mode":"text", "provider":"openrouter", "model":requested}
            check_available(route)
            self.assertEqual(run({"route":route, "prompt":"Test only"}, self.root, self.out), 0)
            result = json.loads(self.out.read_text())
            self.assertEqual(result["usage"], {"prompt_tokens":6,"completion_tokens":1,"total_tokens":7})
            self.assertEqual(result["actual_model"], "vendor/writer")
            with self.assertRaisesRegex(ValueError, "unavailable"):
                check_available(dict(route, model="other"))
            self.assertEqual(calls[:2], [OpenRouter.base_url + p for p in ("/models/user", "/chat/completions")])

    def test_scoped_pin_bans_variants_and_routers(self):
        reg = self.root / "registry"
        def policy(action, rev, model):
            request = {"action":action, "revision":rev, "provider":"openrouter", "model":model}
            if action=="select":
                request["mode"]="text"
            return update_policy(reg, request)
        policy("select", 0, "openrouter/free")
        self.assertFalse(policy("ban", 1, "vendor/writer:free")["selected"])
        for model in ("vendor/writer","~vendor/latest","openrouter/free"):
            with self.assertRaisesRegex(ValueError, "禁用"):
                policy("select", 2, model)
        self.assertEqual(policy("select", 2, "unrelated/text")["selected"]["text"]["model"], "unrelated/text")

    def test_foreign_video_template_is_not_used_and_unsupported_options_fail_before_submit(self):
        config=self.root/".pioneers"
        config.mkdir()
        (config/"models.json").write_text(json.dumps({"profiles":{"sf":{"mode":"video","provider":"siliconflow",
                 "model":"sf/video","resolution":"768p"}},"defaults":{"types":{"video":"sf"}}}))
        selected={"selected":{"video":{"provider":"openrouter","model":VIDEO["id"]}}}
        with patch.dict(os.environ,OPENROUTER_API_KEY="test"):
            route=resolve_route(self.root,{"role":"pm","video_prompt":"boat"}, isolated=False, policy=selected)
        self.assertNotIn("resolution",route)
        self.assertNotIn("duration",route)
        with patch.object(OpenRouter, "request", side_effect=[{"data":[dict(VIDEO,supported_durations=[4,8])]},
                                                             {"data":[VIDEO]}]) as request:
            with self.assertRaisesRegex(ValueError, "duration"):
                check_available(dict(route,duration=5))
            self.assertTrue(all("payload" not in c.kwargs for c in request.call_args_list))

    def test_video_recovery_ignores_remote_polling_url_and_preserves_unknown_duration(self):
        job = "job_123"
        content = OpenRouter.base_url + "/videos/" + job + "/content?index=0"
        route = {"mode":"video","provider":"openrouter","model":VIDEO["id"],"ratio":"16:9"}
        request = {"route":route,"video_prompt":"boat","video_receipt":str(self.root/"receipt.json")}
        replies = [{"id":job,"polling_url":"https://other.test/api"}, {"id":job,"status":"in_progress"},
                   TimeoutError("timeout"), {"id":job,"status":"completed","unsigned_urls":[content]}]
        with patch.object(OpenRouter,"request",side_effect=replies) as api, patch("model_video.time.sleep"):
            self.assertEqual(run(request,self.root,self.out),0)
            self.assertEqual(run(request,self.root,self.out),0)
            result=json.loads(self.out.read_text())
            self.assertEqual(result["usage"],{"videos":1})
            self.assertIn("Bearer",result["content"])
            self.assertIn(content,result["content"])
            self.assertEqual([c.args[0] for c in api.call_args_list],["/videos"] + ["/videos/"+job]*3)
            self.assertEqual(api.call_args_list[0].kwargs["payload"],{"model":VIDEO["id"],"prompt":"boat","aspect_ratio":"16:9"})
        with self.assertRaisesRegex(ValueError,"usable"):
            OpenRouter.video.link(OpenRouter,job,{"unsigned_urls":["https://other.test/steal-key"]})

    def test_video_signed_delivery_and_duration(self):
        request={"route":{"mode":"video","provider":"openrouter","model":VIDEO["id"],"duration":8},
                 "video_prompt":"boat","video_receipt":str(self.root/"receipt.json")}
        with patch.object(OpenRouter,"request",side_effect=[{"id":"job"},{"status":"completed","urls":["https://cdn.example/ok"]}]):
            self.assertEqual(run(request,self.root,self.out),0)
            result=json.loads(self.out.read_text())
            self.assertEqual(result["usage"],{"videos":1,"video_seconds":8})
            self.assertIn("临时签名",result["content"])
