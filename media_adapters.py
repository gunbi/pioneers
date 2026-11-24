"""Generate a single passive image or audio artifact using a registered provider."""

import base64
from email.message import Message
import io
import json
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener
import wave

import openrouter_media
import siliconflow_media


MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_ENCODED_BYTES = (MAX_MEDIA_BYTES + 2) // 3 * 4
MAX_JSON_BYTES = MAX_ENCODED_BYTES + 4 * 1024 * 1024


def check_available(provider, model, mode, options):
    if provider.name == "OpenRouter":
        openrouter_media.generation_options(provider, model, mode, options)
    elif provider.name == "SiliconFlow":
        siliconflow_media.check_media_available(provider, model, mode, options)
    else:
        raise ValueError("Unknown media provider")


def decode_media(value):
    if not isinstance(value, str) or not value or len(value) > MAX_ENCODED_BYTES:
        raise ValueError("Missing or oversized encoded media")
    try:
        content = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError("Invalid encoded media") from None
    if len(content) > MAX_MEDIA_BYTES:
        raise ValueError("Decoded media exceeds size limit")
    return content


def file_type(content, mode):
    if not isinstance(content, bytes) or not 12 <= len(content) <= MAX_MEDIA_BYTES:
        raise ValueError("Empty, truncated or oversized media file")
    if mode == "image":
        if (content.startswith(b"\x89PNG\r\n\x1a\n") and content[12:16] == b"IHDR"
                and content.endswith(b"IEND\xaeB\x60\x82")):
            return "image/png"
        if content.startswith(b"\xff\xd8\xff") and content.endswith(b"\xff\xd9"):
            return "image/jpeg"
        if (content.startswith(b"RIFF") and content[8:12] == b"WEBP"
                and content[12:16] in (b"VP8 ", b"VP8L", b"VP8X")
                and int.from_bytes(content[4:8], "little") + 8 == len(content)):
            return "image/webp"
    else:
        if (content.startswith(b"RIFF") and content[8:12] == b"WAVE"
                and int.from_bytes(content[4:8], "little") + 8 == len(content)):
            try:
                with wave.open(io.BytesIO(content), "rb") as stream:
                    if stream.getnframes() > 0:
                        return "audio/wav"
            except (wave.Error, EOFError):
                pass
        offset = 0
        if content.startswith(b"ID3") and not any(b & 128 for b in content[6:10]):
            tag_size = 0
            for part in content[6:10]:
                tag_size = (tag_size << 7) | part
            offset = 10 + tag_size + (10 if content[5] & 16 else 0)
        frame = content[offset:offset + 4]
        if (len(frame) == 4 and frame[0] == 255 and frame[1] & 224 == 224
                and frame[1] & 6 == 2 and frame[1] & 24 != 8
                and frame[2] >> 4 not in (0, 15) and frame[2] & 12 != 12):
            return "audio/mpeg"
    raise ValueError("Provider did not return a supported " + ("image" if mode == "image" else "audio file"))


def download_siliconflow_image(url):
    # Signed delivery uses a separate anonymous request. Redirection can cross origins.
    from model_plugins import NoRedirect

    if not isinstance(url, str) or not url.isascii() or len(url) > 8192 or any(c.isspace() for c in url):
        raise ValueError("Invalid SiliconFlow image URL")
    try:
        parsed = urlsplit(url)
        valid = (parsed.scheme == "https" and parsed.hostname in {"s3.siliconflow.cn", "s3.siliconflow.com"}
                 and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
                 and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Untrusted SiliconFlow image delivery host")
    try:
        with build_opener(NoRedirect()).open(Request(url), timeout=45) as response:
            content = response.read(MAX_MEDIA_BYTES + 1)
    except HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f"Image delivery returned HTTP {code}") from None
    except (OSError, HTTPException, ValueError):
        raise ValueError("Could not read the delivered image") from None
    if len(content) > MAX_MEDIA_BYTES:
        raise ValueError("Image exceeds size limit")
    return content


def usage_counts(body):
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {}
    return {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
            if type(usage.get(key)) is int and usage[key] >= 0}


def first_result(body, name):
    items = body.get(name) if isinstance(body, dict) else None
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("Provider did not return one generated image")
    return items[0]


def image_result(provider, model, prompt, options):
    payload = {"model": model, "prompt": prompt, **options}
    if provider.name == "OpenRouter":
        body = provider.request("/images", payload=payload, timeout=180, max_bytes=MAX_JSON_BYTES)
        content = decode_media(first_result(body, "data").get("b64_json"))
    else:
        body = provider.request("/images/generations", payload=payload, timeout=180)
        content = download_siliconflow_image(first_result(body, "images").get("url"))
    return content, {"images": 1, **usage_counts(body)}


