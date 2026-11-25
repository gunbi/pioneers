from contextlib import contextmanager
from html import escape
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
from threading import Thread
from time import monotonic
from urllib.parse import quote, urlsplit
import webbrowser

from iterations import iteration_summary, project_snapshot, registered_run
from progress import STATUS_LABELS, artifact_name
from dispatch import dispatch_summary, workflow_path
from model_plugins import PROVIDERS
from model_board import MEDIA_FILENAME, ModelBoard, media_reference


ARTIFACT_PATH = re.compile(r"/evidence/([a-z0-9][a-z0-9_-]*(?:\.[0-9]+)?\.(?:md|json))")
RETURN_ACTIVITY = re.compile(r"退回 ([a-z0-9][a-z0-9_-]*) · ")
LOOPBACK_HOST = re.compile(r"(?:127\.0\.0\.1|localhost)(?::([0-9]{1,5}))?")
CLOUD_CONTROL_SECONDS = 3600


def preview_origin(value):
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    match = re.fullmatch(rf"https://({label}(?:\.{label})*)(?::([0-9]{{1,5}}))?/?", value.lower())
    if not match or (match[2] and not 1 <= int(match[2]) <= 65535):
        raise ValueError("Use an HTTPS preview origin without a path, query or fragment")
    port = int(match[2]) if match[2] else 443
    return f"https://{match[1]}" + (f":{port}" if port != 443 else "")


def collaboration_flow(data):
    tasks = data["tasks"]
    events = data.get("events", [])
    nodes = {}
    for index, event in enumerate(events):
        task = event.get("task")
        if task not in tasks:
            continue
        key = (task, event.get("attempt", 1))
        start = nodes[key]["start"] if key in nodes else index
        nodes[key] = {**event, "start": start, "end": index, "role": tasks[task]["role"]}
    for task, current in tasks.items():
        key = (task, current.get("attempt", 1))
        event = nodes.get(key, {"start": len(events), "end": len(events)})
        nodes[key] = {**event, **current, "task": task}

    ordered = sorted(nodes, key=lambda key: nodes[key]["start"])
    edges = []
    for key in ordered:
        node = nodes[key]
        node["key"] = f"{key[0]}.{key[1]}"
        node["attempt"] = key[1]
    for key in ordered:
        node = nodes[key]
        if node["state"] != "returned":
            continue
        handoff = node.get("handoff")
        if handoff:
            target = (handoff["task"], handoff["attempt"])
        else:
            # Runs before structured handoffs logged the target in the return event.
            match = RETURN_ACTIVITY.match(node["activity"])
            target = next((k for k in ordered if match and k[0] == match[1]
                           and nodes[k]["start"] > node["end"]), None)
        if target in nodes:
            edges.append({"from": node["key"], "to": nodes[target]["key"], "kind": "return"})

    repaired = {edge["to"] for edge in edges}
    for key in ordered:
        node = nodes[key]
        if node["key"] in repaired:
            continue
        for dependency in tasks[key[0]]["depends_on"]:
            candidates = [k for k in ordered if k[0] == dependency and nodes[k]["start"] <= node["start"]]
            if not candidates:
                continue
            completed = [k for k in candidates if nodes[k]["state"] == "completed" and nodes[k]["end"] <= node["start"]]
            if node["state"] == "pending":
                source = nodes[candidates[-1]]
            elif completed:
                source = nodes[completed[-1]]
            else:
                continue
            edges.append({"from": source["key"], "to": node["key"],
                          "kind": "handoff" if source["state"] == "completed" else "waiting"})

    stages = [nodes[key] for key in ordered]
    if data.get("release"):
        release = data["release"]
        release_event = next((event for event in reversed(events) if event["state"] == release["state"]
                              and not event.get("task") and event["role"] == "ld"), {})
        stages.append({**release_event, **release, "key": "release", "role": "ld"})
        destinations = {dep for task in tasks.values() for dep in task["depends_on"]}
        for task, current in tasks.items():
            if task not in destinations:
                source = nodes[(task, current.get("attempt", 1))]
                edges.append({"from": source["key"], "to": "release",
                              "kind": "handoff" if current["state"] == "completed" else "waiting"})
    return {"nodes": stages, "edges": edges}


