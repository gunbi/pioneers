"""Project model controls shared by the dashboard and host dispatch processes."""

from contextlib import closing, contextmanager
import json
from pathlib import Path
import re
import sqlite3


MEDIA_MODES = ("image", "speech", "music", "sound_effect")
MODE_KINDS = {
    "text": "chat", "patch": "chat", "video": "text-to-video", "image": "text-to-image",
    "speech": "text-to-speech", "music": "text-to-music", "sound_effect": "text-to-sound-effect",
}
MODES = tuple(MODE_KINDS)
MODEL_ID = re.compile(r"~?[A-Za-z0-9][A-Za-z0-9_./:+-]{0,255}")
PROVIDER_ID = re.compile(r"[a-z][a-z0-9_-]{0,63}")

# These are preferred catalog IDs. Project pins and bans are stored separately.
SILICONFLOW_QUALITY = {
    "id": "siliconflow-quality",
    "label": "SiliconFlow 优先、质量优先",
    "provider": "siliconflow",
    "models": {
        "text": ["deepseek-ai/DeepSeek-V4-Pro"],
        "patch": ["zai-org/GLM-5.3", "moonshotai/Kimi-K2.7-Code"],
        "image": ["OpenMOSS-Team/MOSS-Image"],
        "video": ["MiniMaxAI/MiniMax-H3"],
        "speech": ["FunAudioLLM/CosyVoice2-0.5B"],
    },
}
MODEL_PRESETS = {SILICONFLOW_QUALITY["id"]: SILICONFLOW_QUALITY}


class PolicyConflict(ValueError):
    """The operator edited a stale dashboard snapshot."""


def banned(policy, provider, model):
    return provider in policy.get("banned_providers", ()) or _model_banned(policy, provider, model)


def _model_banned(policy, provider, model):
    blocked = [entry["model"] for entry in policy.get("banned", []) if entry["provider"] == provider]
    if provider != "openrouter":
        return model in blocked
    if not blocked:
        return False
    # Dynamic routers may choose any downstream model, including a banned one.
    if model.startswith(("openrouter/", "~")):
        return True
    return any(model.partition(":")[0] == other.partition(":")[0] for other in blocked)


def empty_policy():
    return {"revision": 0, "selected": {}, "banned": [], "banned_providers": [],
            "provider_ban_reasons": {}, "preference": "", "default_preset": SILICONFLOW_QUALITY["id"]}


def active_preset(policy):
    # An explicit preset (including None for general routing) overrides the project default.
    default = None if policy.get("preference", "").strip() else policy.get("default_preset")
    return MODEL_PRESETS.get(policy.get("preset", default))


def initialize_model_policy(registry):
    """Persist the project-wide default on startup without resetting operator choices."""
    with _policy_transaction(registry) as db:
        row = db.execute("SELECT data FROM policy WHERE id=1").fetchone()
        if row and "default_preset" in json.loads(row[0]):
            return
        db.execute("INSERT OR REPLACE INTO policy VALUES (1,?)", (json.dumps(_read(db), ensure_ascii=False),))


def _policy_path(registry):
    path = Path(registry) / "model-policy.sqlite3"
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("模型策略必须保存在项目登记目录内")
    return path


def _read(db):
    row = db.execute("SELECT data FROM policy WHERE id=1").fetchone()
    return {**empty_policy(), **json.loads(row[0])} if row else empty_policy()


def read_policy(registry):
    path = _policy_path(registry)
    if not path.exists():
        return empty_policy()
    with closing(sqlite3.connect(path)) as db:
        # Reader can arrive between sqlite3.connect and the initial transaction.
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy'").fetchone():
            return empty_policy()
        return _read(db)


