"""Apply model-authored text edits after running project-owned tests in an isolated copy."""

import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import unicodedata


MAX_FILES = 24
MAX_BYTES = 192 * 1024
TEST_TIMEOUT = 120
PRIVATE_NAME = re.compile(
    r"(credential|secret|password|api[-_]?key|access[-_]?token|private[-_]?key|"
    r"^id_(rsa|dsa|ecdsa|ed25519)|^kubeconfig$|^env($|\.)|\.(pem|key|p8|p12|pfx|keystore|jks|env)$)", re.I
)
PRIVATE_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:sk-|gh[pousr]_)[A-Za-z0-9_-]{20,}")
SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"


def validate_paths(workspace, write_paths):
    if not isinstance(write_paths, list) or not 1 <= len(write_paths) <= MAX_FILES:
        raise ValueError("Patch mode needs an explicit list of files")
    seen = set()
    for name in write_paths:
        if not isinstance(name, str) or not name or not name.isprintable():
            raise ValueError("Invalid patch path")
        parts = name.split("/")
        key = unicodedata.normalize("NFC", name).casefold()
        if (key in seen or any(c in name for c in "\\:*?[]") or
                any(not p or p.startswith((".", "-")) or PRIVATE_NAME.search(p) for p in parts)):
            raise ValueError("Patch path is duplicated, protected or not a project-relative file")
        seen.add(key)
        current = Path(workspace)
        for index, part in enumerate(parts):
            current = current / part
            try:
                mode = current.lstat().st_mode
            except FileNotFoundError:
                continue
            if not (stat.S_ISREG(mode) if index == len(parts) - 1 else stat.S_ISDIR(mode)):
                raise ValueError("Patch paths must be regular files with local parent directories")
        if current.exists():
            info = current.stat()
            if info.st_nlink != 1 or info.st_size > MAX_BYTES:
                raise ValueError("Patch context does not support hard links or oversized files")


def _contents(workspace, names):
    validate_paths(workspace, names)
    files = {}
    total = 0
    tracked = set(_git(workspace, "--literal-pathspecs", "ls-files", "-z", "--", *names).split(b"\0"))
    for name in names:
        path = workspace / name
        if path.exists() and os.fsencode(name) not in tracked:
            raise ValueError("Only existing tracked files may be sent as patch context")
        body = path.read_bytes() if path.exists() else None
        if body is not None:
            total += len(body)
            value = body.decode("utf-8")
            if total > MAX_BYTES or "\x00" in value or PRIVATE_TEXT.search(value):
                raise ValueError("Patch context contains binary, sensitive or oversized data")
            files[name] = value
        else:
            files[name] = None
    return files


def patch_prompt(workspace, write_paths):
    files = _contents(Path(workspace), write_paths)
    return ("\n\n以下是获准修改的文本文件。null 表示新文件。只返回一个 JSON 对象，格式为"
            ' {"files":{"相对路径":"修改后的完整文本"}}，只列出有改动的文件。'
            "本地执行器会验证文件并运行项目指定的测试。\n" + json.dumps(files, ensure_ascii=False))


def _git(workspace, *args, data=None):
    # Model content never reaches shell, clean/smudge filters, hooks or the caller's secrets.
    env = {"PATH": SYSTEM_PATH, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_CONFIG_GLOBAL": os.devnull}
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "commit.gpgSign=false", "-C", str(workspace), *args]
    result = subprocess.run(command, input=data, capture_output=True, env=env)
    if result.returncode:
        raise ValueError("Patch Git step failed: " + args[0])
    return result.stdout


