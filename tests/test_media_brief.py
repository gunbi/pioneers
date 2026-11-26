import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from media_brief import MediaBriefError, poster_plan_issue, poster_prompt


class PosterBriefTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.run = Path(self.directory.name)
        self.task = {"id": "poster", "media": {"type": "image", "prompt": "纸感插画；保留标题与文案区。"},
                     "depends_on": ["facts", "design"], "media_copy_from": "facts"}
        self.document = ("内部招募测试账户十人，请勿对外。\n"
                         "```media-copy\n周五出逃计划\n活动时间、地点待定\n报名链接待公布\n```\n"
                         "内部测试链接 https://example.invalid/private")
        self.save(self.document)

    def save(self, document, *, state="approved", attempt=1):
        evidence = "facts.md" if attempt == 1 else f"facts.{attempt}.md"
        path = self.run / evidence
        path.write_text(document + "\n", encoding="utf-8")
        self.decision = {"kind": "result", "state": state, "task": "facts", "attempt": attempt,
                         "evidence": evidence, "content": document[:2400],
                         "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        self.job = {"state": "completed", "attempt": attempt, "decision": "D1", "evidence": evidence}
        self.data = {"tasks": {"facts": self.job}, "decisions": {"D1": self.decision}}
        return path

    def render(self):
        return poster_prompt(self.task, self.data, self.run)

    def test_only_public_words_join_creative_brief(self):
        for approval in ("auto", "approved"):
            with self.subTest(approval=approval):
                self.save(self.document, state=approval)
                prompt = self.render()
                self.assertTrue(prompt.startswith(self.task["media"]["prompt"]))
                self.assertTrue(prompt.endswith("周五出逃计划\n活动时间、地点待定\n报名链接待公布"))
                for excluded in ("十人", "内部", "example.invalid", "media-copy"):
                    self.assertNotIn(excluded, prompt)

    def test_original_or_self_contained_prompts_pass_through(self):
        self.task.pop("media_copy_from")
        for prompt in ("极简黑白几何插画", "海报标题：周五出逃计划。活动时间、地点待定。报名链接待公布。"):
            self.task["media"]["prompt"] = prompt
            self.assertIsNone(poster_plan_issue(self.task))
            self.assertEqual(self.render(), prompt)

    def test_rejects_draft_construction_instructions(self):
        for brief in ("主题清晰。正式生成前必须等待已确认活动信息、采用设计并更新此提示词，不捏造信息。",
                      "等待照片，资料齐全后再出图。", "Replace this prompt with confirmed details."):
            self.task["media"]["prompt"] = brief
            self.assertIn("LD", poster_plan_issue(self.task))
            with self.assertRaises(MediaBriefError):
                self.render()

    def test_source_is_direct_image_dependency(self):
        for tasks in ({"media_copy_from": "../facts"}, {"media_copy_from": "missing"},
                      {"media_copy_from": None}, {"media_copy_from": ["facts"]}, {"media_copy_from": "poster"},
                      {"media": {"type": "speech", "prompt": "大家好"}}, {"depends_on": "facts"}):
            with self.subTest(tasks=tasks):
                self.assertIn("直接前置", poster_plan_issue({**self.task, **tasks}))

    def test_explicit_current_accepted_result_required(self):
        for change in ({"state": "waiting"}, {"state": "superseded"}, {"attempt": 0}, {"task": "design"},
                       {"evidence": "../facts.md"}, {"kind": "effect"}):
            with self.subTest(change=change):
                self.save(self.document)
                self.decision.update(change)
                with self.assertRaises(MediaBriefError):
                    self.render()
        for change in ({"attempt": True}, {"state": "judging"}, {"evidence": "../facts.md"}, {"decision": "missing"}):
            self.save(self.document)
            self.job.update(change)
            with self.assertRaises(MediaBriefError):
                self.render()

    def test_reads_only_latest_attempt_and_checks_digest(self):
        self.save("```media-copy\n旧文案\n```")
        path = self.save(self.document, attempt=2)
        self.assertNotIn("旧文案", self.render())
        path.write_text(self.document.replace("待公布", "https://example.invalid"), encoding="utf-8")
        with self.assertRaisesRegex(MediaBriefError, "放行版本"):
            self.render()

    def test_missing_duplicate_nested_or_draft_copy_never_leaks(self):
        for block in ("无公开文案", "```media-copy\n内部评论", "```media-copy\n\n```",
                      self.document + "\n" + self.document, "````md\n" + self.document + "\n````",
                      "```media-copy\nTODO 更新此提示词\n```", "```media-copy\n```yaml\nsecret: xxx\n```"):
            with self.subTest(block=block):
                self.save(block)
                with self.assertRaises(MediaBriefError):
                    self.render()

    def test_legacy_short_release_requires_complete_exact_content(self):
        self.decision.pop("evidence_sha256")
        self.assertIn("报名链接待公布", self.render())
        self.decision["content"] = "旧内容"
        with self.assertRaises(MediaBriefError):
            self.render()
        self.save("未公开" * 950 + "\n" + self.document)
        self.decision.pop("evidence_sha256")
        with self.assertRaises(MediaBriefError):
            self.render()

    def test_rejects_special_paths(self):
        path = self.run / "facts.md"
        path.rename(self.run / "old.md")
        with self.assertRaises(MediaBriefError):
            self.render()
        path.symlink_to(self.run / "old.md")
        with self.assertRaises(MediaBriefError):
            self.render()

    def test_size_boundaries(self):
        self.save("```media-copy\n" + "a" * 9980 + "\n```")
        with self.assertRaisesRegex(MediaBriefError, "10000"):
            self.render()
        with (self.run / "facts.md").open("wb") as handle:
            handle.truncate(1_048_577)
        with self.assertRaisesRegex(MediaBriefError, "1 MiB"):
            self.render()