def flow_with_approvals(flow, decisions):
    by_stage = {}
    for decision in decisions.values():
        stage = "release" if decision["kind"] == "release" else f"{decision['task']}.{decision['attempt']}"
        placement = "after" if decision["kind"] == "result" else "before"
        by_stage[(stage, placement)] = decision
    before = {}
    after = {}
    nodes = []
    for stage in flow["nodes"]:
        key = stage["key"]
        for placement, group in (("before", before), ("after", after)):
            decision = by_stage.get((key, placement))
            if not decision or decision["state"] == "auto":
                continue
            if decision["state"] == "superseded" and decision.get("previous_state") not in ("waiting", "approved"):
                continue
            state = "awaiting_confirmation" if decision["state"] == "waiting" else decision["state"]
            group[key] = {
                "key": f"approval.{decision['id']}", "stage": key, "node_type": "approval",
                "decision": decision["id"], "kind": decision["kind"], "state": state,
                "activity": decision["reason"], "at": decision.get("at"), "evidence": decision.get("evidence"),
            }
        nodes.extend(part for part in (before.get(key), stage, after.get(key)) if part)
    edges = [{**edge, "from": after.get(edge["from"], {}).get("key", edge["from"]),
              "to": before.get(edge["to"], {}).get("key", edge["to"])} for edge in flow["edges"]]
    edges.extend({"from": gate["key"], "to": stage, "kind": "approval"} for stage, gate in before.items())
    edges.extend({"from": stage, "to": gate["key"], "kind": "approval"} for stage, gate in after.items())
    return {"nodes": nodes, "edges": edges}


def read_dashboard_status(status):
    data = json.loads(status.read_bytes())
    for task_id, task in data["tasks"].items():
        for executor in task.get("executors", {}).values():
            if "media" in executor:
                executor["media"] = media_reference(task_id, executor)
        for attempt in task.get("execution", {}).values():
            if "checkpoint" in attempt:
                attempt["checkpoint"].pop("session_id", None)
    return data


def dashboard_snapshot(status):
    data = read_dashboard_status(status)
    tasks = data["tasks"]
    records = [(task_id, task) for task_id, task in tasks.items()]
    records.extend((event.get("task"), event) for event in data.get("events", []))
    artifacts = set()
    for task_id, record in records:
        evidence = record.pop("evidence", None)
        if task_id not in tasks or record["state"] not in ("completed", "blocked", "returned", "judging", "awaiting_confirmation"):
            continue
        attempt = record.get("attempt", 1)
        for name in (artifact_name(task_id, attempt, "md"), artifact_name(task_id, attempt, "json")):
            activity = record["activity"]
            if evidence is None:
                # Older runs stored the filename at the end of the activity.
                for suffix in (f" · 证据：{name}", f"，见 {name}"):
                    if activity.endswith(suffix):
                        record["activity"] = activity[:-len(suffix)]
                        evidence = name
                        break
                if activity in (f"产物已保存 {name}", f"离线预览已保存 {name}"):
                    record["activity"] = activity[:-len(name) - 1]
                    evidence = name
            if evidence == name:
                record["evidence"] = evidence
                artifacts.add(evidence)
                break
    return data, artifacts


def evidence_page(name, text, *, video=False):
    title = escape(name)
    video_link = ""
    if video:
        url = next((line for line in text.splitlines() if re.fullmatch(r"https://[^\s<>\"']+", line)), None)
        if url:
            video_link = f'<p><a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">打开生成的视频</a></p>'
    return f"""<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Pioneers</title>
<style>
body {{ margin: 0; background: #f3f5f0; color: #202a25; font: 1rem/1.65 system-ui,sans-serif; }}
main {{ max-width: 65rem; margin: auto; padding: clamp(1rem,5vw,3rem); }}
a {{ color: #216044; }}
h1 {{ overflow-wrap: anywhere; font-size: clamp(1.45rem,4vw,2.25rem); }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; padding: 1rem;
       background: white; border: 1px solid #dce3dc; border-radius: .8rem; font-size: .95rem; }}
</style>
<main>
  <a href="../">返回运行看板</a>
  <h1>{title}</h1>
  {video_link}
  <pre>{escape(text)}</pre>
</main>
</html>
""".encode("utf-8")


def summary_markdown(summary):
    lines = ["# 迭代摘要", "", summary["goal"], "",
             f"{STATUS_LABELS[summary['state']]} · {summary['finished_at']}", "",
             "## 任务与验收", ""]
    for item in summary["results"]:
        lines.extend([f"### {item['role']} · {item['task']}（第 {item['attempt']} 次）", "", item["result"], ""])
        if item["evidence"]:
            lines.extend([f"[查看产物](evidence/{item['evidence']})", ""])
        if item["git"]:
            git = item["git"]
            lines.extend([f"Git：{git.get('branch', '')} · {git.get('commit', '')}", ""])
    if summary["approvals"]:
        lines.extend(["## 人工确认", ""])
        for item in summary["approvals"]:
            reason = item.get("operator_reason") or item["reason"]
            lines.append(f"- {item['id']} · {item['task'] or '发布'} · {item['by'] or '人工确认'} · {reason}")
    if summary["release"]:
        release = summary["release"]
        lines.extend(["", "## 发布", "", release["activity"]])
        if release.get("url"):
            lines.append(release["url"])
    return "\n".join(lines) + "\n"


