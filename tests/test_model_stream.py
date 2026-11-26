import io
import json
import os
import unittest
from unittest.mock import Mock, patch

from model_stream import read_completion, response_lines
from model_timeouts import worker_limit
from pioneers import model_supervisor_environment
from model_plugins import OpenRouter, SiliconFlow
from provider_errors import ProviderError


def sse(*events):
    return b"".join(b"data: " + (event if isinstance(event, bytes) else json.dumps(event).encode()) + b"\n\n"
                    for event in events)


class StreamingCompletionTest(unittest.TestCase):
    def test_single_stream_delivers_visible_text_actual_model_and_trailing_usage(self):
        body = b": keepalive\r\n\r\n" + sse(
            {"model": "org/actual", "choices": [{"delta": {"reasoning_content": "private"}}]},
            {"choices": [{"delta": {"content": "第一段"}}]},
            {"choices": [{"delta": {"content": "、第二段"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"total_tokens": 17}}, b"[DONE]")
        for provider in (OpenRouter, SiliconFlow):
            with self.subTest(provider=provider.name), patch.dict(os.environ, {provider.key_env: "test"}), patch(
                    "model_plugins.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(body)
                content, usage = provider.complete("org/request", "test")
                self.assertEqual(content, "第一段、第二段")
                self.assertEqual(usage["total_tokens"], 17)
                if provider is OpenRouter:
                    self.assertEqual(usage["actual_model"], "org/actual")
                opener.return_value.open.assert_called_once()
                request = opener.return_value.open.call_args
                self.assertTrue(json.loads(request.args[0].data)["stream"])
                self.assertEqual(request.kwargs["timeout"], 300)

    def test_distinct_first_idle_and_total_budgets_on_partial_lines(self):
        response = Mock()
        response.read1.side_effect = [
            b": processing\n\n", b"da", b'ta: {"choices":[{"delta":{"content":"hi"}}]}\n\n',
            b'data: {"choices":[{"finish_reason":"stop"}]}\n\n', b"data: [DONE]\n\n"]
        with patch("model_stream.time.monotonic", side_effect=[0, 125, 130, 200, 810]):
            lines = response_lines(response, 500, started=0, first=300, idle=120, total=900)
            body = read_completion(lines)
        self.assertEqual(body["choices"][0]["message"]["content"], "hi")
        waits = [call.args[0] for call in response.fp.raw._sock.settimeout.call_args_list]
        self.assertEqual(waits, [300, 175, 170, 120, 90])

    def test_keepalives_cannot_extend_total_deadline_or_hide_incomplete_stream(self):
        response = Mock()
        response.read1.side_effect = [b'data: {"choices":[]}\n\n', b": keepalive\n\n"]
        with patch("model_stream.time.monotonic", side_effect=[0, 42, 901]):
            lines = response_lines(response, 500, started=0, first=300, idle=120, total=900)
            with self.assertRaisesRegex(ProviderError, "total time"):
                read_completion(lines)

    def test_worker_passes_only_allowed_overrides_and_reserves_patch_test_time(self):
        settings = {"PIONEERS_MODEL_TOTAL_SECONDS": "400", "PIONEERS_MEDIA_TOTAL_SECONDS": "1400",
                    "PIONEERS_MODEL_STREAM_IDLE_SECONDS": "20", "PIONEERS_OTHER_SECRET": "private"}
        with patch.dict(os.environ, settings, clear=True):
            env = model_supervisor_environment("siliconflow")
            self.assertNotIn("PIONEERS_OTHER_SECRET", env)
            self.assertEqual(env["PIONEERS_MODEL_STREAM_IDLE_SECONDS"], "20")
            self.assertEqual([worker_limit(mode) for mode in ("text", "patch", "video", "image", "speech", "music")],
                             [400, 520, 1400, 1400, 1400, 1400])
        with patch.dict(os.environ, {"PIONEERS_MODEL_STREAM_IDLE_SECONDS": "0"}):
            with self.assertRaisesRegex(ValueError, "PIONEERS_MODEL_STREAM_IDLE_SECONDS"):
                worker_limit("text")

    def test_partial_or_unsuccessful_stream_never_reposts_or_delivers(self):
        part = {"choices": [{"delta": {"content": "partial"}}]}
        stop = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        for stream in (sse(part, stop), sse(part, b"[DONE]"), sse(part, {"error": {"message": "secret"}}, b"[DONE]"),
                       sse(part, {"choices": [{"delta": {}, "finish_reason": "length"}]}, b"[DONE]")):
            with self.subTest(stream=stream), patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test"}), patch(
                    "model_plugins.build_opener") as opener:
                opener.return_value.open.return_value = io.BytesIO(stream)
                with self.assertRaises(ProviderError) as err:
                    SiliconFlow.complete("org/test", "test")
                self.assertNotIn("secret", str(err.exception))
                opener.return_value.open.assert_called_once()
