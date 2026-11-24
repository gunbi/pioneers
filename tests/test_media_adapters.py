import base64
import io
import json
import unittest
import wave
from unittest.mock import Mock, patch

from media_adapters import check_available, download_siliconflow_image, file_type, generate
import openrouter_media
import siliconflow_media


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
MP3 = bytes.fromhex("fffb9000") + bytes(413)
IMAGE = {"id": "raster/basic", "architecture": {"input_modalities": ["text", "image"],
         "output_modalities": ["image"]}, "supported_parameters": {
             "input_references": {"min": 0, "max": 4}, "output_format": {"values": ["png"]}}}
MUSIC = {"id": "google/lyria-3-clip-preview", "endpoints": ["/api/v1/chat/completions"],
         "architecture": {"input_modalities": ["text"], "output_modalities": ["text", "audio"]}}


def tts(model, formats=("mp3",), voices=("first", "second"), *, needs_reference=False):
    return {"id": model, "endpoints": [{"provider_slug": "test/us-central1",
            "input_modalities": [{"type": "text"}], "output_modalities": [{"type": "speech", "supported_parameters": {
                "voice": {"type": "enum", "values": list(voices), "references": "required" if needs_reference else "excluded"},
                "response_format": {"type": "enum", "values": list(formats)}}}]}]}


def fixture(name, catalog=None, replies=None):
    provider = Mock()
    provider.name = name
    def request(path, **kw):
        if kw.get("payload") is not None:
            return replies[path]
        return {"data": (catalog or {}).get(path, [])}
    provider.request.side_effect = request
    return provider


def execute(provider, mode, model, **options):
    return generate(provider, {"mode": mode, "model": model, **options}, "A short prompt")


