import io
import json
import os
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from model_plugins import NoRedirect, ProviderError, check_available, complete, resolve_route


class ProjectRoutes(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.config = self.root / ".pioneers" / "models.json"
        self.config.parent.mkdir()
        self.pm = {"role": "pm"}
        self.engineering = {"role": "engineer", "write_paths": ["main.py"]}
        self.spec = {
            "profiles": {
                "writer": {"provider": "siliconflow", "model": "org/text", "mode": "text"},
                "other": {"provider": "siliconflow", "model": "org/small", "mode": "text"},
                "dev": {"provider": "siliconflow", "model": "org/coder", "mode": "patch",
                        "test_command": ["python3", "-m", "unittest"]},
            },
            "defaults": {"types": {"text": "writer", "patch": "dev"}, "roles": {"pm": "other"}},
        }
        key = patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-token"}, clear=True)
        key.start()
        self.addCleanup(key.stop)

    def configure(self):
        self.config.write_text(json.dumps(self.spec), encoding="utf-8")

    def route(self, task, profile=None, isolated=True):
        return resolve_route(self.root, task, profile, isolated=isolated)

    def test_legacy_and_explicit_host(self):
        self.assertIsNone(self.route(self.engineering))
        with self.assertRaisesRegex(ValueError, "Unknown model profile"):
            self.route(self.pm, "unregistered")
        self.config.write_text("{")
        self.assertIsNone(self.route(self.pm, "host"))
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            self.route(self.pm)

    def test_explicit_then_type_then_role(self):
        self.configure()
        self.assertEqual(self.route(self.pm, "other")["model"], "org/small")
        self.assertEqual(self.route(self.pm)["profile"], "writer")
        self.assertEqual(self.route(self.engineering)["test_command"], ["python3", "-m", "unittest"])
        self.assertIsNone(self.route(self.engineering, "host"))
        del self.spec["defaults"]["types"]
        self.configure()
        self.assertEqual(self.route(self.pm)["profile"], "other")
        self.assertIsNone(self.route(self.engineering))

    def test_host_tasks_and_capability_mismatch(self):
        self.configure()
        for task in ({"role": "qa", "review": True}, {"role": "engineer", "effect": "publish"},
                     {"role": "qa", "tools": ["browser"]}):
            with self.subTest(task=task):
                self.assertIsNone(self.route(task))
                with self.assertRaisesRegex(ValueError, "host executor"):
                    self.route(task, "writer")
        with self.assertRaisesRegex(ValueError, "mode patch"):
            self.route(self.engineering, "writer")
        with self.assertRaisesRegex(ValueError, "mode text"):
            self.route(self.pm, "dev")
        with self.assertRaisesRegex(ValueError, "isolated"):
            self.route(self.engineering, isolated=False)
        with self.assertRaises(ValueError):
            self.route({**self.engineering, "write_paths": ["."]})
        self.spec["profiles"]["dev"]["test_command"] = "python3 -m unittest"
        self.configure()
        with self.assertRaisesRegex(ValueError, "argv"):
            self.route(self.engineering)

    def test_reject_bad_configuration_and_missing_key(self):
        self.configure()
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "SILICONFLOW_API_KEY"):
            self.route(self.pm)
        self.spec["profiles"]["writer"]["provider"] = "unknown"
        self.configure()
        with self.assertRaisesRegex(ValueError, "provider"):
            self.route(self.pm)
        self.spec["profiles"]["writer"]["base_url"] = "https://untrusted.invalid"
        self.configure()
        with self.assertRaisesRegex(ValueError, "profile"):
            self.route(self.pm)
        self.spec["defaults"]["roles"]["qa"] = []
        self.configure()
        with self.assertRaisesRegex(ValueError, "defaults"):
            self.route(self.pm)

    def test_reject_linked_configuration(self):
        self.config.symlink_to(self.root / "other.json")
        with self.assertRaisesRegex(ValueError, "local"):
            self.route(self.pm)


class SiliconFlowAPI(unittest.TestCase):
    route = {"provider": "siliconflow", "model": "org/model"}

    def setUp(self):
        key = patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test-only"}, clear=True)
        key.start()
        self.addCleanup(key.stop)
        builder = patch("model_plugins.build_opener")
        self.builder = builder.start()
        self.addCleanup(builder.stop)
        self.open = self.builder.return_value.open

    def respond(self, data):
        self.open.return_value = io.BytesIO(json.dumps(data).encode("utf-8"))

    def choice(self, reason="stop", content="交付", usage=None):
        return {"choices": [{"message": {"content": content}, "finish_reason": reason}], "usage": usage}

    def test_catalog_preflight_authenticates_and_filters_chat(self):
        self.respond({"object": "list", "data": [{"id": "org/other"}, {"id": "org/model"}]})
        check_available(self.route)
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.siliconflow.cn/v1/models?sub_type=chat")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-only")
        self.respond({"data": []})
        with self.assertRaisesRegex(ProviderError, "unavailable"):
            check_available(self.route)
        self.respond({"message": "error"})
        with self.assertRaisesRegex(ProviderError, "invalid model catalog"):
            check_available(self.route)

    def test_normalized_completion_and_no_redirect(self):
        self.respond(self.choice(usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15,
                                        "cost": 123}))
        self.assertEqual(complete(self.route, "测试"), ("交付", {"prompt_tokens": 12, "completion_tokens": 3,
                                                           "total_tokens": 15}))
        request = self.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.siliconflow.cn/v1/chat/completions")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data)["model"], "org/model")
        self.assertEqual(json.loads(request.data)["messages"], [{"role": "user", "content": "测试"}])
        handler = self.builder.call_args.args[0]
        self.assertIsInstance(handler, NoRedirect)
        self.assertIsNone(handler.redirect_request(request, None, 307, "", {}, "https://untrusted.invalid"))
        self.respond(self.choice())
        self.assertEqual(complete(self.route, "test")[1], {})

    def test_only_video_preflight_filters_for_text_to_video(self):
        self.respond({"data": [{"id": "org/video"}]})
        check_available({"provider": "siliconflow", "model": "org/video", "mode": "video"})
        self.assertTrue(self.open.call_args.args[0].full_url.endswith("/models?sub_type=text-to-video"))

    def test_incomplete_or_invalid_completions(self):
        for response, error in ((self.choice(reason="length"), "truncated"), (self.choice(content=""), "empty"),
                                (self.choice(reason="content_filter"), "did not finish"),
                                ({"choices": []}, "no completion")):
            with self.subTest(response=response):
                self.respond(response)
                with self.assertRaisesRegex(ProviderError, error):
                    complete(self.route, "test")
        self.open.return_value = io.BytesIO(b"non-json proxy error")
        with self.assertRaisesRegex(ProviderError, "invalid JSON"):
            complete(self.route, "test")

    def test_rate_limit_and_connection_errors_are_safe(self):
        for code, hint in ((401, "invalid credential"), (429, "rate limited"), (504, "timed out")):
            with self.subTest(code=code):
                self.open.side_effect = HTTPError("", code, "test-only", {}, io.BytesIO(b"test-only"))
                with self.assertRaisesRegex(ProviderError, hint) as error:
                    complete(self.route, "test")
                self.assertNotIn("test-only", str(error.exception))
        self.open.side_effect = URLError(socket.timeout())
        with self.assertRaisesRegex(ProviderError, "timed out"):
            complete(self.route, "test")
        self.open.side_effect = URLError("test-only")
        with self.assertRaisesRegex(ProviderError, "connection failed"):
            complete(self.route, "test")