def _snapshot(workspace, target):
    # Use tracked regular files from HEAD. Keep the test process away from real Git
    # metadata, ignored credentials, and untracked dependencies in the host checkout.
    listing = _git(workspace, "ls-tree", "-rz", "HEAD")
    with subprocess.Popen(["git", "-C", str(workspace), "cat-file", "--batch"],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          env={"PATH": SYSTEM_PATH}) as blobs:
        try:
            for record in listing.split(b"\0"):
                if not record:
                    continue
                meta, filename = record.split(b"\t", 1)
                mode, _, oid = meta.split()
                parts = Path(os.fsdecode(filename)).parts
                if mode not in (b"100644", b"100755") or any(
                    p in (".git", ".pioneers", ".ssh", ".aws", ".env") or
                    p.startswith(".env.") or PRIVATE_NAME.search(p) for p in parts
                ):
                    continue
                dest = target.joinpath(*parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                blobs.stdin.write(oid + b"\n")
                blobs.stdin.flush()
                header = blobs.stdout.readline().split()
                if len(header) != 3 or header[0] != oid or header[1] != b"blob":
                    raise ValueError("Could not read a tracked file for the test snapshot")
                remaining = int(header[2])
                with dest.open("wb") as out:
                    while remaining:
                        chunk = blobs.stdout.read(min(remaining, 128 * 1024))
                        if not chunk:
                            raise ValueError("Truncated Git blob")
                        out.write(chunk)
                        remaining -= len(chunk)
                if blobs.stdout.read(1) != b"\n":
                    raise ValueError("Invalid Git blob response")
                if mode == b"100755":
                    dest.chmod(0o755)
        finally:
            blobs.stdin.close()


def test_sandbox():
    if sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return "sandbox-exec"
    if sys.platform.startswith("linux") and shutil.which("bwrap", path=SYSTEM_PATH):
        return "bwrap"
    raise ValueError("No supported test sandbox available; assign this task to the host")


def _test(workspace, scratch, command, *, supervised):
    if not isinstance(command, list) or not command or not all(isinstance(s, str) and s and "\0" not in s for s in command):
        raise ValueError("Patch mode needs a project-configured test command (argv)")
    if test_sandbox() == "sandbox-exec":
        # No network, IPC to host services, home directory or other working trees.
        runtime = ["/System/Library", "/usr/bin", "/usr/lib", "/usr/libexec", "/usr/sbin", "/usr/share", "/bin", "/sbin",
                   "/usr/local/bin", "/usr/local/lib", "/usr/local/Cellar", "/opt/homebrew/bin", "/opt/homebrew/lib",
                   "/opt/homebrew/Cellar", "/Library/Developer/CommandLineTools",
                   "/Applications/Xcode.app/Contents", "/Library/Apple", "/private/var/db/xcode_select_link",
                   "/dev/null", "/dev/random", "/dev/urandom"]
        roots = " ".join("(subpath " + json.dumps(path) + ")" for path in runtime)
        area = "(subpath " + json.dumps(str(scratch)) + ")"
        bootstrap = ('(import "dyld-support.sb")' if Path("/System/Library/Sandbox/Profiles/dyld-support.sb").exists()
                     else '(allow file-read* (literal "/"))')
        profile = ("(version 1)(deny default)" + bootstrap +
                   "(allow process-fork process-exec)(allow signal (target self))"
                   "(allow sysctl-read)(allow file-read-metadata)"
                   "(allow file-read* file-map-executable " + roots + " " + area + ")"
                   "(allow file-write* " + area + ' (literal "/dev/null"))')
        argv = ["/usr/bin/sandbox-exec", "-p", profile, *command]
        env_home = str(scratch / "tmp")
    else:
        argv = ["bwrap", "--unshare-all", "--die-with-parent", "--new-session"]
        for root in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc/ld.so.cache"):
            if Path(root).exists():
                argv += ["--ro-bind", root, root]
        argv += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--bind", str(workspace), "/work",
                 "--chdir", "/work", "--", *command]
        env_home = "/tmp"
    path = SYSTEM_PATH
    if sys.platform == "darwin":
        path = "/Applications/Xcode.app/Contents/Developer/usr/bin:/Library/Developer/CommandLineTools/usr/bin:" + path
    env = {"PATH": path, "HOME": env_home, "TMPDIR": env_home, "LANG": "C", "LC_ALL": "C"}
    try:
        with subprocess.Popen(argv, cwd=workspace, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=not supervised) as process:
            try:
                status = process.wait(timeout=TEST_TIMEOUT)
            finally:
                try:
                    if supervised:
                        # The host executor owns this whole group, including test descendants.
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    except subprocess.TimeoutExpired as error:
        raise ValueError("Configured sandboxed test timed out") from error
    if status != 0:
        raise ValueError(f"Configured sandboxed test failed (exit {status}); check command and sandbox availability")


