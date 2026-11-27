"""Assemble a poster brief from its task and explicitly released public copy."""

import hashlib
import os
from pathlib import Path
import re
import stat


class MediaBriefError(ValueError):
    """The LD must correct the media handoff before generation."""


_MAX_PROMPT = 10_000
_MAX_ARTIFACT_BYTES = 1_048_576
_PUBLIC_COPY_MARKER = "\n\n请将以下已确认的公开文案原样呈现在海报上：\n"
_RATIO = re.compile(r"(?<![\d:：])([1-9][0-9]?)\s*[:：]\s*([1-9][0-9]?)(?![\d:：])")
_CANVAS_BEFORE = re.compile(r"(?:海报|画幅|宽高比|纵横比|画布|比例|poster|canvas|aspect ratio)"
                            r"(?:\s*(?:为|为约|比例|的|[:：=]|of|in))*\s*$", re.I)
_CANVAS_AFTER = re.compile(r"^\s*[- ]?\s*(?:的\s*)?(?:竖版|竖屏|横版|横屏|海报|画幅|画布|纵向|横向|"
                           r"portrait|landscape|poster|canvas|aspect ratio|format)", re.I)
_ORIENTATION = re.compile(r"竖版|竖屏|横版|横屏|方版|方形海报|正方形海报|"
                          r"\b(?:portrait|landscape|square)[ -](?:orientation|format|poster|canvas)\b", re.I)
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
# Draft handoff instructions are different from approved public wording like
# “时间待定” or “Registration details to follow”, which is valid poster copy.
_UNFINISHED = re.compile(
    r"(?:更新|替换|补全|改写|补齐)(?:本|此|这个|这份)(?:提示词|创作简报)|"
    r"(?:正式)?(?:生成|制图|出图)前(?:必须|需要|需|请|应|先|仍)*等待(?:已确认|确认|活动|资料|素材|信息|文案)|"
    r"(?:资料|信息|文案|素材)(?:齐全|齐备|确认)后再(?:生成|制图|出图)|"
    r"\b(?:update|replace|revise|complete) (?:this|the current) (?:prompt|creative brief)\b|"
    r"\bwait for (?:confirmation|confirmed (?:details|copy)|approved copy) before (?:generat\w*|render\w*)\b", re.I)


def poster_ratio(media, task_prompt=""):
    """Resolve the canvas before upstream public text is added to the brief."""
    ratios = set()
    if "aspect_ratio" in media:
        value = media["aspect_ratio"]
        if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]?:[1-9][0-9]?", value):
            raise MediaBriefError("media.aspect_ratio 需要宽:高，例如 3:4")
        ratios.add(value)
    directions = set()
    for text in (task_prompt, media["prompt"].partition(_PUBLIC_COPY_MARKER)[0]):
        for match in _RATIO.finditer(text):
            if (_CANVAS_BEFORE.search(text[:match.start()]) or _CANVAS_AFTER.match(text[match.end():])
                    or text.strip() == match[0]):
                ratios.add(":".join(match.groups()))
        for match in _ORIENTATION.finditer(text):
            word = match[0].lower()
            directions.add("portrait" if word.startswith(("竖", "portrait")) else
                           "landscape" if word.startswith(("横", "landscape")) else "square")
    if len(ratios) > 1 or len(directions) > 1:
        raise MediaBriefError("海报画幅存在冲突，请统一 media.aspect_ratio 与创作说明")
    direction = next(iter(directions), None)
    if not ratios:
        return {"portrait": "3:4", "landscape": "4:3", "square": "1:1"}.get(direction)
    ratio = ratios.pop()
    width, height = map(int, ratio.split(":"))
    if direction and direction != ("portrait" if width < height else "landscape" if width > height else "square"):
        raise MediaBriefError("海报方向与画幅冲突，请核对 media.aspect_ratio")
    return ratio