@contextmanager
def _policy_transaction(registry):
    path = _policy_path(registry)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS preference_setup (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE IF NOT EXISTS provider_decisions (provider TEXT PRIMARY KEY, revision INTEGER NOT NULL)")
        yield db


@contextmanager
def offer_model_preference(registry, providers=()):
    """Mark the question delivered only once the caller has flushed the response."""
    with _policy_transaction(registry) as db:
        policy = _read(db)
        if policy["preference"] or db.execute("SELECT 1 FROM preference_setup WHERE id=1").fetchone():
            db.commit()
            yield None
            return
        db.execute("INSERT INTO preference_setup VALUES (1)")
        presets = [preset for preset in MODEL_PRESETS.values() if preset["provider"] in providers]
        choices = "、".join("「" + preset["label"] + "」" for preset in presets)
        question = "这个项目希望怎么选模型？"
        if choices:
            question += f"可以选内置策略{choices}，按任务类型优先使用推荐型号，允许选择收费模型。"
        question += "项目默认先尝试可用的 SiliconFlow，允许免费和收费模型。也可以说「仅免费」「优先速度」或「优先质量」。"
        offer = {"action": "ask", "revision": policy["revision"], "question": question}
        if presets:
            offer["presets"] = presets
        yield offer


def update_policy(registry, request):
    fields = {"select": ("mode", "provider", "model"), "clear": ("mode",),
              "ban": ("provider", "model"), "unban": ("provider", "model"),
              "ban_provider": ("provider",), "unban_provider": ("provider",),
              "preference": ("value",), "preset": ("value",)}
    action = request.get("action")
    if not isinstance(action, str) or action not in fields or set(request) != {"action", "revision", *fields[action]}:
        raise ValueError("未知模型规则或字段")
    revision = request["revision"]
    if type(revision) is not int or revision < 0:
        raise ValueError("需要有效的规则版本")
    mode = request.get("mode")
    if "mode" in request and mode not in MODES:
        raise ValueError("未知的派工类型")
    if action in ("ban_provider", "unban_provider"):
        from model_plugins import PROVIDERS
        provider = request["provider"]
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ValueError("请选择已接入的模型提供商")
    if "model" in request:
        model, provider = request["model"], request["provider"]
        if (not isinstance(model, str) or not MODEL_ID.fullmatch(model)
                or not isinstance(provider, str) or not PROVIDER_ID.fullmatch(provider)):
            raise ValueError("无效的供应商或模型 ID")
        ref = {"provider": provider, "model": model}
    if action == "preference" and (not isinstance(request["value"], str) or len(request["value"]) > 1000):
        raise ValueError("模型倾向应为 1000 字以内的文本")
    if action == "preset" and request["value"] is not None:
        if not isinstance(request["value"], str) or request["value"] not in MODEL_PRESETS:
            raise ValueError("未知的内置选模策略")
    with _policy_transaction(registry) as db:
        policy = _read(db)
        if revision != policy["revision"]:
            raise PolicyConflict("规则已被其他操作更新，请刷新后重试")
        if action == "select":
            if banned(policy, ref["provider"], ref["model"]):
                raise ValueError("不能选择已禁用的模型")
            policy["selected"][mode] = ref
        elif action == "clear":
            policy["selected"].pop(mode, None)
        elif action == "ban":
            if ref not in policy["banned"]:
                policy["banned"].append(ref)
            policy["selected"] = {k: v for k, v in policy["selected"].items() if not _model_banned(policy, v["provider"], v["model"])}
        elif action == "unban":
            policy["banned"] = [v for v in policy["banned"] if v != ref]
        elif action == "ban_provider":
            if provider not in policy["banned_providers"]:
                policy["banned_providers"].append(provider)
        elif action == "unban_provider":
            policy["banned_providers"] = [name for name in policy["banned_providers"] if name != provider]
        elif action == "preference":
            policy["preference"] = request["value"].strip()
        else:
            policy["preset"] = request["value"]
        if action in ("preference", "preset"):
            # Saving an explicit choice finishes preference onboarding, including the default.
            db.execute("INSERT OR IGNORE INTO preference_setup VALUES (1)")
        if action in ("ban_provider", "unban_provider"):
            policy["provider_ban_reasons"].pop(provider, None)
            db.execute("INSERT OR REPLACE INTO provider_decisions VALUES (?,?)", (provider, revision + 1))
        return _save(db, policy)


def _save(db, policy):
    policy["revision"] += 1
    db.execute("INSERT OR REPLACE INTO policy VALUES (1,?)", (json.dumps(policy, ensure_ascii=False),))
    return policy


def ban_insufficient_credit(registry, provider, observed_revision):
    """Merge a verified provider error unless a newer manual decision supersedes that request."""
    with _policy_transaction(registry) as db:
        policy = _read(db)
        if provider in policy["banned_providers"]:
            return True
        decision = db.execute("SELECT revision FROM provider_decisions WHERE provider=?", (provider,)).fetchone()
        if decision and decision[0] > observed_revision:
            return False
        policy["banned_providers"].append(provider)
        policy["provider_ban_reasons"][provider] = "insufficient_credit"
        _save(db, policy)
        return True
