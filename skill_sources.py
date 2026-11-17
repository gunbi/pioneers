"""Refresh upstream Markdown snapshots without running upstream code."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


SNAPSHOT = "skill-sources.json"
MAX_SOURCE_BYTES = 128 * 1024
PROMPT_EXCERPT = 6000


def source_url(repo, path):
    return f"https://github.com/{repo}/blob/main/{path}"


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path, data):
    with tempfile.TemporaryDirectory(dir=path.parent) as staging:
        draft = Path(staging) / path.name
        draft.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        draft.replace(path)


def refresh_source(repo, path, cache_dir, offline):
    url = source_url(repo, path)
    raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    cache = cache_dir / f"{sha256(url.encode()).hexdigest()}.json"
    saved = read_json(cache)
    if not isinstance(saved, dict) or saved.get("url") != url or not isinstance(saved.get("text"), str):
        saved = {}
    if not offline:
        headers = {"User-Agent": "pioneers-skill-sync"}
        if saved.get("etag"):
            headers["If-None-Match"] = saved["etag"]
        try:
            with urlopen(Request(raw_url, headers=headers), timeout=3) as response:
                body = response.read(MAX_SOURCE_BYTES + 1)
                etag = response.headers.get("ETag")
            content = body.decode("utf-8")
            if not content.strip() or len(body) > MAX_SOURCE_BYTES:
                raise ValueError("Unexpected upstream Markdown size")
            if path.endswith("/SKILL.md") and not content.startswith("---"):
                raise ValueError("Missing skill front matter")
            record = {"url": url, "text": content, "sha256": sha256(body).hexdigest(), "etag": etag,
                      "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            try:
                cache_dir.mkdir(parents=True, exist_ok=True)
                write_json(cache, record)
            except OSError:
                pass
            return {**record, "status": "updated"}
        except HTTPError as error:
            if error.code == 304 and saved:
                return {**saved, "status": "current"}
        except (URLError, OSError, ValueError):
            pass
    return {**saved, "url": url, "status": "cached" if saved else "unavailable"}


def refresh_run(run_dir, root, registry):
    sources = list(dict.fromkeys(tuple(source) for spec in registry.values() for source in spec.get("sources", [])))
    cache_dir = root / ".pioneers" / "skill-cache"
    offline = os.environ.get("PIONEERS_SKILLS_OFFLINE") == "1"
    work = [(repo, path, cache_dir, offline) for repo, path in sources]
    with ThreadPoolExecutor(max_workers=8) as pool:
        records = list(pool.map(lambda args: refresh_source(*args), work))
    path = run_dir / SNAPSHOT
    previous = read_json(path)
    snapshot = {}
    for record in records:
        url = record["url"]
        saved = previous.get(url, {})
        if record["status"] == "unavailable" and isinstance(saved, dict) and saved.get("text"):
            record = {**saved, "url": url, "status": "cached"}
        snapshot[url] = record
    write_json(path, snapshot)
    return {state: sum(record["status"] == state for record in snapshot.values())
            for state in ("updated", "current", "cached", "unavailable")}


def skill_references(spec, snapshot):
    parts = []
    for repo, path in spec.get("sources", []):
        url = source_url(repo, path)
        record = snapshot.get(url, {})
        content = record.get("text")
        if content:
            note = "，仅显示前 6000 字符" if len(content) > PROMPT_EXCERPT else ""
            parts.append(f"上游参考资料（{record['status']}{note}）\n{url}\n{content[:PROMPT_EXCERPT]}")
        else:
            parts.append(f"上游来源\n{url}")
    return parts
