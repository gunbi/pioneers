import io
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capabilities import REGISTRY, capability_prompt
from skill_sources import SNAPSHOT, read_json, refresh_run, refresh_source, source_url


class SkillSourcesTest(unittest.TestCase):
    def test_ecs_skill_refreshes_master_and_loads_reference(self):
        skill = "aliyun-ecs-container-deployment"
        spec = REGISTRY["skills"][skill]
        repo, path, ref = spec["sources"][0]
        self.assertEqual(ref, "master")
        response = io.BytesIO(b"---\nname: ecs\n---\nECS upstream reference")
        response.headers = {}
        with TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            with patch.dict("os.environ", {"PIONEERS_SKILLS_OFFLINE": "0"}), patch(
                "skill_sources.urlopen", return_value=response
            ) as download:
                self.assertEqual(refresh_run(run, root, {skill: spec})["updated"], 1)
            request = download.call_args.args[0]
            self.assertEqual(request.full_url, f"https://raw.githubusercontent.com/{repo}/master/{path}")
            snapshot = read_json(run / SNAPSHOT)
            url = f"https://github.com/{repo}/blob/master/{path}"
            self.assertEqual(snapshot[url]["status"], "updated")
            for role in ("ld", "engineer", "qa"):
                prompt = "\n".join(capability_prompt(role, [skill], [], sources=snapshot))
                self.assertIn("本任务的 Skill " + skill, prompt)
                self.assertIn("ECS upstream reference", prompt)

    def test_refresh_revalidates_then_falls_back(self):
        repo, path = "android/skills", "testing/testing-setup/SKILL.md"
        with TemporaryDirectory() as temp:
            cache = Path(temp)
            response = io.BytesIO(b"---\nname: testing\n---\nA verified excerpt.")
            response.headers = {"ETag": '"v1"'}
            with patch("skill_sources.urlopen", return_value=response):
                fresh = refresh_source(repo, path, cache, False)
            self.assertEqual(fresh["status"], "updated")
            self.assertEqual(len(fresh["sha256"]), 64)

            def not_modified(request, *, timeout):
                self.assertEqual(request.get_header("If-none-match"), '"v1"')
                raise HTTPError(request.full_url, 304, "Not modified", None, None)

            with patch("skill_sources.urlopen", side_effect=not_modified):
                self.assertEqual(refresh_source(repo, path, cache, False)["status"], "current")
            with patch("skill_sources.urlopen", side_effect=URLError("offline")):
                fallback = refresh_source(repo, path, cache, False)
            self.assertEqual(fallback["status"], "cached")
            self.assertEqual(fallback["sha256"], fresh["sha256"])

    def test_run_snapshots_source_and_only_selected_skills_receive_it(self):
        repo, path = "android/skills", "testing/testing-setup/SKILL.md"
        url = source_url(repo, path)
        with TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "run"
            run.mkdir()
            skills = {"android-development": {"sources": [[repo, path]]}}
            data = {"url": url, "status": "updated", "text": "Upstream example"}
            with patch("skill_sources.refresh_source", return_value=data):
                summary = refresh_run(run, root, skills)
            self.assertEqual(summary["updated"], 1)
            snapshot = read_json(run / SNAPSHOT)
            prompt = "\n".join(capability_prompt("engineer", ["android-development"], [], sources=snapshot))
            self.assertIn("本任务的 Skill android-development", prompt)
            self.assertIn("Upstream example", prompt)
            self.assertNotIn("Upstream example", "\n".join(capability_prompt("engineer", [], [], sources=snapshot)))

            added = ["obra/superpowers", "skills/test-driven-development/SKILL.md"]
            added_url = source_url(*added)
            skills["test-engineering"] = {"sources": [added]}
            responses = {
                url: {"url": url, "status": "unavailable"},
                added_url: {"url": added_url, "status": "updated", "text": "New upstream example"},
            }
            with patch("skill_sources.refresh_source",
                       side_effect=lambda repo, path, *_: responses[source_url(repo, path)]):
                summary = refresh_run(run, root, skills)
            snapshot = read_json(run / SNAPSHOT)
            self.assertEqual(summary["cached"], 1)
            self.assertEqual(snapshot[url]["text"], "Upstream example")
            self.assertEqual(snapshot[added_url]["text"], "New upstream example")
