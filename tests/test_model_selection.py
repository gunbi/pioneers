from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from model_plugins import OpenRouter, SiliconFlow
from model_selection import confirmed_free, load_catalogs, rank_candidates, requires_free
from openrouter_media import catalog
from skill_sources import write_json


def chat(model, **metadata):
    return {"id": model, "kinds": ["chat"], **metadata}


def task(level="medium", prompt=""):
    return {"complexity": {"level": level}, "prompt": prompt}


def models(candidates):
    return [c["model"] for c in candidates]


class ModelRankingTest(unittest.TestCase):
    def test_quality_preset_ranks_the_matching_siliconflow_models_by_task(self):
        recommendations = {
            "text": ("chat", ["deepseek-ai/DeepSeek-V4-Pro"]),
            "patch": ("chat", ["zai-org/GLM-5.3", "moonshotai/Kimi-K2.7-Code"]),
            "image": ("text-to-image", ["OpenMOSS-Team/MOSS-Image"]),
            "speech": ("text-to-speech", ["FunAudioLLM/CosyVoice2-0.5B"]),
            "video": ("text-to-video", ["MiniMaxAI/MiniMax-H3"]),
        }
        for mode, (kind, preferred) in recommendations.items():
            with self.subTest(mode=mode):
                def row(model):
                    return {"id": model, "kinds": [kind]}

                catalogs = {"siliconflow": [row("other/fast"), row("other/flagship"), *map(row, reversed(preferred))],
                            "openrouter": [row(preferred[0])]}
                policy = {"preset": "siliconflow-quality"}
                ranked = rank_candidates(catalogs, task("low"), mode, policy, limit=None)
                self.assertEqual(models(ranked), [*preferred, "other/flagship", "other/fast", preferred[0]])
                self.assertEqual(ranked[0]["provider"], "siliconflow")
                self.assertIn("内置策略", ranked[0]["reason"])
                self.assertEqual(ranked[-1]["provider"], "openrouter")
                policy["banned"] = [{"provider": "siliconflow", "model": preferred[0]}]
                self.assertEqual(models(rank_candidates(catalogs, task(), mode, policy)), models(ranked)[1:])

    def test_complexity_changes_first_choice_using_evidence_outside_model_names(self):
        rows = [chat("vendor/a", description="A lightweight, low-latency assistant"),
                chat("vendor/b", tags=["reasoning"], context_length=262144)]
        catalogs = {"openrouter": rows}
        low = rank_candidates(catalogs, task("low"), "text", {})
        high = rank_candidates(catalogs, task("high"), "text", {})
        self.assertEqual(low[0]["model"], "vendor/a")
        self.assertEqual(high[0]["model"], "vendor/b")
        self.assertIn("复杂", high[0]["reason"])
        self.assertEqual(high[0]["context_length"], 262144)
        self.assertEqual(high[0]["tags"], ["reasoning"])

    def test_preferences_provider_profiles_and_catalog_description_actually_change_order(self):
        rows = {"siliconflow": [chat("family/modelA")],
                "openrouter": [chat("vendor/scribe", description="For multilingual translation"),
                               chat("vendor/plain", description="For software programming")]}
        profiles = [{"name": "author", "mode": "text", "provider": "siliconflow",
                     "model": "family/modelA", "tags": ["法务"]}]
        self.assertEqual(rank_candidates(rows, task(), "text", {"preference": "优先法务"}, profiles)[0]["provider"],
                         "siliconflow")
        self.assertEqual(models(rank_candidates(rows, task(prompt="翻译说明"), "text", {}))[0], "vendor/scribe")
        self.assertEqual(models(rank_candidates(rows, task(), "text", {"preference": "优先 OpenRouter，避免翻译"}))[0],
                         "vendor/plain")
        self.assertEqual(models(rank_candidates(rows, task(), "patch", {}))[0], "vendor/plain")

    def test_prefer_cheap_uses_actual_input_output_price_unknown_is_not_free(self):
        rows = {"openrouter": [chat("v/a", pricing={"prompt": ".005", "completion": ".0001"}),
                               chat("v/b", pricing={"prompt": ".0002", "completion": ".0001"}),
                               chat("v/c:free", pricing={"prompt": "0", "completion": "0"})],
                "siliconflow": [chat("free-model")]}
        self.assertEqual(models(rank_candidates(rows, task(), "text", {"preference": "便宜优先"})),
                         ["v/c:free", "v/b", "v/a", "free-model"])
        self.assertEqual(models(rank_candidates(rows, task(), "text", {"preference": "仅免费"})), ["v/c:free"])

    def test_speed_and_quality_answers_change_the_order_for_the_same_task(self):
        rows = {"openrouter": [chat("v/a", description="flagship"), chat("v/b", description="low latency")]}
        for preference, expected in (("优先质量", "v/a"), ("优先速度", "v/b"), ("速度优先", "v/b")):
            with self.subTest(preference=preference):
                self.assertEqual(models(rank_candidates(rows, task(), "text", {"preference": preference}))[0], expected)

    def test_fee_proof_matches_the_generation_mode_and_rejects_ambiguous_skus(self):
        token_zeros = {"prompt": "0", "completion": "0"}
        for mode, kind, key in [("image", "text-to-image", "image_output"),
                                ("music", "text-to-music", "audio_output"),
                                ("speech", "text-to-speech", "audio_output"),
                                ("sound_effect", "text-to-sound-effect", "audio_output"),
                                ("video", "text-to-video", "video_output")]:
            row = {"id": "provider/model", "kinds": [kind], "pricing": token_zeros}
            with self.subTest(mode=mode):
                self.assertFalse(confirmed_free("openrouter", row, mode))
                self.assertTrue(confirmed_free("openrouter", dict(row, pricing={**token_zeros, key: 0}), mode))
                self.assertTrue(confirmed_free("openrouter", dict(row, id="provider/model:free"), mode))
                self.assertFalse(confirmed_free("openrouter", dict(row, id="provider/model:free",
                    pricing={**token_zeros, key: ".034"}), mode))
                self.assertFalse(confirmed_free("siliconflow", dict(row, id="provider/model:free"), mode))
        video = {"id": "scene/video", "kinds": ["text-to-video"], "pricing": token_zeros}
        self.assertTrue(confirmed_free("openrouter", dict(video, pricing_skus={"duration_seconds": "0"}), "video"))
        for skus in [{"unrelated": "0"}, {"video_tokens": 0, "other": "0.02"}, {"video_tokens": {"tier": 0}},
                     {"duration_seconds_4K": "1E-999"}, {"duration_seconds": "NaN"}]:
            self.assertFalse(confirmed_free("openrouter", dict(video, pricing_skus=skus), "video"))
        self.assertFalse(confirmed_free("openrouter", dict(chat("v/music", pricing=token_zeros),
                                                           kinds=["chat", "audio", "text-to-music"]), "text"))
        for model in ("~alias/quick:free", "openrouter/auto", "openrouter/free"):
            self.assertFalse(confirmed_free("openrouter", chat(model, pricing=token_zeros), "text"))
        for bad in (None, -1, True, "NaN", "Infinity", {}, []):
            self.assertFalse(confirmed_free("openrouter", chat("x/a", pricing={"prompt": bad, "completion": 0}), "text"))

    def test_video_resolution_price_is_not_a_model_wide_free_rate(self):
        from pioneers import free_in_catalog

        row = {"id": "scene/video", "kinds": ["text-to-video"], "supported_resolutions": ["720p", "1080p"],
               "pricing_skus": {"duration_seconds_1080p": "0"}}
        catalogs = {"openrouter": [row]}
        route = {"provider": "openrouter", "model": row["id"], "mode": "video", "resolution": "720p"}
        self.assertFalse(free_in_catalog(catalogs, route))
        self.assertEqual(rank_candidates(catalogs, task(), "video", {"preference": "仅免费"}), [])
        self.assertFalse(rank_candidates(catalogs, task(), "video", {})[0]["free"])

    def test_strict_free_language_is_separate_from_preferences(self):
        for text in ["仅免费", "只用免费模型", "不使用付费模型", "free models only", "no paid models", "only use a free model",
                     "必须免费，优先中文", "不得使用付费模型", "do not use paid models"]:
            self.assertTrue(requires_free(text), text)
        for text in ["免费优先", "不只免费", "不限于免费", "not free only", "prefer free if available", "不要求仅免费"]:
            self.assertFalse(requires_free(text), text)

    def test_capability_bans_variants_dynamic_routers_and_stable_shortlist(self):
        rows = [chat("m/r" + str(i)) for i in range(12)]
        rows += [chat("m/r0:free"), chat("~company/recommended"), chat("openrouter/free"),
                 {"id": "media/edit", "kinds": ["image"]}, chat("bad id"), chat("m/r3")]
        policy = {"banned": [{"provider": "openrouter", "model": "m/r0"}]}
        first = rank_candidates({"openrouter": rows}, task(), "text", policy)
        full = rank_candidates({"openrouter": list(reversed(rows))}, task(), "text", policy, limit=None)
        self.assertEqual(first, full[:8])
        self.assertEqual(len(full), 11)
        self.assertFalse(set(models(full)) & {"m/r0", "m/r0:free", "~company/recommended", "openrouter/free"})
        self.assertEqual(rank_candidates({"openrouter": rows}, task(), "image", {}), [])


class ModelCatalogTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def save(self, provider, models, age):
        write_json(self.root / f"model-catalog-{provider}.json",
                   {"updated_at": (datetime.now(timezone.utc) - timedelta(seconds=age)).isoformat(), "models": models})

    def test_fresh_dashboard_cache_needs_configured_valid_key(self):
        self.save("openrouter", [chat("o/a")], 30)
        self.save("siliconflow", [chat("s/a")], 30)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test", "SILICONFLOW_API_KEY": "invalid key"}, clear=True), \
                patch.object(OpenRouter, "list_catalog", side_effect=AssertionError):
            self.assertEqual(load_catalogs(self.root), {"openrouter": [chat("o/a")]})
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(load_catalogs(self.root), {})

    def test_stale_refresh_failure_isolated_and_retries_throttled_across_calls(self):
        self.save("openrouter", [chat("old/price", pricing={"prompt": 0, "completion": 0})], 601)
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test", "SILICONFLOW_API_KEY": "test"}, clear=True), \
                patch.object(OpenRouter, "list_catalog", side_effect=RuntimeError("offline")) as failed, \
                patch.object(SiliconFlow, "list_catalog", return_value=[chat("valid/model")]) as good:
            for _ in range(2):
                self.assertEqual(load_catalogs(self.root), {"openrouter": [], "siliconflow": [chat("valid/model")]})
        failed.assert_called_once()
        good.assert_called_once()
        self.assertEqual(json.loads((self.root / "model-catalog-openrouter.json").read_text())["models"][0]["id"], "old/price")

    def test_concurrent_cold_start_shares_one_refresh(self):
        began, finish = Event(), Event()
        def fetch():
            began.set()
            self.assertTrue(finish.wait(3))
            return [chat("fast/new")]
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test"}, clear=True), \
                patch.object(OpenRouter, "list_catalog", side_effect=fetch) as api, ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(load_catalogs, self.root)
            self.assertTrue(began.wait(2))
            second = pool.submit(load_catalogs, self.root)
            finish.set()
            for future in (first, second):
                self.assertEqual(future.result(), {"openrouter": [chat("fast/new")]})
        api.assert_called_once()

    def test_directory_preserves_provider_metadata_and_leaves_legacy_minimal_rows(self):
        basic = {"id": "legacy/text", "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}}
        rich = {**basic, "id": "new/text", "description": "  Long    context  ", "context_length": 200000,
                "supported_parameters": ["reasoning"], "pricing": {"prompt": ".002", "completion": ".003"}}
        movie = {"id": "movie/one", "description": "text-to-video generator",
                 "architecture": {"input_modalities": ["text"], "output_modalities": ["video"]}}
        def lookup(path, **kwargs):
            return {"data": [basic, rich, movie] if "output_modalities" in path else
                           [dict(movie, pricing_skus={"duration_seconds_720p": ".05"})]}
        with patch.object(OpenRouter, "request", side_effect=lookup):
            rows = {r["id"]:r for r in catalog(OpenRouter)}
        self.assertEqual(rows[basic["id"]], chat(basic["id"]))
        self.assertEqual(rows[rich["id"]]["description"], "Long context")
        self.assertEqual(rows[rich["id"]]["tags"], ["reasoning"])
        self.assertEqual(rows[movie["id"]]["pricing_skus"], {"duration_seconds_720p": ".05"})
