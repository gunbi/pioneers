"""Publish and recover an idempotent, passive media deliverable in a run directory."""

import hashlib
import json
import os
from pathlib import Path
import re
import stat

from model_policy import MEDIA_MODES
from provider_errors import InsufficientCreditError
from skill_sources import write_json


MEDIA_EXTENSIONS = {
    "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp",
    "audio/mpeg": "mp3", "audio/wav": "wav",
}
MAX_BYTES = 64 * 1024 * 1024
COUNT_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")
LABELS = {"image": "生成图片", "speech": "旁白", "music": "配乐", "sound_effect": "音效"}


def generation(job):
    return max(job.get("continuity_from", job.get("attempt", 1)), job.get("media_retry_from", 0))


def receipt_path(run_dir, task, number):
    if (not run_dir.is_absolute() or run_dir.is_symlink()
            or not isinstance(task, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", task)
            or type(number) is not int or number < 1):
        raise ValueError("无效的媒体产物路径")
    return run_dir / f".media-{task}-{number}.json"


def read_receipt(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 8192:
        return {}
    try:
        receipt = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    return receipt if isinstance(receipt, dict) else {}


def generate(provider, route, prompt):
    from media_adapters import generate as generate_media
    return generate_media(provider, route, prompt)


def _extension(mode, mime):
    ext = MEDIA_EXTENSIONS.get(mime) if isinstance(mime, str) else None
    if not ext or (mode == "image") != mime.startswith("image/"):
        raise ValueError("媒体响应格式与任务不匹配")
    return ext


def _valid_content(data, mime):
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_BYTES:
        return False
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if mime == "audio/mpeg":
        return data.startswith(b"ID3") or (len(data) >= 2 and data[0] == 255 and data[1] & 0xe0 == 0xe0)
    return data[:4] == b"RIFF" and data[8:12] == (b"WEBP" if mime == "image/webp" else b"WAVE")


def _usage(mode, supplied):
    supplied = supplied if isinstance(supplied, dict) else {}
    counts = {key: supplied[key] for key in COUNT_KEYS if type(supplied.get(key)) is int and supplied[key] >= 0}
    counts["images" if mode == "image" else "audio_files"] = 1
    return counts


def _check_file(path, expected, mime):
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode):
        raise ValueError("媒体路径需要指向普通文件")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not os.path.samestat(entry, info) or info.st_size != expected["bytes"]:
            raise ValueError("媒体文件与回执不一致，请人工核对")
        data = handle.read(MAX_BYTES + 1)
    if not _valid_content(data, mime) or hashlib.sha256(data).hexdigest() != expected.get("sha256"):
        raise ValueError("媒体文件与回执不一致，请人工核对")


def fingerprint(route, prompt):
    parameters = [route["provider"], route["model"], route["mode"], route.get("voice"), prompt]
    return hashlib.sha256(json.dumps(parameters, ensure_ascii=False).encode()).hexdigest()


def media_job(provider, route, prompt, run_dir, task, number, *, reuse_only=False):
    path = receipt_path(run_dir, task, number)
    mode = route["mode"]
    if mode not in MEDIA_MODES or not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("媒体任务需要明确的创作提示与类型")
    directory = run_dir / "media"
    if directory.is_symlink():
        raise ValueError("无效的媒体产物目录")
    directory.mkdir(mode=0o700, exist_ok=True)
    stage = run_dir / f".media-{task}-{number}.staging"
    identity = fingerprint(route, prompt)
    fd = None
    if not reuse_only:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
    if fd is None:
        receipt = read_receipt(path)
        if receipt.get("fingerprint") != identity:
            raise ValueError("媒体回执与当前参数不一致，请核对原请求")
        if receipt.get("state") != "ready":
            raise ValueError("媒体生成结果尚未确认，请人工核对原请求")
    else:
        # A competing or recovering worker can never issue a second paid request.
        receipt = {"state": "submitting", "fingerprint": identity}
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            data, mime, usage = generate(provider, route, prompt)
        except InsufficientCreditError:
            # Only the worker that reserved the receipt can release a rejected submission.
            path.unlink()
            raise
        _extension(mode, mime)
        if not _valid_content(data, mime):
            raise ValueError("媒体响应的文件内容或大小无效，请核对原请求")
        with os.fdopen(os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as binary:
            binary.write(data)
            binary.flush()
            os.fsync(binary.fileno())
        receipt.update(state="ready", mime=mime, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                       usage=_usage(mode, usage))
        write_json(path, receipt)
        del data
    mime = receipt.get("mime")
    extension = _extension(mode, mime)
    if type(receipt.get("bytes")) is not int or not 0 < receipt["bytes"] <= MAX_BYTES:
        raise ValueError("媒体回执的文件大小无效")
    filename = f"{task}.{number}.{extension}"
    final = directory / filename
    # Link without overwriting an existing file. A stopped worker can resume publication
    # from either side of the link/unlink boundary, including simultaneous recovery.
    if not os.path.lexists(final):
        try:
            _check_file(stage, receipt, mime)
            os.link(stage, final, follow_symlinks=False)
        except (FileExistsError, FileNotFoundError):
            if not os.path.lexists(final):
                raise ValueError("已登记的媒体文件缺失，请人工核对") from None
    _check_file(final, receipt, mime)
    stage.unlink(missing_ok=True)
    return {"content": f"[{LABELS[mode]}](media/{filename})", "usage": _usage(mode, receipt.get("usage")),
            "media": {"filename": filename, "mime": mime, "bytes": receipt["bytes"]}}
