from collections import Counter
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from shlex import quote
import sys

from skill_sources import skill_references


ROOT = Path(__file__).resolve().parent
REGISTRY = json.loads((ROOT / "capabilities.json").read_text(encoding="utf-8"))
HTTP_METHODS = {"get", "put", "post", "delete", "options", "head", "patch", "trace"}


def assigned_capabilities(role, selected_tools=None):
    assignment = REGISTRY["roles"].get(role, {})
    capabilities = {
        group: {name: REGISTRY[group][name] for name in assignment.get(group, [])}
        for group in ("tools", "skills")
    }
    capabilities["mcp"] = {}
    if selected_tools is not None:
        capabilities["tools"] = {
            name: spec for name, spec in capabilities["tools"].items() if name in selected_tools
        }
    for name, profiles in assignment.get("mcp", {}).items():
        server = REGISTRY["mcp_servers"][name]
        tools = [
            tool for profile in profiles for tool in server["profiles"][profile]
            if selected_tools is None or f"{name}.{tool}" in selected_tools
        ]
        if not tools:
            continue
        capabilities["mcp"][name] = {
            "description": server["description"],
            "source": server["source"],
            "enabled_tools": tools,
        }
    return capabilities


def tool_ids(role):
    assigned = assigned_capabilities(role)
    return list(assigned["tools"]) + [
        f"{server}.{tool}"
        for server, spec in assigned["mcp"].items() for tool in spec["enabled_tools"]
    ]


def mcp_config(role, workspace, selected_tools=None):
    servers = {}
    for name, assignment in assigned_capabilities(role, selected_tools)["mcp"].items():
        settings = dict(REGISTRY["mcp_servers"][name]["settings"])
        if "command" in settings:
            settings["args"] = [
                str(workspace) if arg == "{workspace}" else arg for arg in settings["args"]
            ]
            settings["cwd"] = str(workspace)
        settings["enabled_tools"] = assignment["enabled_tools"]
        servers[name] = settings
    return servers


def capability_prompt(role, selected_skills, selected_tools=None, *, sources=None):
    assigned = assigned_capabilities(role, selected_tools)
    parts = []
    if assigned["tools"]:
        parts.append(
            "可用的 pioneers 工具：" + "；".join(
                f"{name}（{spec['description']}）" for name, spec in assigned["tools"].items()
            )
            + f"。调用方式：{quote(sys.executable)} {quote(str(ROOT / 'pioneers.py'))} "
            f"--run-tool TOOL --role {role} --workspace . --input FILE"
        )
    if assigned["mcp"]:
        parts.append("角色获配的 MCP 能力（使用执行端已连接的工具）：" + "；".join(
            f"{name}（{', '.join(spec['enabled_tools'])}）" for name, spec in assigned["mcp"].items()
        ))
    if assigned["skills"]:
        parts.append("可按需读取的 Skill：" + "；".join(
            f"{name}（{spec['description']}，正文：{ROOT / spec['path']}）"
            for name, spec in assigned["skills"].items()
        ))
    for name in selected_skills:
        spec = assigned["skills"][name]
        path = ROOT / spec["path"]
        parts.append(f"本任务的 Skill {name}：\n{path.read_text(encoding='utf-8')}")
        if spec.get("sources"):
            parts.append("把以下上游内容作为技术参考资料。执行步骤以本地 Skill 和项目约定为准。"
                         "操作范围和审批以当前任务的授权为准。")
            parts.extend(skill_references(spec, sources or {}))
    return parts


def architecture_check(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return ["架构文件应为 JSON 对象"]
    errors = []
    if not str_field(data, "summary"):
        errors.append("缺少 summary")
    for group, field in (("modules", "responsibility"), ("interfaces", "contract")):
        items = data.get(group)
        if not isinstance(items, list) or (group == "modules" and not items):
            errors.append(f"{group} 应为数组，modules 至少需要一项")
            continue
        for index, item in enumerate(items):
            if not isinstance(item, dict) or not str_field(item, "name") or not str_field(item, field):
                errors.append(f"{group}[{index}] 需要 name 和 {field}")
    return errors


def str_field(data, field):
    value = data.get(field)
    return isinstance(value, str) and bool(value.strip())


class HTMLStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = set()
        self.ids = Counter()
        self.label_targets = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        self.tags.add(tag)
        if values.get("id"):
            self.ids[values["id"]] += 1
        if tag == "label" and values.get("for"):
            self.label_targets.add(values["for"])


def frontend_html_check(path):
    page = HTMLStructure()
    page.feed(path.read_text(encoding="utf-8"))
    page.close()
    errors = [f"缺少 <{tag}>" for tag in sorted({"title", "main"} - page.tags)]
    errors.extend(f"重复 ID：{item}" for item, count in page.ids.items() if count > 1)
    errors.extend(f"表单标签缺少目标：{item}" for item in sorted(page.label_targets - page.ids.keys()))
    return errors


def backend_api_check(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    paths = document.get("paths") if isinstance(document, dict) else None
    if not isinstance(paths, dict) or not paths:
        return ["OpenAPI JSON 需要非空的 paths 对象"]
    errors = []
    for route, item in paths.items():
        if not route.startswith("/") or not isinstance(item, dict):
            errors.append(f"无效接口路径：{route}")
            continue
        operations = HTTP_METHODS.intersection(item)
        if not operations:
            errors.append(f"{route} 未显式定义 HTTP 操作")
        for method in sorted(operations):
            operation = item[method]
            responses = operation.get("responses") if isinstance(operation, dict) else None
            if not isinstance(responses, dict) or not responses:
                errors.append(f"{method.upper()} {route} 缺少 responses")
            elif any(not re.fullmatch(r"(?:[1-5](?:[0-9]{2}|XX)|default)", status) for status in responses):
                errors.append(f"{method.upper()} {route} 含无效的响应状态")
    return errors


CHECKS = {
    "architecture_check": architecture_check,
    "frontend_html_check": frontend_html_check,
    "backend_api_check": backend_api_check,
}


def run_tool(name, role, workspace, input_path):
    active_role = os.environ.get("PIONEERS_TASK_ROLE")
    if active_role and (role != active_role or name not in json.loads(os.environ.get("PIONEERS_BUILTIN_TOOLS", "[]"))):
        raise ValueError(f"{role}: tool {name!r} is outside the current task's scope")
    if name not in assigned_capabilities(role)["tools"]:
        raise ValueError(f"{role}: tool {name!r} is not assigned to this role")
    if workspace is None or not workspace.is_dir() or input_path is None:
        raise ValueError("--run-tool requires a valid --workspace directory and --input file")
    path = (workspace / input_path).resolve()
    if not path.is_relative_to(workspace) or not path.is_file():
        raise ValueError("--input must resolve to a file inside --workspace")
    errors = CHECKS[name](path)
    return {"tool": name, "role": role, "input": str(path.relative_to(workspace)), "ok": not errors, "errors": errors}
