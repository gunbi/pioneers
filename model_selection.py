"""Project catalog discovery and deterministic shortlists for host dispatch."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from model_policy import MODEL_ID, MODE_KINDS, banned
from skill_sources import read_json, write_json


CACHE_SECONDS = 600
RETRY_SECONDS = 30
MAX_CANDIDATES = 8


def _fresh(path):
    saved = read_json(path)
    if not isinstance(saved.get("models"), list):
        return None
    try:
        stamp = datetime.fromisoformat(saved["updated_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
    except (KeyError, ValueError, TypeError, AttributeError):
        return None
    return saved["models"] if 0 <= age < CACHE_SECONDS else None


def _load_provider(registry, name, provider):
    path = registry / ("model-catalog-" + name + ".json")
    cached = _fresh(path)
    if cached is not None:
        return cached

    # Coordinate separate CLI processes. Only the ownership change holds a transaction.
    # Expiry also permits recovery when the updating process exits unexpectedly.
    registry.mkdir(parents=True, exist_ok=True)
    owner = uuid4().hex
    with closing(sqlite3.connect(registry / "model-catalog-refresh.sqlite3")) as db:
        now = time.time()
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS refresh (provider TEXT PRIMARY KEY, owner TEXT, until REAL)")
            changed = db.execute(
                "INSERT INTO refresh VALUES (?,?,?) ON CONFLICT(provider) DO UPDATE SET "
                "owner=excluded.owner, until=excluded.until WHERE refresh.until <= ?",
                (name, owner, now + 180, now))
        if not changed.rowcount:
            # Give a concurrent first refresh a chance to populate the shared cache.
            # Failed refreshes carry a retry timestamp and can fall back immediately.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                cached = _fresh(path)
                entry = db.execute("SELECT owner FROM refresh WHERE provider=?", (name,)).fetchone()
                if cached is not None or not entry or not entry[0]:
                    return cached or []
                time.sleep(0.1)
            return []
        refreshed = False
        try:
            cached = _fresh(path)
            if cached is None:
                cached = provider.list_catalog()
                if not isinstance(cached, list):
                    raise ValueError("Invalid model directory")
                write_json(path, {"models": cached, "updated_at": datetime.now(timezone.utc).isoformat()})
            refreshed = True
            return cached
        except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            return []
        finally:
            retry_at = 0 if refreshed else time.time() + RETRY_SECONDS
            with db:
                db.execute("UPDATE refresh SET owner='', until=? WHERE provider=? AND owner=?",
                           (retry_at, name, owner))


def load_catalogs(registry, *, cached_only=False):
    from model_plugins import PROVIDERS  # The dispatch module also consumes this shortlist.

    connected = {}
    for name, provider in PROVIDERS.items():
        try:
            provider.api_key()
        except ValueError:
            continue
        connected[name] = provider
    if not connected:
        return {}
    if cached_only:
        return {name: _fresh(Path(registry) / ("model-catalog-" + name + ".json")) or [] for name in connected}
    catalogs = {}
    with ThreadPoolExecutor(max_workers=len(connected)) as pool:
        pending = {name: pool.submit(_load_provider, Path(registry), name, provider)
                   for name, provider in connected.items()}
        for name, future in pending.items():
            try:
                catalogs[name] = future.result()
            except (sqlite3.Error, OSError):
                catalogs[name] = []
    return catalogs


# Recognize explicit budget constraints separately from a soft price preference.
def requires_free(preference):
    text = str(preference or "").casefold()
    text = re.sub(r"不(?:仅|只|只能|只限于)免费|不限(?:于)?免费|不要求(?:仅)?免费|"
                  r"\bnot (?:free(?: models?)? only|(?:limited to|only) free)\b", "", text)
    return bool(re.search(
        r"(?:只能|只|仅(?:限)?|必须|一律)(?:允许|接受|使用|用|选用|选择|考虑|采用|支持|是|的|要|选|可|能|跑|模型|已确认|确定|完全|真正|绝对| )*免费"
        r"|免费(?:模型)?(?:才可以|才行|限定)|(?:禁止|拒绝|不能|不可|不许|不允许|不接受|不用|不要|不使用|不准|不得|避免|不选)"
        r"(?:调用|使用|选择|任何|所有|用|带|的| )*(?:收费|付费)|零预算|不花钱|不要花钱"
        r"|\b(?:must (?:be|use) free|zero budget|(?:only|just)\s+(?:(?:choose|use|select|allow)\s+)?(?:a |the )?free"
        r"|free(?:[- ](?:tier|models?|routes?))?[- ]only"
        r"|(?:do not|don't|no|never|avoid|disallow)(?:\s+(?:use|allow|select|any))*\s+paid)\b", text))


def _rate(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool) or len(str(value)) > 64:
        return None
    try:
        price = Decimal(str(value))
        return price if price.is_finite() and price >= 0 else None
    except InvalidOperation:
        return None


def confirmed_free(provider, row, mode):
    """Require evidence for the exact SKU and the requested generation mode."""
    if not isinstance(row, dict) or not isinstance(row.get("kinds"), list):
        return False
    model = row.get("id", "")
    if (provider != "openrouter" or not isinstance(model, str) or not MODEL_ID.fullmatch(model)
            or model.startswith(("openrouter/", "~")) or MODE_KINDS.get(mode) not in row.get("kinds", [])):
        return False
    pricing = row.get("pricing", {})
    skus = row.get("pricing_skus", {})
    if not isinstance(pricing, dict) or not isinstance(skus, dict):
        return False
    # Unknown fields, tier overrides, invalid values or any positive charge veto free status.
    if any(_rate(value) != 0 for rates in (pricing, skus) for value in rates.values()):
        return False
    if model.endswith(":free"):
        return True
    if mode in ("text", "patch"):
        if any(kind in row["kinds"] for kind in ("image", "audio", "video")):
            return False
        return "prompt" in pricing and "completion" in pricing
    if mode == "video" and any(key.startswith(("duration_seconds", "video_tokens")) for key in skus):
        return True
    output_rates = {"image": ("image_output",), "speech": ("audio_output",), "music": ("audio_output",),
                    "sound_effect": ("audio_output",), "video": ("video_output",)}
    return any(key in pricing for key in output_rates.get(mode, ())) and "prompt" in pricing


TRAITS = {
    "fast": r"\b(?:flash|fast|turbo|lite|mini|small|haiku|speed|lightweight|low[- ]latency)\b|快速|低延迟|高速|轻量",
    "reasoning": r"\b(?:reasoning|reasoner|thinking|reasoned)\b|推理|深度思考",
    "quality": r"\b(?:quality|flagship|frontier|pro|max|opus|large)\b|高质量|质量|旗舰|高品质",
    "coding": r"\b(?:coding|coder|code|programming|debugging)\b|编程|代码|补丁",
    "chinese": r"\b(?:chinese|mandarin)\b|中文|汉语",
    "translation": r"\b(?:translation|translate|multilingual)\b|翻译|多语言",
    "math": r"\b(?:math|mathematics|mathematical)\b|数学",
    "writing": r"\b(?:writing|storytelling)\b|写作|叙事",
    "photorealism": r"\b(?:photo[- ]?realistic|photorealism|photograph)\b|摄影|写实",
    "long-context": r"\blong[- ]context\b|长上下文|长文档",
}
TRAIT_LABELS = {"fast": "速度", "quality": "质量", "reasoning": "推理", "coding": "编程", "long-context": "长上下文",
                "chinese": "中文", "translation": "翻译", "math": "数学", "writing": "写作", "photorealism": "写实图像"}
BUDGET = re.compile(r"便宜|低价|省钱|低成本|性价比|经济|廉价|免费|\b(?:cheap|cheapest|budget|affordable|free|low[- ]cost)\b", re.I)
NEGATE = re.compile(r"不(?:要|使用|用|喜欢|选|希望)|排除|避免|禁止|慎用|\b(?:avoid|not|never|except|exclude)\b", re.I)


def _traits(text):
    return {tag for tag, pattern in TRAITS.items() if re.search(pattern, text, re.I)}


def _score(row, provider, mode, level, preference, task_traits, labels):
    name = row["id"]
    description = row.get("description")
    description = " ".join(description.split())[:400] if isinstance(description, str) else ""
    context = row.get("context_length")
    directory_tags = row.get("tags") if isinstance(row.get("tags"), list) else []
    tags = [tag for tag in [*labels, *directory_tags] if isinstance(tag, str) and 0 < len(tag) <= 64]
    text = " ".join([name.replace("_", "-"), description, *tags]).casefold()
    traits = _traits(text)
    if type(context) is int and context >= 128000:
        traits.add("long-context")
    free = confirmed_free(provider, row, mode)
    score = 0
    reasons = []
    if level == "low" and "fast" in traits:
        score += 6
        reasons.append("轻量任务匹配快速模型线索")
    if level == "high" and traits & {"reasoning", "quality", "long-context"}:
        score += 4 * len(traits & {"reasoning", "quality", "long-context"})
        reasons.append("复杂任务匹配推理、质量或上下文线索")
    matched_task = task_traits & traits
    if matched_task:
        score += 7 * len(matched_task)
        reasons.append("任务匹配" + "、".join(TRAIT_LABELS[tag] for tag in sorted(matched_task)))

    # Evaluate clauses independently, so 'prefer A, avoid B' can affect both families.
    identifiers = {provider, name.casefold(), *re.findall(r"[a-z][a-z0-9]{2,}", name.casefold()),
                   *(tag.casefold() for tag in tags)}
    if provider == "siliconflow":
        identifiers.add("硅基流动")
    budget = False
    for clause in re.split(r"[,，。.;；]|\bbut\b|但是|但", preference.casefold()):
        negative = bool(NEGATE.search(clause))
        matched = _traits(clause) & traits
        by_name = any((identifier in clause if not identifier.isascii() else
                       re.search(r"(?<![a-z0-9])" + re.escape(identifier) + r"(?![a-z0-9])", clause))
                      for identifier in identifiers)
        if by_name or matched:
            score += (-1 if negative else 1) * ((24 if by_name else 0) + 12 * len(matched))
            reason = "供应商、型号或项目标签" if by_name else "、".join(TRAIT_LABELS[t] for t in sorted(matched))
            reasons.append(("避开" if negative else "匹配") + "项目倾向中的" + reason)
        if BUDGET.search(clause) and not negative:
            budget = True
    if budget and free:
        score += 32
        reasons.append("目录可确认免费")
    candidate = {"provider": provider, "model": name, "free": free,
                 "reason": "；".join(dict.fromkeys([f"支持 {mode}", *reasons]))}
    if description:
        candidate["description"] = description
    if type(context) is int and context > 0:
        candidate["context_length"] = context
    if tags:
        candidate["tags"] = sorted(set(tags))
    # Compare like-for-like text token prices only; absent media units are not zero.
    cost = Decimal("Infinity")
    pricing = row.get("pricing")
    if budget and mode in ("text", "patch") and isinstance(pricing, dict):
        rates = [_rate(pricing.get(key)) for key in ("prompt", "completion")]
        if all(rate is not None and rate < 1 for rate in rates):
            cost = sum(rates)
            candidate["reason"] += "；参考目录输入和输出 token 单价"
    return (-score, cost, provider, name), candidate


def rank_candidates(catalogs, task, mode, policy, profiles=(), *, limit=MAX_CANDIDATES):
    """Shortlist by default; claim can traverse the full order with limit=None."""
    kind = MODE_KINDS.get(mode)
    if not kind:
        return []
    preference = policy.get("preference") if isinstance(policy.get("preference"), str) else ""
    only_free = requires_free(preference)
    complexity = task.get("complexity")
    level = complexity.get("level", "medium") if isinstance(complexity, dict) else "medium"
    task_text = " ".join(str(task.get(k, "")) for k in ("prompt", "title"))
    task_traits = _traits(task_text)
    if mode == "patch":
        task_traits.add("coding")
    labels = {}
    for spec in profiles:
        if spec.get("mode") == mode:
            labels.setdefault((spec["provider"], spec["model"]), []).extend([spec["name"], *spec.get("tags", [])])
    ranked = []
    for provider, rows in catalogs.items():
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("id")
            if (not isinstance(name, str) or not MODEL_ID.fullmatch(name) or name in seen
                    or not isinstance(row.get("kinds"), list) or kind not in row["kinds"]
                    or banned(policy, provider, name) or (only_free and not confirmed_free(provider, row, mode))):
                continue
            seen.add(name)
            ranked.append(_score(row, provider, mode, level, preference, task_traits, labels.get((provider, name), [])))
    ranked.sort(key=lambda item: item[0])
    return [candidate for _, candidate in ranked[:limit]]
