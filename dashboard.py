from contextlib import contextmanager
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
from threading import Thread

from progress import artifact_name


ARTIFACT_PATH = re.compile(r"/evidence/([a-z0-9][a-z0-9_-]*(?:\.[0-9]+)?\.(?:md|json))")
RETURN_ACTIVITY = re.compile(r"退回 ([a-z0-9][a-z0-9_-]*) · ")


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


def dashboard_server(run_dir, port, control=None):
    run_dir = run_dir.resolve()
    status = run_dir / "status.json"
    if not status.is_file():
        raise ValueError(f"Run status does not exist: {status}")
    page = Path(__file__).with_name("dashboard.html").read_bytes()

    class DashboardHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != "/human-action" or control is None:
                self.send_error(404)
                return
            # Local operator UI only. Reject browser requests from other origins.
            hosts = (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")
            host = self.headers.get("Host")
            if host not in hosts or self.headers.get("Origin") != f"http://{host}":
                self.send_error(403)
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
            self.respond(json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json", code)

        def do_GET(self):
            if self.path == "/":
                content, kind = page, "text/html"
            elif self.path == "/status.json":
                content, kind = status.read_bytes(), "application/json"
            elif self.path == "/dashboard-data.json":
                snapshot, _ = dashboard_snapshot(status)
                snapshot["flow"] = flow_with_approvals(collaboration_flow(snapshot), snapshot.get("decisions", {}))
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

    return ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)


def announce(server):
    print(f"Dashboard: http://127.0.0.1:{server.server_port}/", flush=True)


def serve_dashboard(run_dir, port, control=None):
    with dashboard_server(run_dir, port, control) as server:
        announce(server)
        server.serve_forever()


@contextmanager
def live_dashboard(run_dir, port, control=None):
    with dashboard_server(run_dir, port, control) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            announce(server)
            yield
        finally:
            server.shutdown()
            worker.join()
