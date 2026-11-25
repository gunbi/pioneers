import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from generated_media import media_job, receipt_path
from model_plugins import OpenRouter, SiliconFlow, shared_preflight
from model_video import video_job
from model_worker import run
from provider_errors import InsufficientCreditError, ProviderError


class BillingErrorsTest(unittest.TestCase):
    def test_authenticated_billing_response_and_transient_lookalikes(self):
        def body(message, **metadata):
            return json.dumps({"error": {"code": 402, "message": message, "metadata": metadata}}).encode()

        insufficient = body("Insufficient credits. SECRET")
        cases = [
            (OpenRouter, 402, insufficient, False, True),
            (SiliconFlow, 402, b"", False, True),
            (OpenRouter, 402, insufficient, True, False),
            (OpenRouter, 429, insufficient, False, False),
            (SiliconFlow, 403, insufficient, False, False),
            (OpenRouter, 402, body("Insufficient credits", limit_source="openrouter_in_flight_budget"), False, False),
            (OpenRouter, 402, body("Insufficient credits", limit_source="openrouter_key"), False, False),
            (OpenRouter, 402, body("Insufficient credits, try fewer max_tokens"), False, False),
            (OpenRouter, 402, body("Insufficient credits for this API key"), False, False),
            (OpenRouter, 402, b"invalid JSON", False, False),
        ]
        for provider, status, payload, public, disabled in cases:
            with self.subTest(provider=provider.name, status=status, payload=payload, public=public):
                with patch.dict(os.environ, {provider.key_env: "example"}), patch("model_plugins.build_opener") as opener:
                    opener.return_value.open.side_effect = HTTPError("", status, "SECRET", {}, io.BytesIO(payload))
                    with shared_preflight():
                        for _ in range(2):
                            with self.assertRaises(ProviderError) as result:
                                provider.request("/models", public=public, timeout=1)
                            self.assertEqual(isinstance(result.exception, InsufficientCreditError), disabled)
                            self.assertNotIn("SECRET", str(result.exception))
                    opener.return_value.open.assert_called_once()

    def test_worker_emits_account_code_only_for_confirmed_billing(self):
        with TemporaryDirectory() as temp:
            output = Path(temp) / "result.json"
            request = {"route": {"provider": "openrouter", "mode": "text"}, "prompt": "hello"}
            for kind in (InsufficientCreditError, ProviderError):
                with patch("model_worker.complete", side_effect=kind("OpenRouter HTTP 402: insufficient credits")):
                    self.assertEqual(run(request, Path(temp), output), 1)
                    result = json.loads(output.read_text())
                    self.assertEqual(result.get("error_code") == "insufficient_credit", kind is InsufficientCreditError)

    def test_media_and_video_release_only_definitively_rejected_submissions(self):
        route = {"provider": "openrouter", "model": "demo", "mode": "image"}
        with TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            video_receipt = root / "video.json"
            adapter = Mock()
            adapter.video.payload.return_value = {"model": "demo"}
            for error in (InsufficientCreditError("billing"), TimeoutError("unknown")):
                with patch("generated_media.generate", side_effect=error), self.assertRaises(type(error)):
                    media_job(adapter, route, "hello", root, "image", 1)
                adapter.video.submit.side_effect = error
                result = {}
                with self.assertRaises(type(error)):
                    video_job(adapter, route, "hello", video_receipt, result)
                ambiguous = isinstance(error, TimeoutError)
                self.assertEqual(receipt_path(root, "image", 1).exists(), ambiguous)
                self.assertEqual(video_receipt.exists(), ambiguous)
                self.assertEqual("video" in result, ambiguous)
