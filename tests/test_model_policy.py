from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_plugins import public_profiles, resolve_route
from model_policy import PolicyConflict, read_policy, update_policy


class ModelPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = self.root / "iterations"
        self.ref = {"provider": "siliconflow", "model": "example/v1"}

    def test_optimistic_writes_and_project_isolation(self):
        self.assertEqual(read_policy(self.registry)["revision"], 0)
        self.assertFalse(self.registry.exists())

        def worker(value):
            try:
                return update_policy(self.registry, {"action": "preference", "revision": 0, "value": value})["revision"]
            except PolicyConflict:
                return None

        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(pool.map(worker, ("文案", "快速")), [1, None])
        self.assertEqual(read_policy(self.registry)["revision"], 1)
        self.assertEqual(read_policy(self.root / "other")["preference"], "")

    def test_ban_invalidates_pinned_aliases_and_preserves_other_rules(self):
        def change(action, **fields):
            revision = read_policy(self.registry)["revision"]
            return update_policy(self.registry, {"action": action, "revision": revision, **fields})
        change("select", mode="text", **self.ref)
        change("select", mode="patch", **self.ref)
        change("select", mode="video", provider="siliconflow", model="example/video")
        self.assertEqual(list(change("ban", **self.ref)["selected"]), ["video"])
        with self.assertRaises(ValueError):
            change("select", mode="text", **self.ref)
        self.assertEqual(change("unban", **self.ref)["banned"], [])
        self.assertEqual(change("clear", mode="video")["selected"], {})

    @patch.dict(os.environ, SILICONFLOW_API_KEY="mock")
    def test_pin_without_profiles_and_trusted_patch_command(self):
        policy = {"selected": {"text": self.ref, "patch": self.ref, "video": self.ref}}
        video = resolve_route(self.root, {"role": "engineer", "video_prompt": "a sailboat"}, isolated=False, policy=policy)
        self.assertEqual((video["mode"], video["duration"]), ("video", 5))
        task = {"role": "engineer", "write_paths": ["main.py"]}
        with self.assertRaisesRegex(ValueError, "test_command"):
            resolve_route(self.root, task, isolated=True, policy=policy)
        profiles = {"code": {"provider": "siliconflow", "model": "other/code", "mode": "patch",
                             "test_command": ["python3", "-m", "unittest"]}}
        (self.root / ".pioneers").mkdir()
        (self.root / ".pioneers/models.json").write_text(json.dumps({"profiles": profiles}))
        route = resolve_route(self.root, task, isolated=True, policy=policy)
        self.assertEqual((route["model"], route["test_command"]), ("example/v1", ["python3", "-m", "unittest"]))
        self.assertNotIn("test_command", public_profiles(self.root)[0])
        self.assertIsNone(resolve_route(self.root, {"role": "qa", "review": True}, isolated=False, policy=policy))

    @patch.dict(os.environ, SILICONFLOW_API_KEY="mock")
    def test_preference_and_explicit_selection(self):
        profiles = {name: {"provider": "siliconflow", "model": "org/" + name, "mode": "text", "tags": tags}
                    for name, tags in (("fast", ["快速"]), ("edit", ["文案"]))}
        (self.root / ".pioneers").mkdir()
        (self.root / ".pioneers/models.json").write_text(json.dumps({"profiles": profiles, "defaults": {"types": {"text": "edit"}}}))
        task = {"role": "pm"}
        def route(policy, profile=None):
            return resolve_route(self.root, task, profile, isolated=False, policy=policy)
        self.assertEqual(route({"preference": "写作时优先快速"})["profile"], "fast")
        self.assertEqual(route({"preference": "不要使用快速"})["profile"], "edit")
        self.assertIn("未唯一匹配", route({"preference": "节省预算"})["reason"])
        self.assertEqual(route({"preference": "快速"}, "edit")["profile"], "edit")
        self.assertEqual(route({"selected": {"text": self.ref}}, "edit")["model"], "example/v1")
        with self.assertRaisesRegex(ValueError, "禁用"):
            route({"banned": [{"provider": "siliconflow", "model": "org/fast"}]}, "fast")
