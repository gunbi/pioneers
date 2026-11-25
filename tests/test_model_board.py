import json
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dashboard import collaboration_flow, dashboard_server, dashboard_snapshot
from iterations import register_run
from model_board import ModelBoard, run_usage
from model_policy import PolicyConflict
from model_plugins import OpenRouter, SiliconFlow
from skill_sources import write_json


class ModelBoardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        public = patch.object(OpenRouter, "public_catalog", False)
        public.start()
        self.addCleanup(public.stop)
        self.root = Path(temp.name)
        self.registry = self.root / "registry"
        self.registry.mkdir()
        self.board = ModelBoard(self.registry, "sample", self.root)

    def test_cache_keeps_last_good_models_and_select_validates_modality(self):
        models = [{"id": "sample/video", "kinds": ["video", "text-to-video"]},
                  {"id": "sample/image-to-video", "kinds": ["video"]}]
        with patch.dict(os.environ, SILICONFLOW_API_KEY="test-key"), patch.object(SiliconFlow, "list_catalog", return_value=models):
            self.board.apply({"action": "refresh", "provider": "siliconflow"})
        select = {"action": "select", "mode": "video", "provider": "siliconflow", "model": "sample/video", "revision": 0}
        self.board.apply(select)
        with self.assertRaises(ValueError):
            self.board.apply({**select, "revision": 1, "mode": "text"})
        with self.assertRaises(ValueError):
            self.board.apply({**select, "revision": 1, "model": "sample/image-to-video"})
        with patch.dict(os.environ, SILICONFLOW_API_KEY="test-key"), patch.object(
                SiliconFlow, "list_catalog", side_effect=RuntimeError("directory unavailable")):
            with self.assertRaisesRegex(RuntimeError, "unavailable"):
                self.board.apply({"action": "refresh", "provider": "siliconflow"})
            snapshot = self.board.snapshot()
        self.assertEqual(snapshot["providers"][0]["models"], models)
        self.assertTrue(snapshot["providers"][0]["error"])
        self.assertNotIn("test-key", json.dumps(snapshot))

    def test_provider_toggle_keeps_model_rules_and_rejects_stale_or_unknown_edits(self):
        self.board.apply({"action": "ban", "provider": "openrouter", "model": "org/one", "revision": 0})
        self.board.apply({"action": "ban_provider", "provider": "openrouter", "revision": 1})

        def providers():
            return [{"id": name, "models": [{"id": model} for model in ("org/one", "org/two", "~dynamic")]}
                    for name in ("openrouter", "siliconflow")]

        with patch.object(self.board, "providers", side_effect=providers):
            snapshot = self.board.snapshot()
            self.assertEqual(snapshot["policy"]["banned_providers"], ["openrouter"])
            router, silicon = snapshot["providers"]
            self.assertTrue(all(row["blocked"] for row in router["models"]))
            self.assertFalse(any(row.get("blocked") for row in silicon["models"]))
            for provider in ("host", "unknown", None, []):
                with self.assertRaises(ValueError):
                    self.board.apply({"action": "ban_provider", "provider": provider, "revision": 2})
            with self.assertRaises(PolicyConflict):
                self.board.apply({"action": "unban_provider", "provider": "openrouter", "revision": 1})
            self.board.apply({"action": "unban_provider", "provider": "openrouter", "revision": 2})
            router = self.board.snapshot()["providers"][0]
            self.assertEqual([row["id"] for row in router["models"] if row.get("blocked")], ["org/one", "~dynamic"])

    def test_dashboard_and_model_usage_show_ownership_separately_from_execution(self):
        run = self.root / "run-handoff"
        run.mkdir()
        register_run(self.registry, run)
        status = run / "status.json"
        native = {"role": "pm", "state": "running", "depends_on": [], "attempt": 2, "lease_expires_at": 1,
                  "executors": {"2": {"provider": "host"}}}
        remote = {"role": "pm", "state": "running", "depends_on": [], "attempt": 1,
                  "executors": {"1": {"provider": "siliconflow", "model": "demo/text", "mode": "text"}}}
        data = {"mode": "host", "tasks": {"native": native, "remote": remote}, "events": [
            {"role": "pm", "state": "interrupted", "task": "native", "attempt": 1}]}
        write_json(status, data)
        snapshot, _ = dashboard_snapshot(status)
        phases = {node["key"]: node.get("execution_phase") for node in collaboration_flow(snapshot)["nodes"]}
        self.assertEqual(phases, {"native.1": None, "native.2": "awaiting_host", "remote.1": "awaiting_start"})
        self.assertEqual(run_usage(self.registry)[0]["state"], "awaiting_start")
        self.assertEqual(json.loads(status.read_text()), data)
        remote["executors"]["1"]["started_at"] = "2026-11-25T01:00:00Z"
        write_json(status, data)
        self.assertEqual(run_usage(self.registry)[0]["state"], "running")

    def test_usage_includes_all_attempts_and_hides_signed_urls(self):
        run = self.root / "run-sample"
        run.mkdir()
        write_json(run / "status.json", {"tasks": {"film": {"attempt": 2, "state": "running", "executors": {
            "1": {"model": "sample/chat", "provider": "siliconflow", "error": "timeout"},
            "2": {"model": "sample/video", "provider": "siliconflow", "mode": "video", "video_generation": 2},
        }}}})
        write_json(run / ".video-film-2.json", {"request_id": "123", "state": "submitted", "url": "private-signed-url"})
        register_run(self.registry, run)
        rows = run_usage(self.registry)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["request_id"], "123")
        self.assertIsNone(rows[0]["total"])
        self.assertEqual(rows[1]["state"], "failed")
        self.assertNotIn("private-signed-url", json.dumps(rows))

    def test_project_actions_check_origin_and_policy_revision(self):
        with dashboard_server(None, 0, control=lambda *args: None, project=(self.registry, "sample", self.root)) as server:
            thread = Thread(target=server.serve_forever)
            thread.start()
            self.addCleanup(thread.join, 3)
            self.addCleanup(server.shutdown)
            origin = "http://127.0.0.1:" + str(server.server_port)
            body = json.dumps({"action": "preference", "value": "优先快速模型", "revision": 0}).encode()
            def post(source):
                request = Request(origin + "/model-action", data=body, headers={"Origin": source, "Content-Type": "application/json"})
                with urlopen(request, timeout=3) as response:
                    return json.load(response)
            with self.assertRaises(HTTPError) as issue:
                post("https://other.example")
            self.assertEqual(issue.exception.code, 403)
            self.assertTrue(post(origin)["ok"])
            with self.assertRaises(HTTPError) as stale:
                post(origin)
            self.assertEqual(stale.exception.code, 409)