def dashboard_server(run_dir, port, control=None, *, cloud_origin=None, project=None):
    if run_dir is not None:
        run_dir = run_dir.resolve()
        if not (run_dir / "status.json").is_file():
            raise ValueError(f"Run status does not exist: {run_dir}")
    page = Path(__file__).with_name("dashboard.html").read_bytes()
    project_page = Path(__file__).with_name("project-dashboard.html").read_bytes() if project else None
    model_board = None
    if project:
        workspace = project[2] if len(project) > 2 else None
        project = project[:2]
        model_board = ModelBoard(*project, workspace)
    project_prefix = "/project" if run_dir is not None else ""
    control_key = secrets.token_urlsafe(32) if cloud_origin else None
    key_deadline = monotonic() + CLOUD_CONTROL_SECONDS
    last_preview = None

    class DashboardHandler(BaseHTTPRequestHandler):
        def run_route(self, path):
            if project is None:
                return run_dir, path
            match = re.fullmatch(project_prefix + r"/runs/([^/]+)(/.*)", path)
            if match:
                return registered_run(project[0], match[1]), match[2]
            return run_dir, path

        def can_control(self, target_run):
            return control is not None and (run_dir is None or run_dir == target_run)

        def do_POST(self):
            url_path = urlsplit(self.path).path
            model_action = model_board is not None and url_path == project_prefix + "/model-action"
            target_run, path = self.run_route(url_path)
            if not model_action and (target_run is None or path != "/human-action"):
                self.send_error(404)
                return
            if (not self.can_control(target_run) or (model_action and run_dir is not None)):
                self.respond_json({"error": "当前入口仅供查看此轮。请使用该轮的独立看板或运行环境的命令行。"}, 404)
                return
            if not self.authorize_action():
                return
            code = 200
            try:
                size = int(self.headers.get("Content-Length", 0))
                if not 0 < size <= 128 * 1024 or self.headers.get_content_type() != "application/json":
                    raise ValueError("Send an application/json request up to 128 KiB")
                request = json.loads(self.rfile.read(size))
                if not isinstance(request, dict):
                    raise ValueError("Request must be a JSON object")
                result = model_board.apply(request) if model_action else control(target_run, request)
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                from model_policy import PolicyConflict
                code = 409 if isinstance(error, PolicyConflict) else 400
                result = {"error": str(error)}
            self.respond_json(result, code)

        def authorize_action(self):
            origin = self.headers.get("Origin")
            if cloud_origin:
                if origin != cloud_origin:
                    self.respond_json({"error": "请在新标签打开启动时配置的 HTTPS 预览地址，再提交操作。"}, 403)
                    return False
                key = self.headers.get("X-Pioneers-Control", "")
                if monotonic() >= key_deadline:
                    self.respond_json({"code": "control_expired",
                                       "error": "控制凭证已过期。请重启这个运行的看板以获取新凭证。"}, 401)
                    return False
                if not secrets.compare_digest(key.encode(), control_key.encode()):
                    self.respond_json({"code": "control_key",
                                       "error": "控制凭证缺失或无效。请使用当前看板启动终端中的凭证。"}, 401)
                    return False
                return True
            # SSH can forward the dashboard to a different browser-side loopback port.
            host = self.headers.get("Host")
            local = LOOPBACK_HOST.fullmatch(host or "")
            if local and (not local[1] or 1 <= int(local[1]) <= 65535) and origin == f"http://{host}":
                return True
            self.respond_json({"error": "当前入口无法提交人工操作（HTTP 403）。"
                                         "请直接打开本机看板或 SSH 转发后的本机地址，也可以在运行环境操作。"}, 403)
            return False

        def respond_json(self, payload, code):
            self.respond(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json", code)

        def serve_media(self, target_run, filename):
            match = MEDIA_FILENAME.fullmatch(filename)
            if not match or target_run.is_symlink() or (target_run / "status.json").is_symlink():
                self.send_error(404)
                return
            status = read_dashboard_status(target_run / "status.json")
            executors = status["tasks"].get(match[1], {}).get("executors", {})
            media = next((item["media"] for item in executors.values()
                          if item.get("media") and item["media"]["filename"] == filename), None)
            root = target_run.resolve()
            path = root / "media" / filename
            try:
                if not media or path.resolve(strict=True) != path:
                    raise FileNotFoundError
                if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
                    parent = os.open(path.parent, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
                    try:
                        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                    finally:
                        os.close(parent)
                    if not stat.S_ISREG(os.fstat(fd).st_mode):
                        os.close(fd)
                        raise FileNotFoundError
                    stream = os.fdopen(fd, "rb")
                else:
                    expected = path.lstat()
                    if not stat.S_ISREG(expected.st_mode):
                        raise FileNotFoundError
                    stream = path.open("rb")
                    if not os.path.samestat(expected, os.fstat(stream.fileno())):
                        stream.close()
                        raise FileNotFoundError
            except (OSError, RuntimeError):
                self.send_error(404)
                return
            with stream:
                opened = os.fstat(stream.fileno())
                if opened.st_size != media["bytes"]:
                    self.send_error(404)
                    return
                size = media["bytes"]
                start, end = 0, size - 1
                requested = self.headers.get("Range")
                if requested is not None:
                    bounds = re.fullmatch(r"bytes=([0-9]{0,19})-([0-9]{0,19})", requested)
                    if bounds and bounds[1]:
                        start = int(bounds[1])
                        if bounds[2]:
                            end = min(end, int(bounds[2]))
                    elif bounds and bounds[2]:
                        start = max(0, size - int(bounds[2]))
                    else:
                        start = size
                    if start > end:
                        self.send_response(416)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.end_headers()
                        return
                self.send_response(206 if requested is not None else 200)
                self.send_header("Content-Type", media["mime"])
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Accept-Ranges", "bytes")
                if requested is not None:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.end_headers()
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(128 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_GET(self):
            nonlocal last_preview
            path = urlsplit(self.path).path
            if model_board and path == project_prefix + "/models":
                self.respond(Path(__file__).with_name("model-dashboard.html").read_bytes(), "text/html")
                return
            if model_board and path == project_prefix + "/model-data.json":
                try:
                    snapshot = model_board.snapshot()
                    snapshot.update(read_only=run_dir is not None or control is None, cloud_control=bool(cloud_origin))
                    self.respond_json(snapshot, 200)
                except (ValueError, OSError, RuntimeError, sqlite3.Error) as error:
                    self.respond_json({"error": str(error)}, 400)
                return
            if project and path == project_prefix + "/":
                self.respond(project_page, "text/html")
                return
            if project and path == project_prefix + "/project-data.json":
                snapshot = project_snapshot(*project)
                if run_dir is not None:
                    for decision in snapshot["pending"]:
                        decision["read_only"] = decision["run"] != run_dir.name
                self.respond_json(snapshot, 200)
                return
            target_run, route = self.run_route(path)
            if target_run is None:
                self.send_error(404)
                return
            status = target_run / "status.json"
            if route.startswith("/media/"):
                self.serve_media(target_run, route[len("/media/"):])
                return
            if route == "/":
                content, kind = page, "text/html"
            elif route == f"/health/{quote(target_run.name, safe='')}":
                content, kind = target_run.name.encode("utf-8"), "text/plain"
            elif route == "/status.json":
                self.respond_json(read_dashboard_status(status), 200)
                return
            elif route == "/workflow.json":
                current = workflow_path(target_run, json.loads(status.read_bytes()))
                if current.is_symlink() or not current.is_file():
                    self.send_error(404)
                    return
                content, kind = current.read_bytes(), "application/json"
            elif route == "/summary.md":
                snapshot, _ = dashboard_snapshot(status)
                summary = iteration_summary(snapshot)
                if summary is None:
                    self.send_error(404)
                    return
                content, kind = summary_markdown(summary).encode("utf-8"), "text/plain"
            elif route == "/dashboard-data.json":
                snapshot, _ = dashboard_snapshot(status)
                if summary := iteration_summary(snapshot):
                    snapshot["summary"] = summary
                if project:
                    snapshot["project_href"] = "../../" if path != route else "project/"
                if run_dir is not None and target_run != run_dir:
                    snapshot["read_only"] = True
                if cloud_origin and self.can_control(target_run):
                    snapshot["cloud_control"] = True
                snapshot["flow"] = flow_with_approvals(collaboration_flow(snapshot), snapshot.get("decisions", {}))
                if snapshot["mode"] in ("host", "live"):
                    try:
                        workflow = json.loads(workflow_path(target_run, snapshot).read_bytes())
                        planned = {task["id"]: task for task in workflow["tasks"]}
                    except (OSError, json.JSONDecodeError):
                        planned = {}
                    tasks = {key: task for key, task in planned.items() if key in snapshot["tasks"]}
                    tasks.update({key: job for key, job in snapshot["tasks"].items() if key not in tasks})
                    snapshot["dispatch"] = dispatch_summary(snapshot, tasks)
                    if project and snapshot["mode"] == "host" and snapshot.get("workspace"):
                        # Render the host's routing rules without making provider requests on page polls.
                        from model_selection import load_catalogs
                        from provider_credentials import PROVIDER_SETUP, catalog_path, project_credentials
                        from pioneers import host_routing_summary
                        registry = project[0]
                        with project_credentials(registry) as source:
                            files = [status, workflow_path(target_run, snapshot), Path(snapshot["workspace"]) / ".pioneers/models.json",
                                     registry / "model-policy.sqlite3", registry / PROVIDER_SETUP,
                                     *(catalog_path(registry, p) for p in PROVIDERS)]
                            stamp = (target_run, source.revision, int(monotonic() // 10), tuple(p.stat().st_mtime_ns if p.exists() else None for p in files))
                            previous = last_preview
                            if previous and previous[0] == stamp:
                                snapshot["dispatch"] = previous[1]
                            else:
                                from functools import lru_cache
                                load = lru_cache(maxsize=1)(lambda: load_catalogs(registry, cached_only=True))
                                view = host_routing_summary(target_run, snapshot, tasks, snapshot["dispatch"], registry, load)
                                last_preview = (stamp, view)
                content, kind = json.dumps(snapshot, ensure_ascii=False).encode("utf-8"), "application/json"
            else:
                match = ARTIFACT_PATH.fullmatch(route)
                if match is None:
                    self.send_error(404)
                    return
                name = match[1]
                snapshot, artifacts = dashboard_snapshot(status)
                path = target_run / name
                if name not in artifacts or path.is_symlink() or not path.is_file():
                    self.send_error(404)
                    return
                try:
                    video = any(executor.get("mode") == "video" and name == artifact_name(task_id, int(attempt), "md")
                                for task_id, task in snapshot["tasks"].items()
                                for attempt, executor in task.get("executors", {}).items() if str(attempt).isdigit())
                    content, kind = evidence_page(name, path.read_text(encoding="utf-8", errors="replace"), video=video), "text/html"
                except FileNotFoundError:
                    self.send_error(404)
                    return
            self.respond(content, kind)

        def respond(self, content, kind, code=200):
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    if cloud_origin:
        print(f"Dashboard control key (valid for {CLOUD_CONTROL_SECONDS // 60} min): {control_key}",
              file=sys.stderr, flush=True)
    return server


def dashboard_url(server):
    return f"http://127.0.0.1:{server.server_port}/"


def announce(url):
    print(f"Dashboard: {url}", flush=True)


def dashboard_is_running(run_dir, port, *, project=False):
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        name = quote(run_dir.name, safe="")
        prefix = f"/runs/{name}" if project else ""
        connection.request("GET", f"{prefix}/health/{name}")
        response = connection.getresponse()
        expected = run_dir.name.encode("utf-8")
        return response.status == 200 and response.read(len(expected) + 1) == expected
    except (OSError, HTTPException):
        return False
    finally:
        connection.close()


def request_browser_open(url):
    def launch():
        try:
            if webbrowser.open(url):
                return
        except (OSError, webbrowser.Error):
            pass
        print(f"Browser launch unavailable. Open the dashboard manually: {url}", file=sys.stderr, flush=True)

    worker = Thread(target=launch, daemon=True)
    worker.start()
    return worker


def serve_dashboard(run_dir, port, control=None, *, on_ready=announce, open_browser=False, cloud_origin=None,
                    project=None):
    with dashboard_server(run_dir, port, control, cloud_origin=cloud_origin, project=project) as server:
        url = dashboard_url(server)
        on_ready(url)
        if open_browser:
            request_browser_open(url)
        server.serve_forever()


@contextmanager
def live_dashboard(run_dir, port, control=None, *, open_browser=False, cloud_origin=None, project=None):
    with dashboard_server(run_dir, port, control, cloud_origin=cloud_origin, project=project) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = dashboard_url(server)
            announce(url)
            if open_browser:
                request_browser_open(url)
            yield
        finally:
            server.shutdown()
            worker.join()
