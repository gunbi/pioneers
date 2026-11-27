import base64
import io
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from media_adapters import music_result
from model_stream import SIZE_DEFAULTS, read_completion, response_lines
from model_worker import run as run_worker
from model_timeouts import worker_limit
from pioneers import model_supervisor_environment
from model_plugins import OpenRouter, SiliconFlow
from provider_errors import ProviderError


def sse(*events):
    return b"".join(b"data: " + (event if isinstance(event, bytes) else json.dumps(event).encode()) + b"\n\n"
                    for event in events)


class StreamingCompletionTest(unittest.TestCase):
    def complete_once(self, body, **limits):
        env = {"SILICONFLOW_API_KEY": "test",
               **{"PIONEERS_MODEL_STREAM_" + key + "_BYTES": str(value) for key, value in limits.items()}}
        with patch.dict(os.environ, env, clear=True), patch("model_plugins.build_opener") as opener:
            opener.return_value.open.return_value = body if hasattr(body, "read1") else io.BytesIO(body)
            try:
                return SiliconFlow.complete("org/model", "test")
            finally:
                opener.return_value.open.assert_called_once()

    def test_worker_delivers_short_result_after_more_than_eight_mib_of_reasoning(self):
        reasoning = sse({"choices": [{"delta": {"reasoning_content": "x" * 65536, "content": ""}}]})
        wire = reasoning * 128 + sse({"choices": [{"delta": {"content": "交付"}, "finish_reason": "stop"}]},
                                     {"choices": [], "usage": {"total_tokens": 345}}, b"[DONE]")
        self.assertGreater(len(wire), 8 * 1024 * 1024)
        route = {"provider": "siliconflow", "mode": "text", "model": "org/model"}
        with TemporaryDirectory() as temp, patch.dict(os.environ, {"SILICONFLOW_API_KEY": "test"}, clear=True), patch(
                "model_plugins.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(wire)
            output = Path(temp) / "result.json"
            self.assertEqual(run_worker({"route": route, "prompt": "test"}, Path(temp), output), 0)
            self.assertEqual(json.loads(output.read_text()), {"content": "交付", "usage": {"total_tokens": 345}})
            opener.return_value.open.assert_called_once()

    def test_transport_boundary_includes_comments_and_envelopes(self):
        secret = b"private comment"
        wire = b": " + secret + b"\n\n" + sse({"choices": [{"delta": {"content": "a"}, "finish_reason": "stop"}]},
                                             b"[DONE]")
        self.assertEqual(self.complete_once(wire, TRANSPORT=len(wire)), ("a", {}))
        with self.assertRaisesRegex(ProviderError, f"transport.*{len(wire)} bytes, limit {len(wire)-1} bytes") as err:
            self.complete_once(wire, TRANSPORT=len(wire)-1)
        self.assertNotIn(secret.decode(), str(err.exception))

    def test_event_limit_counts_fields_and_fragments_across_reads(self):
        # A single event containing two data lines and a metadata field.
        event = 'id: private\r\ndata: {"choices":\r\ndata: [{"delta":{"content":"👍"},"finish_reason":"stop"}]}\r\n'.encode()
        wire = event + b"\r\ndata: [DONE]\r\n"
        class TinyReads(io.BytesIO):
            def read1(self, size):
                return super().read1(min(size, 1))
        self.assertEqual(self.complete_once(TinyReads(wire), EVENT=len(event), CONTENT=4), ("👍", {}))
        with self.assertRaisesRegex(ProviderError, f"event.*{len(event)} bytes, limit {len(event)-1} bytes") as err:
            self.complete_once(TinyReads(wire), EVENT=len(event)-1)
        self.assertNotIn("private", str(err.exception))

    def test_unterminated_event_is_rejected_without_reading_the_rest(self):
        response = Mock()
        response.read1.side_effect = [b"data: {}\n", b"data: private", AssertionError("read past event limit")]
        lines = response_lines(response, 1000, started=0, first=100, idle=100, total=100, event_bytes=16)
        with patch("model_stream.time.monotonic", return_value=0), self.assertRaisesRegex(
                ProviderError, "event.*22 bytes, limit 16 bytes"):
            read_completion(lines)
        self.assertEqual(response.read1.call_count, 2)

    def test_unclassified_whitespace_is_bounded_across_crlf_fragments(self):
        response = Mock()
        response.read1.side_effect = [b"\r", b"\n", b" \r", b"\n", b"      ", AssertionError("unbounded read")]
        lines = response_lines(response, 1000, started=0, first=100, idle=100, total=100, event_bytes=8)
        with patch("model_stream.time.monotonic", return_value=0), self.assertRaisesRegex(
                ProviderError, "event.*9 bytes, limit 8 bytes"):
            read_completion(lines)

    def test_content_limit_counts_only_visible_utf8_bytes_in_sse_and_plain_json(self):
        wire = sse({"choices": [{"delta": {"content": "一"}}, {"index": 1, "delta": {"content": "other choice"}}]},
                   {"choices": [{"delta": {"reasoning_content": "private", "content": ""}}]},
                   {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]},
                   {"usage": {"prompt_tokens": 2, "unneeded": 1000}}, b"[DONE]")
        document = {"choices": [{"message": {"content": "一好", "reasoning_content": "private"},
                                 "finish_reason": "stop"}], "usage": {"prompt_tokens": 2}}
        for body in (wire, json.dumps(document, indent=2, ensure_ascii=False).encode()):
            self.assertEqual(self.complete_once(body, CONTENT=6), ("一好", {"prompt_tokens": 2}))
            with self.assertRaisesRegex(ProviderError, "content.*6 bytes, limit 5 bytes") as err:
                self.complete_once(body, CONTENT=5)
            self.assertNotIn("一", str(err.exception))
        # The provider may answer with an unframed document longer than an SSE event.
        self.assertEqual(self.complete_once(json.dumps(document).encode(), EVENT=1), ("一好", {"prompt_tokens": 2}))

    def test_invalid_size_settings_fail_before_post(self):
        for name in SIZE_DEFAULTS:
            with self.subTest(name=name), patch.dict(os.environ, {name: "0"}, clear=True), patch(
                    "model_plugins.build_opener") as opener:
                with self.assertRaisesRegex(ValueError, name):
                    SiliconFlow.complete("org/model", "test")
                opener.assert_not_called()

    def test_music_can_receive_a_large_base64_event_on_its_own_media_budget(self):
        audio = b"a" * (800 * 1024)
        body = sse({"choices": [{"delta": {"audio": {"data": base64.b64encode(audio).decode()}}}]}, b"[DONE]")
        self.assertGreater(len(body), SIZE_DEFAULTS["PIONEERS_MODEL_STREAM_EVENT_BYTES"])
        env = {"OPENROUTER_API_KEY": "test", **{key: "1" for key in SIZE_DEFAULTS}}
        with patch.dict(os.environ, env, clear=True), patch("model_plugins.build_opener") as opener:
            opener.return_value.open.return_value = io.BytesIO(body)
            self.assertEqual(music_result(OpenRouter, "org/music", "test"), (audio, {"audio_files": 1}))
            opener.return_value.open.assert_called_once()

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
                    "PIONEERS_MODEL_STREAM_IDLE_SECONDS": "20", "PIONEERS_OTHER_SECRET": "private",
                    **{name: str(default + 1) for name, default in SIZE_DEFAULTS.items()}}
        with patch.dict(os.environ, settings, clear=True):
            env = model_supervisor_environment("siliconflow")
            self.assertNotIn("PIONEERS_OTHER_SECRET", env)
            self.assertEqual(env["PIONEERS_MODEL_STREAM_IDLE_SECONDS"], "20")
            for name in SIZE_DEFAULTS:
                self.assertEqual(env[name], settings[name])
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
