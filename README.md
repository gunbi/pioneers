# pioneers

一个可在 AI 平台或本地命令行使用的多 Agent 协作框架。角色、任务依赖和验收规则写在仓库里。

## 在 Manus 中使用

将这个仓库的 Git 地址交给 Manus，并告诉它：

> 读取仓库根目录的 agent.md，用你的原生能力组建 pioneers 团队。按任务依赖和验收要求推进，根据每项任务标注的复杂度选择执行方式。

[agent.md](agent.md) 定义了团队名单和协作流程，[examples/todo.json](examples/todo.json) 展示了 PM、后端和测试如何交接。Manus 路径直接使用当前平台的能力，无需 API Key 或本地 Codex。

## 本地 CLI 快速开始

需要 Python 3.9 或更高版本，无需安装 Python 依赖。

```bash
python3 pioneers.py --list-roles
python3 pioneers.py examples/todo.json
```

示例在离线模式下演练「PM 定义需求 → 后端实现 → 测试验收」。运行时会打印产物目录，默认位于 `.pioneers/runs/`。每个任务生成一个 Markdown 文件，标明离线演示并展示它会收到的任务和前置产物。

## 角色与工作流

| 角色 ID | 职责 | 工作区权限 |
| --- | --- | --- |
| `ld` | 团队负责人，明确目标、分工和依赖 | 只读 |
| `pm` | 产品经理，定义需求和验收条件 | 只读 |
| `ui` | UI 设计师，交付布局和交互说明 | 只读 |
| `backend` | 后端开发，实现 API 和业务逻辑 | 可写 |
| `frontend` | 前端开发，实现 Web 界面 | 可写 |
| `ios` | iOS 开发，实现和验证 iOS 功能 | 可写 |
| `android` | 安卓开发，实现和验证 Android 功能 | 可写 |
| `qa` | 测试，对照验收条件核对实现和证据 | 只读 |

从 [examples/todo.json](examples/todo.json) 开始。为每项任务指定 `id`、`role`、`prompt`，也可提供 `depends_on`、`complexity` 和 `acceptance`。本地 CLI 在一个指定工作区内串行执行，每项任务启动独立的 Codex 会话。QA 的文本结论记录在产物中，供人工验收。

如需留下可重复使用的团队配置，可以调用初始化工具：

```bash
python3 pioneers.py --create-team --workspace /path/to/practice-repo
```

它会在目标 Git 项目写入 `.pioneers/team.json`，输出 JSON。重复调用会返回已有的团队。

## 角色能力

[capabilities.json](capabilities.json) 维护 Tool 和 Skill 的目录以及角色分配。运行 `python3 pioneers.py --list-capabilities frontend` 查看一个角色的能力。

| 角色 | 共用能力 | 专用能力 |
| --- | --- | --- |
| 前端 | `architecture-design` Skill、`architecture_check` Tool | `frontend-development` Skill、`frontend_html_check` Tool |
| 后端 | `architecture-design` Skill、`architecture_check` Tool | `backend-development` Skill、`backend_api_check` Tool |

任务可在 JSON 的 `skills` 字段列出需要预先提供的 Skill，例如 `["architecture-design", "backend-development"]`。角色还会收到可按需读取的 Skill 简介。

```bash
python3 pioneers.py --run-tool architecture_check --role frontend --workspace /path/to/project --input architecture.json
python3 pioneers.py --run-tool frontend_html_check --role frontend --workspace /path/to/project --input index.html
python3 pioneers.py --run-tool backend_api_check --role backend --workspace /path/to/project --input openapi.json
```

内置检查返回 JSON 和退出码。架构 JSON 格式见 [架构设计 Skill](skills/architecture-design/SKILL.md)。HTML 和 OpenAPI 检查覆盖基本结构，用户流程和接口行为仍由项目测试验证。

## 给目标，让 LD 自主分工

默认执行端是 Codex CLI。安装并登录后，在终端把 `MODEL_ID` 设为当前账号可用的模型 ID，再指定一个可供修改的 Git 仓库或 worktree。

```bash
python3 pioneers.py --goal "做一个能创建待办的 Web 页面" \
  --live --model "$MODEL_ID" --workspace /path/to/practice-repo
```

LD 先在只读模式下规划，选择角色、任务依赖、Skill、复杂度及验收项。CLI 校验生成的计划，保存为运行目录的 `workflow.json`，再按顺序执行。可以加 `--plan-only` 生成计划并查看分工。已有计划时可以直接传入 JSON 文件：

```bash
python3 pioneers.py examples/todo.json --live --model "$MODEL_ID" --workspace /path/to/practice-repo
```

如果初始化时用 `--model` 配置了默认模型，运行时可以省略这个参数。每个任务调用一次 `codex exec`，传入同一个模型，并根据角色选择工作区权限。任务失败时，后续任务停止，之前的 Markdown 产物可继续查阅。

## 在不同执行端使用同一份计划

[workflow.schema.json](workflow.schema.json) 定义了生成计划的结构。Manus 等平台读取 [agent.md](agent.md) 后，可以用原生能力规划和执行，也可以把生成的工作流 JSON 交给 CLI。

本地 CLI 接入其他执行端时，在真实模式传入 `--runner /path/to/executable`。每次调用的当前目录是目标项目。运行器通过 stdin 读取一个 JSON 对象，字段为：

| 字段 | 说明 |
| --- | --- |
| `phase` | `plan` 或 `task` |
| `role`、`prompt` | 当前角色 ID 和完整任务提示 |
| `workspace`、`sandbox` | 工作目录和角色要求的权限，适配器负责落实 |
| `model` | 指定的模型，未指定时为 `null`，由执行平台选择 |
| `output_schema` | 规划阶段的 JSON Schema，执行任务时为 `null` |

运行器用 stdout 输出逐行 JSON。可用 `{"type":"activity","message":"正在检查项目"}` 更新看板，成功时用 `{"type":"result","message":"交付内容"}` 返回结果并退出 0。规划阶段的 `message` 是符合工作流结构的 JSON 字符串。失败使用非零退出码，诊断写入 stderr。

## 查看执行进度

运行前会打印团队看板，列出已分配的任务、等待的依赖和未分配的角色。执行时，终端显示角色开工、Codex 的命令与文件操作、产物交接和阻塞情况。离线模式显示的是交接预览进度。

将启动时打印的 `Run output` 路径替换到下面的命令里：

```bash
python3 pioneers.py --watch /path/to/run-dir
python3 pioneers.py --status /path/to/run-dir
```

`--watch` 持续显示看板直到运行结束，`--status` 查看一次。最新快照保存在运行目录的 `status.json`，也可供其他工具读取。通过 Manus 或当前聊天平台执行时，协调者依据 [agent.md](agent.md) 在对话中主动汇报。
