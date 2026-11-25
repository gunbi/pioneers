"""Local, per-project credential-file bindings for provider requests."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Optional

from skill_sources import read_json, write_json

_active = ContextVar("pioneers_provider_credentials", default=None)
_assignment = re.compile(r"(?:export[ \t]+)?([A-Za-z_][A-Za-z_0-9]*)[ \t]*=[ \t]*(.*)")
_value = re.compile(r"""(?:"([^"]*)"|'([^']*)'|([^\s'"]+))\s*(?:\s\#.*)?""")
_invalid_file = "Cannot read the configured provider env file. Check the file or configure it again."
PROVIDER_SETUP = "provider-setup.json"


def _binding(registry):
    # The registry is derived from the resolved workspace or the shared Git
    # directory. A symlink inside a checkout must not select another binding.
    identity = sha256(os.fsencode(os.path.normcase(os.path.abspath(registry)))).hexdigest()
    return Path.home() / ".pioneers" / "provider-bindings" / (identity + ".json")


def _read_keys(path, allowed):
    # Nonblocking open also lets us reject a FIFO without waiting for a writer.
    with open(path, "rb", opener=lambda p, f: os.open(p, f | getattr(os, "O_NONBLOCK", 0))) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(_invalid_file)
        raw = stream.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            raise ValueError(_invalid_file)
    keys = {}
    for line in raw.decode("utf-8-sig").splitlines():
        assignment = _assignment.fullmatch(line.strip())
        if not assignment or assignment[1] not in allowed:
            continue
        name = assignment[1]
        match = _value.fullmatch(assignment[2])
        value = next((v for v in match.groups() if v is not None), "") if match else ""
        if (not value or not value.isascii() or not value.isprintable() or any(c.isspace() for c in value)
                or any(c in value for c in ("$", "`"))):
            raise ValueError(f"Invalid assignment to {name} in the provider env file")
        keys[name] = value
    if not keys:
        raise ValueError("The provider env file has no supported API key assignments")
    return keys, (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _key_names():
    from model_plugins import PROVIDERS
    return {provider.key_env for provider in PROVIDERS.values()}


def _check_store(binding):
    folder = binding.parent
    if (folder.is_symlink() or folder.parent.is_symlink() or binding.is_symlink() or
            (os.name == "posix" and folder.exists() and
             (folder.stat().st_uid != os.getuid() or folder.stat().st_mode & 0o077))):
        raise ValueError("Pioneers provider bindings require a private user directory")


def configure_provider_env(registry, filename):
    """Atomically store a file reference in user-private state, or remove it."""
    binding = _binding(registry)
    _check_store(binding)
    if filename is None:
        binding.unlink(missing_ok=True)
        return {"configured": False}
    filename = os.fspath(filename)
    if filename.startswith("～/"):
        filename = "~/" + filename[2:]
    try:
        path = Path(filename).expanduser().absolute()
        keys, _ = _read_keys(path, _key_names())
    except (OSError, UnicodeError, RuntimeError):
        raise ValueError(_invalid_file) from None
    binding.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_store(binding)
    with tempfile.TemporaryDirectory(dir=binding.parent) as stage:
        temp = Path(stage) / "binding"
        with temp.open("x", encoding="utf-8") as out:
            json.dump(str(path), out)
        temp.chmod(0o600)
        os.replace(temp, binding)
    return {"configured": True, "variables": sorted(keys)}


@dataclass(frozen=True)
class ProjectCredentials:
    registry: Path
    keys: Optional[dict] = field(default=None, repr=False)
    revision: str = ""
    error: Optional[str] = None

    @classmethod
    def read(cls, registry):
        binding = _binding(registry)
        try:
            _check_store(binding)
            with binding.open(encoding="utf-8") as stream:
                binding_stat = os.fstat(stream.fileno())
                path = json.load(stream)
        except FileNotFoundError:
            return cls(registry)
        except (OSError, ValueError):
            return cls(registry, {}, "unavailable", _invalid_file)
        try:
            if not isinstance(path, str) or not os.path.isabs(path):
                raise ValueError(_invalid_file)
            keys, stamp = _read_keys(path, _key_names())
            revision = sha256(repr((binding_stat.st_ino, binding_stat.st_mtime_ns, stamp)).encode()).hexdigest()[:24]
            return cls(registry, keys, revision)
        except (OSError, ValueError):
            return cls(registry, {}, "unavailable", _invalid_file)


@contextmanager
def project_credentials(registry):
    registry = Path(registry)
    previous = _active.get()
    current = previous if previous and previous.registry == registry else ProjectCredentials.read(registry)
    token = _active.set(current)
    try:
        yield current
    finally:
        _active.reset(token)


def provider_key(name):
    current = _active.get()
    if current and current.error:
        raise ValueError(current.error)
    source = current.keys if current and current.keys is not None else os.environ
    return source.get(name, "").strip()


def has_provider_credentials():
    """Check credential format in the current project context without a provider request."""
    from model_plugins import PROVIDERS

    for provider in PROVIDERS.values():
        try:
            provider.api_key()
            return True
        except ValueError:
            continue
    return False


def skipped_without_credentials():
    current = _active.get()
    return (current is not None and
            read_json(current.registry / PROVIDER_SETUP).get("state") == "skipped" and not has_provider_credentials())


def offer_provider_setup(registry):
    """Emit a host conversation prompt at most once, even for concurrent first runs."""
    with project_credentials(registry):
        if has_provider_credentials():
            return None
    # Startup has already registered this run. Concurrent starts may both have
    # observed an empty history, but only one of them can create the receipt.
    try:
        with (registry / PROVIDER_SETUP).open("x", encoding="utf-8") as record:
            json.dump({"state": "offered"}, record)
    except FileExistsError:
        return None
    return {"action": "ask", "question": "这个项目还没有配置模型提供商。要现在配置 API Key 吗？可以提供本机密钥文件路径，或跳过并直接使用宿主。"}


def skip_provider_setup(registry):
    registry.mkdir(parents=True, exist_ok=True)
    write_json(registry / PROVIDER_SETUP, {"state": "skipped"})
    return {"provider_setup": "skipped"}


def catalog_path(registry, provider):
    current = _active.get()
    suffix = "-" + current.revision if current and current.revision else ""
    return Path(registry) / f"model-catalog-{provider}{suffix}.json"