def check_checkout(workspace, checkout):
    if (_git(workspace, "rev-parse", "HEAD").decode().strip() != checkout["base"]
            or _git(workspace, "branch", "--show-current").decode().strip() != checkout["branch"]):
        raise ValueError("Assigned Git branch or base has changed; reassign the patch")


def apply_patch_result(workspace, write_paths, content, test_command, *, supervised=False, checkout=None):
    workspace = Path(workspace)
    original = _contents(workspace, write_paths)
    fenced = re.fullmatch(r"```(?:json)?[ \t]*\n(.*)\n```", content.strip(), re.DOTALL | re.IGNORECASE)
    reply = json.loads(fenced[1] if fenced else content)
    if not isinstance(reply, dict) or set(reply) != {"files"} or not isinstance(reply["files"], dict):
        raise ValueError('Patch response must be a JSON object containing "files"')
    files = reply["files"]
    if not files or not files.keys() <= original.keys() or not all(
        isinstance(v, str) and "\x00" not in v for v in files.values()
    ):
        raise ValueError("Patch contains an invalid change or a file outside the assigned scope")
    if sum(len(s.encode("utf-8")) for s in files.values()) > MAX_BYTES:
        raise ValueError("Model patch exceeds the text size limit")
    changes = {name: value for name, value in files.items() if value != original[name]}
    if not changes:
        raise ValueError("Model response contains no changes")
    head = _git(workspace, "rev-parse", "HEAD")
    if checkout:
        check_checkout(workspace, checkout)
    if _git(workspace, "status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Patch mode requires a clean assigned worktree")
    with tempfile.TemporaryDirectory(prefix="pioneers-model-test-") as staging:
        scratch = Path(staging).resolve()
        copy = scratch / "project"
        copy.mkdir()
        (scratch / "tmp").mkdir()
        _snapshot(workspace, copy)
        for name, value in changes.items():
            candidate = copy / name
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_bytes(value.encode("utf-8"))
        _test(copy, scratch, test_command, supervised=supervised)

    # Install only the tested input. Sandbox-generated files never enter the commit.
    if checkout:
        check_checkout(workspace, checkout)
    if (_git(workspace, "rev-parse", "HEAD") != head or _contents(workspace, write_paths) != original
            or _git(workspace, "status", "--porcelain")):
        raise ValueError("Assigned files changed while the tests ran")
    created_dirs = []
    touched = []
    entries = bytearray()
    try:
        for name, value in changes.items():
            dest = workspace / name
            missing = []
            parent = dest.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                created_dirs.append(directory)
            touched.append(name)
            payload = value.encode("utf-8")
            # Replace rather than truncate any existing hard-linked inode.
            mode = dest.stat().st_mode & 0o777 if dest.exists() else 0o644
            with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
                draft = Path(temp.name)
                try:
                    temp.write(payload)
                    temp.flush()
                    draft.chmod(mode)
                    draft.replace(dest)
                finally:
                    draft.unlink(missing_ok=True)
            blob = _git(workspace, "hash-object", "-w", "--stdin", "--no-filters", data=payload).strip()
            git_mode = b"100755" if mode & 0o111 else b"100644"
            entries.extend(git_mode + b" " + blob + b"\t" + os.fsencode(name) + b"\0")
        _git(workspace, "update-index", "-z", "--index-info", data=entries)
        _git(workspace, "-c", "user.name=Pioneers", "-c", "user.email=pioneers@localhost",
             "commit", "--quiet", "-m", "Apply verified model patch")
    except BaseException:
        # The worktree was clean and belongs exclusively to this attempt.
        if touched:
            _git(workspace, "--literal-pathspecs", "reset", "--quiet", "HEAD", "--", *touched)
        for name in touched:
            if original[name] is None:
                (workspace / name).unlink(missing_ok=True)
            else:
                (workspace / name).write_bytes(original[name].encode("utf-8"))
        for directory in reversed(created_dirs):
            directory.rmdir()
        raise
    commit = _git(workspace, "rev-parse", "HEAD").decode().strip()
    return f"本地补丁已提交 {commit}\n文件：{', '.join(changes)}\n沙箱测试通过 (exit 0)：{json.dumps(test_command, ensure_ascii=False)}"
