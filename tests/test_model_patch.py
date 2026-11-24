import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from model_patch import apply_patch_result, patch_prompt, validate_paths


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


class ModelPatchTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "--quiet")
        (self.repo / "message.txt").write_bytes(b"before\n")
        git(self.repo, "add", "message.txt")
        git(self.repo, "-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "base")
        self.reply = json.dumps({"files":{"message.txt":"after\n"}})

    def test_explicit_regular_files_only(self):
        private = self.root / "private"
        private.write_text("must stay local")
        (self.repo / "link").symlink_to(private)
        (self.repo / "jump").symlink_to(self.root)
        (self.repo / "src").mkdir()
        for name in (".", "../private", "src", "link", "jump/private", ".git/config", ".env.prod",
                     ".pioneers/models.json", "service-account-credentials.json", "key.p12", "src//code"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                patch_prompt(self.repo, [name])
        self.assertIn("before", patch_prompt(self.repo, ["message.txt", "src/new.txt"]))
        (self.repo / "local-config.json").write_text("private local settings")
        with self.assertRaisesRegex(ValueError, "tracked"):
            patch_prompt(self.repo, ["local-config.json"])
        with self.assertRaises(ValueError):
            validate_paths(self.repo, ["message.txt", "MESSAGE.txt"])

    def test_untrusted_patch_rejected_before_any_worktree_change(self):
        original = git(self.repo, "rev-parse", "HEAD")
        for reply in ({"files":{"outside":"evil"}}, {"files":{"message.txt":"ok"},"command":["sh"]},
                      {"files":{"message.txt":None}}, {"files":{"message.txt":"before\n"}}):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                apply_patch_result(self.repo, ["message.txt"], json.dumps(reply), ["true"])
        self.assertEqual(original, git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual("", git(self.repo, "status", "--porcelain"))

    def test_sandbox_failure_preserves_worktree(self):
        with patch("model_patch._test", side_effect=ValueError("test failed")), self.assertRaises(ValueError):
            apply_patch_result(self.repo, ["message.txt"], self.reply, ["false"])
        self.assertEqual((self.repo / "message.txt").read_text(), "before\n")
        self.assertFalse(git(self.repo, "status", "--porcelain"))

    def test_branch_change_fences_patch_before_tests(self):
        checkout = {"base": git(self.repo, "rev-parse", "HEAD"), "branch": git(self.repo, "branch", "--show-current")}
        git(self.repo, "checkout", "-qb", "other-task")
        with patch("model_patch._test") as test, self.assertRaisesRegex(ValueError, "branch or base"):
            apply_patch_result(self.repo, ["message.txt"], self.reply, ["true"], checkout=checkout)
        test.assert_not_called()
        self.assertEqual((self.repo / "message.txt").read_text(), "before\n")

    def test_only_requested_content_committed(self):
        reply = json.dumps({"files":{"message.txt":"after\n", "new/utf8.txt":"中文\r\n"}})
        def verify(snapshot, tmp, command, *, supervised):
            self.assertEqual(command, ["project-test"])
            self.assertEqual((snapshot / "message.txt").read_bytes(), b"after\n")
            self.assertEqual((snapshot / "new/utf8.txt").read_bytes(), "中文\r\n".encode())
            self.assertFalse((snapshot / ".git").exists())
            (snapshot / "message.txt").write_bytes(b"test side effect")
        with patch("model_patch._test", side_effect=verify):
            summary = apply_patch_result(self.repo, ["message.txt", "new/utf8.txt"],
                                         "```json\n" + reply + "\n```", ["project-test"])
        self.assertIn(git(self.repo, "rev-parse", "HEAD"), summary)
        self.assertEqual((self.repo / "message.txt").read_text(), "after\n")
        self.assertFalse(git(self.repo, "status", "--porcelain"))
        self.assertEqual(git(self.repo, "rev-list", "--count", "HEAD"), "2")

    def test_missing_sandbox_is_an_error(self):
        with patch("model_patch.sys.platform", "win32"), self.assertRaisesRegex(ValueError, "assign this task"):
            apply_patch_result(self.repo, ["message.txt"], self.reply, ["true"])

    @unittest.skipUnless(os.environ.get("PIONEERS_TEST_OS_SANDBOX") == "1",
                         "enable real OS sandbox tests on a supported host")
    def test_real_sandbox(self):
        outside = self.root / "private"
        outside.write_text("private")
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        test = [
            sys.executable, "-c",
            "import os, socket\nfrom pathlib import Path\n"
            "assert 'SILICONFLOW_API_KEY' not in os.environ\n"
            "assert Path('message.txt').read_bytes() == b'after\\n'\n"
            "for op in [lambda: Path(" + repr(str(outside)) + ").read_bytes(), "
            "lambda: Path(" + repr(str(outside)) + ").write_text('changed'), "
            "lambda: socket.create_connection(" + repr(listener.getsockname()) + ", timeout=1)]:\n"
            " try: op()\n except OSError: continue\n raise AssertionError('sandbox escape')",
        ]
        with patch.dict(os.environ, SILICONFLOW_API_KEY="fake"):
            result = apply_patch_result(self.repo, ["message.txt"], self.reply, test)
        self.assertEqual(outside.read_text(), "private")
        self.assertIn("exit 0", result)
