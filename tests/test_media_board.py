from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from dashboard import dashboard_server
from iterations import register_run
from model_board import ModelBoard, run_usage
from model_policy import read_policy
from skill_sources import write_json


class MediaBoardTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = self.root / "registry"
        self.run = self.root / "run-one"
        self.run.mkdir()
        (self.run / "media").mkdir()
        self.name = "voice.1.mp3"
        self.content = b"ID3audio-bytes"
        (self.run / "media" / self.name).write_bytes(self.content)
        self.media = {"filename": self.name, "mime": "audio/mpeg", "bytes": len(self.content)}
        self.executor = {"model": "sample/voice", "provider": "openrouter", "mode": "speech",
                         "media": self.media, "usage": {"audio_files": 1}}
        self.save()
        register_run(self.registry, self.run)

    def save(self):
        write_json(self.run / "status.json", {"tasks": {"voice": {"state": "completed", "attempt": 2,
                   "executors": {"1": self.executor, "2": self.executor}}}})

    def start(self, project):
        server = dashboard_server(None if project else self.run, 0,
                                  project=(self.registry, "sample", self.root) if project else None)
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return "http://127.0.0.1:" + str(server.server_port)

    def test_scoped_delivery_and_audio_ranges(self):
        for project in (True, False):
            with self.subTest(project=project):
                base = self.start(project)
                url = base + ("/runs/run-one" if project else "") + "/media/" + self.name
                with urlopen(url, timeout=3) as response:
                    self.assertEqual(response.read(), self.content)
                    self.assertEqual(response.headers["Content-Type"], self.media["mime"])
                    self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
                with urlopen(Request(url, headers={"Range": "bytes=2-6"}), timeout=3) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.read(), self.content[2:7])
                    self.assertEqual(response.headers["Content-Range"], f"bytes 2-6/{len(self.content)}")
                if project:
                    with self.assertRaises(HTTPError) as error:
                        urlopen(url.replace("run-one", "run-other"), timeout=3)
                    self.assertEqual(error.exception.code, 404)

    def test_media_requires_valid_file_and_matching_receipt(self):
        base = self.start(False)
        def denied(name):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/media/" + name, timeout=3)
            self.assertEqual(error.exception.code, 404)
        denied("undeclared.1.mp3")
        self.media["mime"] = "text/html"
        self.save()
        denied(self.name)
        self.media["mime"] = "audio/mpeg"
        self.media["bytes"] += 1
        self.save()
        denied(self.name)
        self.media["bytes"] -= 1
        self.save()
        outside = self.root / "external.mp3"
        outside.write_bytes(self.content)
        target = self.run / "media" / self.name
        target.unlink()
        target.symlink_to(outside)
        denied(self.name)
        target.unlink()
        directory = self.run / "media"
        directory.rmdir()
        directory.symlink_to(self.root, target_is_directory=True)
        (self.root / self.name).write_bytes(self.content)
        denied(self.name)

    def test_usage_points_recovered_attempts_at_same_file(self):
        rows = run_usage(self.registry)
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["media"], self.media)
            self.assertEqual(row["audio_files"], 1)
            self.assertIsNone(row["images"])

    def test_project_select_checks_each_generation_kind(self):
        board = ModelBoard(self.registry, "test", self.root)
        cases = {"image": "text-to-image", "speech": "text-to-speech",
                 "music": "text-to-music", "sound_effect": "text-to-sound-effect"}
        write_json(self.registry / "model-catalog-openrouter.json",
                   {"models": [{"id": "org/" + mode, "kinds": [tag]} for mode, tag in cases.items()]})
        for revision, mode in enumerate(cases):
            choice = {"action": "select", "provider": "openrouter", "mode": mode, "revision": revision}
            with self.assertRaises(ValueError):
                board.apply({**choice, "model": "org/" + ("image" if mode != "image" else "music")})
            board.apply({**choice, "model": "org/" + mode})
        self.assertEqual(set(read_policy(self.registry)["selected"]), set(cases))
