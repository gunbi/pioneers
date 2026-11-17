from contextlib import contextmanager
from html import escape
from http.client import HTTPConnection, HTTPException
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import sqlite3
import sys
from threading import Thread
from time import monotonic
from urllib.parse import quote
import webbrowser

from progress import artifact_name
from dispatch import dispatch_summary, workflow_path


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


def dashboard_snapshot(status):
    data = json.loads(status.read_bytes())
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


def evidence_page(name, text):
    title = escape(name)
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
  <a href="/">返回运行看板</a>
  <h1>{title}</h1>
  <pre>{escape(text)}</pre>
</main>
</html>
""".encode("utf-8")


def dashboard_server(run_dir, port, control=None, *, cloud_origin=None):
    run_dir = run_dir.resolve()
    status = run_dir / "status.json"
    if not status.is_file():
        raise ValueError(f"Run status does not exist: {status}")
    page = Path(__file__).with_name("dashboard.html").read_bytes()
    control_key = secrets.token_urlsafe(32) if cloud_origin else None
    key_deadline = monotonic() + CLOUD_CONTROL_SECONDS

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/human-action":
                self.send_error(404)
                return
            if control is None:
                self.respond_json({"error": "这个看板没有启用人工操作。请使用运行环境的命令行。"}, 404)
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
                result = control(run_dir, request)
            except (ValueError, OSError, sqlite3.Error) as error:
                code = 400
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

        def do_GET(self):
            if self.path == "/":
                content, kind = page, "text/html"
            elif self.path == f"/health/{quote(run_dir.name, safe='')}":
                content, kind = run_dir.name.encode("utf-8"), "text/plain"
            elif self.path == "/status.json":
                content, kind = status.read_bytes(), "application/json"
            elif self.path == "/dashboard-data.json":
                snapshot, _ = dashboard_snapshot(status)
                if cloud_origin:
                    snapshot["cloud_control"] = True
                snapshot["flow"] = flow_with_approvals(collaboration_flow(snapshot), snapshot.get("decisions", {}))
                if snapshot["mode"] in ("host", "live"):
                    try:
                        workflow = json.loads(workflow_path(run_dir, snapshot).read_bytes())
                        planned = {task["id"]: task for task in workflow["tasks"]}
                    except (OSError, json.JSONDecodeError):
                        planned = {}
                    tasks = {key: task for key, task in planned.items() if key in snapshot["tasks"]}
                    tasks.update({key: job for key, job in snapshot["tasks"].items() if key not in tasks})
                    snapshot["dispatch"] = dispatch_summary(snapshot, tasks)
                content, kind = json.dumps(snapshot, ensure_ascii=False).encode("utf-8"), "application/json"
            else:
                match = ARTIFACT_PATH.fullmatch(self.path)
                if match is None:
                    self.send_error(404)
                    return
                name = match[1]
                _, artifacts = dashboard_snapshot(status)
                path = run_dir / name
                if name not in artifacts or path.is_symlink() or not path.is_file():
                    self.send_error(404)
                    return
                try:
                    content, kind = evidence_page(name, path.read_text(encoding="utf-8", errors="replace")), "text/html"
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


def dashboard_is_running(run_dir, port):
    if not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    connection = HTTPConnection("127.0.0.1", port, timeout=1)
    try:
        connection.request("GET", f"/health/{quote(run_dir.name, safe='')}")
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

    Thread(target=launch, daemon=True).start()


def serve_dashboard(run_dir, port, control=None, *, on_ready=announce, open_browser=False, cloud_origin=None):
    with dashboard_server(run_dir, port, control, cloud_origin=cloud_origin) as server:
        url = dashboard_url(server)
        on_ready(url)
        if open_browser:
            request_browser_open(url)
        server.serve_forever()


@contextmanager
def live_dashboard(run_dir, port, control=None, *, open_browser=False, cloud_origin=None):
    with dashboard_server(run_dir, port, control, cloud_origin=cloud_origin) as server:
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
