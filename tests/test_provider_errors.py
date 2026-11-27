import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from model_plugins import OpenRouter, SiliconFlow
from model_worker import run


class HTTPDiagnosticsTests(unittest.TestCase):
    def worker_failure(self, provider, status, body, headers):
        response = io.BytesIO(body if isinstance(body, bytes) else json.dumps(body).encode())
        error = HTTPError("https://example.test/?signature=private", status, "private", headers, response)
        request = {"route": {"mode": "text", "provider": "siliconflow" if provider == SiliconFlow else "openrouter",
                             "model": "test/model"}, "prompt": "private example"}
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {provider.key_env: "test-secret-value"}), patch(
                "model_plugins.build_opener") as opener:
            opener.return_value.open.side_effect = error
            output = Path(tmp) / "result.json"
            self.assertEqual(run(request, Path(tmp), output), 1)
            result = json.loads(output.read_text())
        self.assertNotIn("test-secret-value", str(result))
        self.assertNotIn("private", str(result))
        return result

    def test_siliconflow_validation_extracts_safe_fields(self):
        result = self.worker_failure(SiliconFlow, 400, {
            "code": 20012, "message": "Invalid image_size: private example https://example.org/signed",
            "data": "Bearer test-secret-value"}, {"X-SiliconCloud-Trace-Id": "trace-poster-42", "X-Request-Id": "second"})
        self.assertEqual(result["error_code"], "request_rejected")
        self.assertEqual(result["provider_error"], {"http_status": 400, "code": "20012",
                                                   "reason": "image_size: invalid", "trace_id": "trace-poster-42"})

    def test_nested_billing_and_status_classification(self):
        result = self.worker_failure(OpenRouter, 402, {"error": {"code": 402, "message": "Insufficient credits. private",
                                     "metadata": {"limit_source": "openrouter_credits"}}}, {"X-Request-Id": "req-12345"})
        self.assertEqual(result["error_code"], "insufficient_credit")
        self.assertEqual(result["provider_error"]["trace_id"], "req-12345")
        self.assertEqual(result["provider_error"]["code"], "402")
        result = self.worker_failure(SiliconFlow, 503, {"code": 50505, "message": "Model service overloaded"},
                                     {"X-Siliconcloud-Trace-Id": "prefix-test-secret-value"})
        self.assertEqual(result["error_code"], "outcome_unknown")
        self.assertEqual(result["provider_error"]["reason"], "model service overloaded")
        self.assertNotIn("trace_id", result["provider_error"])

    def test_opaque_or_large_errors_and_untrusted_values(self):
        for body in (b"private non-json error", b"x" * 18000, b'"private"', {
                "code": "test-secret-value", "message": "private", "trace_id": "test-secret-value"}):
            details = self.worker_failure(SiliconFlow, 400, body, {"X-Request-Id": "Bearer test-secret-value"})["provider_error"]
            self.assertEqual(details, {"http_status": 400, "reason": "invalid request parameters"})

    def test_symbols_and_descriptions_only_use_known_words(self):
        details = self.worker_failure(OpenRouter, 422, {"error": {"code": "INVALID_IMAGE_SIZE",
                    "param": "image_size", "message": "Required parameter: private"}}, {})["provider_error"]
        self.assertEqual(details["code"], "INVALID_IMAGE_SIZE")
        self.assertEqual(details["reason"], "image_size: required")
        result = self.worker_failure(OpenRouter, 400, {"code": "INVALID_PRIVATE"}, {"X-Request-Id": "trace-sk-test"})
        self.assertNotIn("code", result["provider_error"])
        self.assertNotIn("trace_id", result["provider_error"])