# OpenRouter can omit the optional rate/channels parameters. These models publish
# their native mono PCM rate; for unfamiliar models require a rate in the response.
PCM_RATES = {"heygen/heygen-voice-1": 44100, "heygen/heygen-voice-1-turbo": 44100,
             "google/gemini-2.5-pro-tts": 24000, "google/gemini-2.5-flash-tts": 24000,
             "google/gemini-3.1-flash-tts-preview": 24000, "google/gemini-3.8-flash-tts": 24000,
             "google/gemini-3.8-flash-lite-tts": 24000}


def pcm_to_wav(data, content_type, model):
    header = Message()
    header["Content-Type"] = content_type
    if header.get_content_type() != "audio/pcm":
        raise ValueError("Expected a PCM audio response")
    try:
        rate = int(header.get_param("rate", PCM_RATES.get(model.removesuffix(":free"))))
        channels = int(header.get_param("channels", "1"))
    except (ValueError, TypeError):
        raise ValueError("PCM response is missing its sample rate or channel count") from None
    if (not 8000 <= rate <= 192000 or channels not in (1, 2) or not data
            or len(data) + 44 > MAX_MEDIA_BYTES or len(data) % (channels * 2)):
        raise ValueError("Invalid PCM audio dimensions")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as stream:
        stream.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
        stream.writeframes(data)
    return buffer.getvalue()


def speech_result(provider, model, prompt, options):
    if provider.name == "SiliconFlow":
        voice = options.get("voice")
        if not voice:
            voice = "alex"
        if ":" not in voice:
            voice = model + ":" + voice
        options = {"voice": voice, "response_format": "mp3"}
    payload = {"model": model, "input": prompt, **options}
    data, header = provider.request("/audio/speech", payload=payload, timeout=180, raw=True,
                                    with_content_type=True, max_bytes=MAX_MEDIA_BYTES)
    if options["response_format"] == "pcm":
        data = pcm_to_wav(data, header, model)
    return data, {"audio_files": 1}


def music_result(provider, model, prompt):
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
               "modalities": ["text", "audio"], "audio": {"format": "mp3", "voice": "alloy"},
               "stream": True, "stream_options": {"include_usage": True}}
    data = provider.request("/chat/completions", payload=payload, timeout=300, raw=True, max_bytes=MAX_JSON_BYTES)
    audio_bytes = bytearray()
    remainder = ""
    counts = {}
    done = False
    for line in io.BytesIO(data):
        if not line.startswith(b"data:"):
            continue
        value = line[5:].strip()
        if value == b"[DONE]":
            done = True
            break
        try:
            event = json.loads(value)
        except (UnicodeError, ValueError):
            raise ValueError("Invalid OpenRouter music stream") from None
        if not isinstance(event, dict) or "error" in event:
            raise ValueError("OpenRouter music generation failed")
        counts.update(usage_counts(event))
        for choice in event.get("choices", []):
            if choice.get("finish_reason") not in (None, "stop"):
                raise ValueError("OpenRouter music generation was interrupted")
            audio = (choice.get("delta") or {}).get("audio")
            if isinstance(audio, dict) and "data" in audio:
                if not isinstance(audio["data"], str):
                    raise ValueError("Invalid OpenRouter music fragment")
                # A delta may split a base64 quartet, or carry a separately padded block.
                encoded = remainder + audio["data"]
                boundary = len(encoded) - len(encoded) % 4
                if boundary:
                    audio_bytes.extend(decode_media(encoded[:boundary]))
                    if len(audio_bytes) > MAX_MEDIA_BYTES:
                        raise ValueError("Decoded media exceeds size limit")
                remainder = encoded[boundary:]
    if not done:
        raise ValueError("OpenRouter music stream ended before completion")
    if remainder or not audio_bytes:
        raise ValueError("Invalid or incomplete OpenRouter music audio")
    return bytes(audio_bytes), {"audio_files": 1, **counts}


def generate(provider, route, prompt):
    mode, model = route["mode"], route["model"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("A media prompt is required")
    if provider.name == "OpenRouter":
        options = openrouter_media.generation_options(provider, model, mode, route)
    else:
        check_available(provider, model, mode, route)
        options = {key: route[key] for key in ("voice",) if key in route}
    if mode == "image":
        content, usage = image_result(provider, model, prompt, options)
    elif mode == "music":
        content, usage = music_result(provider, model, prompt)
    elif mode in ("speech", "sound_effect"):
        content, usage = speech_result(provider, model, prompt, options)
    else:
        raise ValueError("Unsupported media task")
    return content, file_type(content, mode), usage
