import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from model_worker import run
from siliconflow_media import list_catalog


class SiliconFlowCatalogTest(unittest.TestCase):
    def test_full_list_and_verified_capabilities(self):
        rows = {"/models": ["movie", "writer", "sound", "future", "writer"],
                "/models?sub_type=chat": ["writer"], "/models?type=video": ["movie"],
                "/models?type=audio": ["sound", "writer"]}
        provider = Mock()
        provider.request.side_effect = lambda path, **kw: {"data": [{"id": key} for key in rows.get(path, [])]}
        catalog = {item["id"]: item["kinds"] for item in list_catalog(provider)}
        self.assertEqual(catalog, {"movie": ["video"], "writer": ["chat", "audio"],
                                   "sound": ["audio"], "future": ["other"]})
        self.assertEqual(provider.request.call_args_list[0].args, ("/models",))

    def test_malformed_catalog_cannot_replace_a_valid_cache(self):
        provider = Mock()
        provider.request.return_value = {"error": "unavailable"}
        with self.assertRaisesRegex(ValueError, "catalog"):
            list_catalog(provider)


class SiliconFlowVideoTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.receipt = self.root / "video-job.json"
        self.request = {"route": {"provider": "siliconflow", "model": "video-model", "mode": "video", "duration": 7},
                        "video_prompt": "Clouds pass over a hill", "video_receipt": str(self.receipt)}
        self.output = self.root / "result.json"
        self.provider = Mock()
        registry = patch.dict("model_worker.PROVIDERS", {"siliconflow": self.provider})
        registry.start()
        self.addCleanup(registry.stop)
        self.submission = {"requestId": "job-123"}
        self.completed = {"status": "Succeed", "results": {"videos": [{"url": "https://media.example/clip.mp4"}]}}

    def execute(self, expected):
        self.assertEqual(run(self.request, self.root, self.output), expected)
        return json.loads(self.output.read_text())

    def test_worker_submits_once_and_resumes_known_job(self):
        self.provider.request.side_effect = [self.submission, {"status": "InQueue"}, TimeoutError("timeout"),
                                             {"status": "InProgress"}, self.completed]
        with patch("siliconflow_media.time.sleep"):
            failed = self.execute(1)
            self.assertEqual(failed["video"], {"request_id": "job-123", "state": "InQueue"})
            self.assertNotIn("usage", failed)
            result = self.execute(0)
        self.assertEqual(result["usage"], {"video_seconds": 7, "videos": 1})
        self.assertIn("https://media.example/clip.mp4", result["content"])
        self.assertIn("约一小时", result["content"])
        self.assertEqual(self.execute(0), result)
        self.assertEqual([c.args[0] for c in self.provider.request.call_args_list],
                         ["/video/submit"] + ["/video/status"] * 4)
        self.assertEqual(self.provider.request.call_args_list[0].kwargs["payload"]["prompt"], self.request["video_prompt"])

    def test_uncertain_submission_is_never_replayed(self):
        self.provider.request.side_effect = TimeoutError("timeout")
        self.execute(1)
        self.assertEqual(json.loads(self.receipt.read_text())["state"], "submitting")
        self.assertIn("人工核对", self.execute(1)["error"])
        self.assertEqual(self.provider.request.call_count, 1)

    def test_mismatched_or_invalid_jobs_are_rejected(self):
        self.provider.request.side_effect = [self.submission, TimeoutError("timeout")]
        self.execute(1)
        self.request["route"]["model"] = "different-video"
        self.assertIn("参数不一致", self.execute(1)["error"])
        self.request["route"]["duration"] = True
        self.assertIn("duration", self.execute(1)["error"])
        self.assertEqual(self.provider.request.call_count, 2)

    def test_confirmed_failure_has_no_generated_seconds(self):
        self.provider.request.side_effect = [self.submission, {"status": "Failed", "reason": "sensitive detail"}]
        failure = self.execute(1)
        self.assertEqual(failure["video"], {"request_id": "job-123", "state": "Failed"})
        self.assertNotIn("usage", failure)
        self.assertNotIn("sensitive", failure["error"])
        self.assertEqual(self.execute(1), failure)

    def test_retry_after_link_expiry_queries_existing_job(self):
        self.provider.request.side_effect = [self.submission, self.completed, self.completed]
        with patch("siliconflow_media.time.time", return_value=100):
            self.execute(0)
        with patch("siliconflow_media.time.time", return_value=3900):
            self.execute(0)
        self.assertEqual([c.args[0] for c in self.provider.request.call_args_list],
                         ["/video/submit", "/video/status", "/video/status"])

    def test_unsafe_result_url_is_never_delivered_or_fetched(self):
        self.completed["results"]["videos"][0]["url"] = "file:///private.txt"
        self.provider.request.side_effect = [self.submission, self.completed]
        result = self.execute(1)
        self.assertNotIn("content", result)
        self.assertEqual(result["usage"], {"videos": 1, "video_seconds": 7})
        self.assertEqual(self.provider.request.call_count, 2)