class MediaAdaptersTest(unittest.TestCase):
    def test_openrouter_directory_only_offers_proven_pure_text_routes(self):
        edit = dict(IMAGE, id="edit/only", supported_parameters={"input_references": {"min": 1}})
        svg = dict(IMAGE, id="vector/only", supported_parameters={"output_format": {"values": ["svg"]}})
        seed = openrouter_media.SOUND_EFFECT_MODEL
        clones = tts("speech/clone", needs_reference=True)
        voice = tts("speech/basic")
        general = [dict(row, endpoints=["/api/v1/images"]) for row in (IMAGE, edit, svg)]
        general += [{"id": r["id"], "endpoints": ["/api/v1/audio/speech"],
                     "architecture": {"input_modalities": ["text"], "output_modalities": ["speech"]}}
                    for r in (tts(seed), clones, voice)]
        general += [MUSIC, dict(MUSIC, id="speech/chat"),
                    {"id": "audio/asr", "architecture": {"output_modalities": ["transcription"]}}]
        provider = fixture("OpenRouter", {"/models?output_modalities=all": general, "/images/models": [IMAGE, edit, svg],
                          "/audio/speech/models": [tts(seed, voices=()), clones, voice]})
        catalog = {r["id"]: r["kinds"] for r in openrouter_media.catalog(provider)}
        self.assertEqual(catalog[IMAGE["id"]], ["image", "text-to-image"])
        self.assertEqual(catalog[edit["id"]], ["image"])
        self.assertEqual(catalog[svg["id"]], ["image"])
        self.assertEqual(catalog[voice["id"]], ["audio", "text-to-speech"])
        self.assertEqual(catalog[clones["id"]], ["audio"])
        self.assertIn("text-to-sound-effect", catalog[seed])
        self.assertIn("text-to-music", catalog[MUSIC["id"]])
        self.assertNotIn("text-to-music", catalog["speech/chat"])
        self.assertEqual(catalog["audio/asr"], ["transcription"])
        self.assertTrue(all(c.kwargs["public"] for c in provider.request.call_args_list))
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check_available(provider, edit["id"], "image", {})

    def test_openrouter_png_and_trusted_counts_only(self):
        provider = fixture("OpenRouter", {"/images/models": [IMAGE]}, {"/images": {
            "data": [{"b64_json": base64.b64encode(PNG).decode()}],
            "usage": {"prompt_tokens": 12, "completion_tokens": -1, "total_tokens": True, "cost": 12}}})
        self.assertEqual(execute(provider, "image", IMAGE["id"]), (PNG, "image/png", {"images": 1, "prompt_tokens": 12}))
        self.assertEqual(provider.request.call_args.kwargs["payload"],
                         {"model": IMAGE["id"], "output_format": "png", "prompt": "A short prompt"})

    def test_openrouter_speech_voice_and_format_are_paired(self):
        model = "speech/test"
        provider = fixture("OpenRouter", {"/audio/speech/models": [tts(model)]}, {"/audio/speech": (MP3, "audio/mpeg")})
        self.assertEqual(execute(provider, "speech", model, voice="second"), (MP3, "audio/mpeg", {"audio_files": 1}))
        self.assertEqual(provider.request.call_args.kwargs["payload"],
                         {"model": model, "input": "A short prompt", "response_format": "mp3", "voice": "second",
                          "provider": {"only": ["test/us-central1"]}})
        with self.assertRaisesRegex(ValueError, "voice"):
            check_available(provider, model, "speech", {"voice": "other"})
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check_available(provider, model, "music", {})
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check_available(provider, model, "sound_effect", {})

    def test_free_speech_uses_confirmed_base_metadata_and_preserves_requested_id(self):
        base = "heygen/heygen-voice-1-turbo"
        alias = base + ":free"
        general = [{"id": model, "canonical_slug": base, "endpoints": ["/api/v1/audio/speech"]} for model in (base, alias)]
        samples = bytes(range(32))
        catalog = {"/audio/speech/models": [tts(base, formats=("pcm",))], "/models?output_modalities=all": general}
        provider = fixture("OpenRouter", catalog, {"/audio/speech": (samples, "audio/pcm;rate=16000;channels=2")})
        data, mime, usage = execute(provider, "speech", alias)
        self.assertEqual((mime, usage), ("audio/wav", {"audio_files": 1}))
        with wave.open(io.BytesIO(data)) as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels(), stream.readframes(8)), (16000, 2, samples))
        self.assertEqual(provider.request.call_args.kwargs["payload"]["model"], alias)
        self.assertEqual(provider.request.call_args.kwargs["payload"]["voice"], "first")
        fallback = fixture("OpenRouter", catalog, {"/audio/speech": (samples, "audio/pcm")})
        with wave.open(io.BytesIO(execute(fallback, "speech", alias)[0])) as stream:
            self.assertEqual((stream.getframerate(), stream.getnchannels()), (44100, 1))
        general[1]["canonical_slug"] = "entirely/different"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check_available(provider, alias, "speech", {})

    def test_seed_sound_effect_can_use_prompt_without_voice(self):
        model = openrouter_media.SOUND_EFFECT_MODEL
        provider = fixture("OpenRouter", {"/audio/speech/models": [tts(model, voices=())]},
                           {"/audio/speech": (MP3, "audio/mpeg")})
        self.assertEqual(execute(provider, "sound_effect", model), (MP3, "audio/mpeg", {"audio_files": 1}))
        self.assertEqual(provider.request.call_args.args, ("/audio/speech",))
        self.assertNotIn("voice", provider.request.call_args.kwargs["payload"])

    def test_music_sse_combines_fragments_rejects_incomplete_and_error(self):
        encoded = base64.b64encode(MP3).decode()
        events = [{"choices": [{"delta": {"audio": {"data": part}}}]} for part in [encoded[:9], encoded[9:]]]
        events += [{"choices": [], "usage": {"total_tokens": 40}}]
        chunks = b": heartbeat\r\n\r\n" + b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
        api = "/chat/completions"
        replies = {api: chunks + b"data: [DONE]\n\n"}
        provider = fixture("OpenRouter", {"/models?output_modalities=all": [MUSIC]}, replies)
        self.assertEqual(execute(provider, "music", MUSIC["id"]), (MP3, "audio/mpeg", {"audio_files": 1, "total_tokens": 40}))
        self.assertTrue(provider.request.call_args.kwargs["payload"]["stream"])
        for ending in [b"", b'data: {"error":{"message":"injected details"}}\n\ndata: [DONE]\n']:
            replies[api] = chunks + ending
            with self.assertRaisesRegex(ValueError, "ended|failed"):
                execute(provider, "music", MUSIC["id"])

    def test_siliconflow_subtypes_and_anonymous_signed_image_download(self):
        model = "Qwen/Qwen-Image"
        edit = model + "-Edit-2509"
        catalog = {"/models": [{"id": model}, {"id": edit}], "/models?type=image": [{"id": model}, {"id": edit}],
                   "/models?sub_type=text-to-image": [{"id": model}, {"id": edit}]}
        provider = fixture("SiliconFlow", catalog, {"/images/generations": {"images": [{"url": "https://s3.siliconflow.cn/path?signed=1"}]}})
        models = {row["id"]:row["kinds"] for row in siliconflow_media.list_catalog(provider)}
        self.assertEqual(models, {model: ["image", "text-to-image"], edit: ["image"]})
        with self.assertRaisesRegex(ValueError, "unavailable"):
            check_available(provider, edit, "image", {})
        with patch("media_adapters.build_opener") as factory:
            factory.return_value.open.return_value = io.BytesIO(PNG)
            self.assertEqual(execute(provider, "image", model), (PNG, "image/png", {"images": 1}))
            self.assertIsNone(factory.return_value.open.call_args.args[0].get_header("Authorization"))
            self.assertIsNone(factory.call_args.args[0].redirect_request(None, None, None, None, None, None))
        for url in ["https://localhost/token", "https://s3.siliconflow.cn:8443/img",
                    "http://s3.siliconflow.cn/image", "https://s3.siliconflow.cn@example.com/",
                    "https://example.com@s3.siliconflow.cn/image"]:
            with self.assertRaisesRegex(ValueError, "Untrusted"):
                download_siliconflow_image(url)

    def test_siliconflow_narration_and_excluded_capabilities(self):
        model = "FunAudioLLM/CosyVoice2-0.5B"
        provider = fixture("SiliconFlow", {"/models?sub_type=text-to-speech": [{"id": model}]},
                           {"/audio/speech": (MP3, "audio/mpeg")})
        self.assertEqual(execute(provider, "speech", model), (MP3, "audio/mpeg", {"audio_files": 1}))
        self.assertEqual(provider.request.call_args.kwargs["payload"]["voice"], model + ":alex")
        for mode in ("sound_effect", "music"):
            with self.assertRaisesRegex(ValueError, "unavailable"):
                check_available(provider, model, mode, {})

    def test_unsupported_files_are_rejected(self):
        for content, mode in [(b'<script>alert(1)</script>', "image"), (PNG[:-20], "image"),
                              (b'{"error":"insufficient credits"}', "speech"), (b"ID3" + bytes(100), "speech")]:
            with self.assertRaises(ValueError):
                file_type(content, mode)
        model = "speech/pcm"
        provider = fixture("OpenRouter", {"/audio/speech/models": [tts(model, formats=("pcm",))]},
                           {"/audio/speech": (b"bad data", "application/json")})
        with self.assertRaisesRegex(ValueError, "PCM"):
            execute(provider, "speech", model)