def poster_plan_issue(task):
    media = task.get("media")
    image = isinstance(media, dict) and media.get("type") == "image"
    if isinstance(media, dict) and "aspect_ratio" in media and not image:
        return f"{task.get('id', '?')}: 请 LD 仅在图像任务声明 media.aspect_ratio。"
    if "media_copy_from" in task:
        source = task["media_copy_from"]
        dependencies = task.get("depends_on", task.get("deps", []))
        if (not image or not isinstance(source, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", source)
                or source == task.get("id") or not isinstance(dependencies, list) or source not in dependencies):
            return f"{task.get('id', '?')}: 请 LD 将 media_copy_from 登记为海报任务的一项直接前置任务 ID。"
    if image:
        brief = media.get("prompt")
        if not isinstance(brief, str) or not brief.strip() or len(brief) > _MAX_PROMPT:
            return f"{task.get('id', '?')}: 请 LD 在 media.prompt 填写 1–10000 字的海报创作简报。"
        try:
            poster_ratio(media, task.get("prompt", ""))
        except MediaBriefError as error:
            return f"{task.get('id', '?')}: 请 LD 修正海报画幅：{error}"
        if _UNFINISHED.search(brief):
            return (f"{task.get('id', '?')}: 请 LD 替换仍待更新的海报提示词，写明可执行的创意与已确认公开文案，"
                    "或用 media_copy_from 指定公开文案来源。")
    return None


def _read_artifact(run_dir, filename):
    path = run_dir / filename
    try:
        entry = path.lstat()
        if run_dir.is_symlink() or not stat.S_ISREG(entry.st_mode) or entry.st_size > _MAX_ARTIFACT_BYTES:
            raise MediaBriefError("请 LD 提供本轮已放行的海报文案产物（1 MiB 以内的普通文件）。")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as handle:
            if not os.path.samestat(entry, os.fstat(handle.fileno())):
                raise MediaBriefError("海报文案产物已变动，请 LD 核对后重新放行。")
            raw = handle.read(_MAX_ARTIFACT_BYTES + 1)
        if len(raw) > _MAX_ARTIFACT_BYTES:
            raise MediaBriefError("海报文案产物过大，请 LD 单列公开文案。")
        return raw, raw.decode("utf-8").replace("\r\n", "\n")
    except (OSError, UnicodeError) as exc:
        raise MediaBriefError("无法读取本轮海报文案产物，请 LD 核对后重新放行。") from exc


def _public_copy(document):
    """Read one top-level, explicitly labelled Markdown fenced block."""
    fence = ""
    public = False
    found = False
    lines = []
    for line in document.splitlines():
        marker = _FENCE.match(line)
        if not fence:
            if marker:
                fence, label = marker.groups()
                public = fence.startswith("`") and label.strip() == "media-copy"
                if public and found:
                    raise MediaBriefError("公开文案存在多个 media-copy 块，请 LD 确认唯一终稿。")
                found = found or public
        elif marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
            fence = ""
            public = False
        elif public:
            if marker:
                raise MediaBriefError("media-copy 只能包含可公开的终稿文字，请 LD 移除嵌套围栏。")
            lines.append(line)
    copy = "\n".join(lines).strip()
    if not copy or public or _UNFINISHED.search(copy):
        raise MediaBriefError("请 LD 在已放行的前置产物中提供唯一、闭合且包含公开终稿的 ```media-copy 文案块。")
    return copy


def poster_prompt(task, data, run_dir):
    """Return only the creative brief and the current accepted public copy."""
    issue = poster_plan_issue(task)
    if issue:
        raise MediaBriefError(issue)
    brief = task["media"]["prompt"]
    ratio = task["media"].get("aspect_ratio")
    if ratio and ratio not in brief:
        brief += f"\n海报画幅：{ratio}。"
    if len(brief) > _MAX_PROMPT:
        raise MediaBriefError("创作简报和画幅超过 10000 字，请 LD 精简。")
    source = task.get("media_copy_from")
    if source is None:
        return brief

    job = data["tasks"].get(source, {})
    attempt = job.get("attempt", 1)
    decision = data.get("decisions", {}).get(job.get("decision"), {})
    if (job.get("state") != "completed" or type(attempt) is not int or attempt < 1
            or decision.get("kind") != "result" or decision.get("state") not in ("approved", "auto")
            or decision.get("task") != source or decision.get("attempt") != attempt):
        raise MediaBriefError(f"请 LD 核对 {source} 本轮已放行的产物后再派发海报。")

    from progress import artifact_name  # progress imports dispatch and its planning rules.
    filename = artifact_name(source, attempt, "md")
    if job.get("evidence") != filename or decision.get("evidence") != filename:
        raise MediaBriefError(f"海报文案来源与 {source} 本轮放行记录不匹配，请 LD 核对。")
    raw, document = _read_artifact(Path(run_dir), filename)
    if "evidence_sha256" in decision:
        verified = hashlib.sha256(raw).hexdigest() == decision["evidence_sha256"]
    else:
        # Older decisions retained at most 2400 characters of the released result.
        preview = decision.get("content")
        verified = isinstance(preview, str) and len(preview) < 2400 and document == preview + "\n"
    if not verified:
        raise MediaBriefError("海报文案与可核验的放行版本不一致，请 LD 重新交付公开文案。")

    copy = _public_copy(document)
    prompt = brief + _PUBLIC_COPY_MARKER + copy
    if len(prompt) > _MAX_PROMPT:
        raise MediaBriefError("创意与公开文案合计超过 10000 字，请 LD 精简。")
    return prompt
