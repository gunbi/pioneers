# pioneers

面向不同 LLM 智能体的研发团队协作框架。pioneers 用统一的角色、Tool、Skill、工作流和验收规则，支持研发团队跨执行平台协作，并用可复用的计划和交付物在团队之间交接。

## 在智能体平台中使用

把仓库的 Git 地址交给能够读取仓库的智能体，并告诉它：

> 读取仓库根目录的 agent.md，用你的原生能力组建 pioneers 团队。按任务依赖和验收要求推进，根据每项任务标注的复杂度选择执行方式。

[agent.md](agent.md) 定义了角色、能力分配、交接和进度汇报规则。智能体使用所在平台可用的执行与协作能力。[examples/todo.json](examples/todo.json) 展示了产品负责人、交付工程师和独立验证如何交接。

## 本地 CLI 快速开始

需要 Python 3.9 或更高版本，无需安装 Python 依赖。

```bash
python3 pioneers.py --list-roles
python3 pioneers.py examples/todo.json
```

示例在离线模式下演练「产品定义验收 → 工程师实现并自测 → 独立验证」。运行时会打印产物目录，默认位于 `.pioneers/runs/`。每个任务生成一个 Markdown 文件，展示它会收到的任务和前置产物。

## 角色与工作流

| 角色 ID | 职责 | 工作区权限 |
| --- | --- | --- |
| `pm` | 产品负责人，定义目标、范围和验收条件，处理需求歧义 | 只读 |
| `ld` | 交付负责人，协调分工和依赖，明确共享契约与集成负责人 | 只读 |
| `engineer` | 交付工程师，负责设计、实现、自测和交付证据 | 可写 |
| `qa` | 独立验证，核对实际变更、运行结果和验收证据 | 只读 |

从 [examples/todo.json](examples/todo.json) 开始。为任务定义角色、依赖、能力和验收条件。本地 CLI 在一个工作区内按依赖串行调度。已有手写工作流可以继续使用原技术角色 ID。

QA 任务开启 `review` 时，真实运行会保存结构化验收报告。全部验收项通过后继续推进。实现问题交回工程师，需求歧义交回产品，环境、依赖或证据缺失交回 LD。CLI 保存结论并停止当前运行，供修复后复验。

如需留下可重复使用的团队配置，可以调用初始化工具：

```bash
python3 pioneers.py --create-team --workspace /path/to/practice-repo
```

它会在目标 Git 项目写入 `.pioneers/team.json`，输出 JSON。重复调用会返回已有的团队，旧版默认团队会迁移到这四个角色并保留模型设置。

## 角色能力

[capabilities.json](capabilities.json) 维护角色可用的 Tool 和 Skill。运行 `python3 pioneers.py --list-capabilities engineer` 查看交付工程师的能力目录。

每个工程师任务通过 `tools` 选择本次使用的 Tool，例如 `["frontend_html_check", "pioneers_git.git_diff"]`。空数组表示本次无需内置 Tool 或 MCP Tool。任务可用 `skills` 预载指引，也可按需读取角色获配的 Skill。

```bash
python3 pioneers.py --run-tool architecture_check --role engineer --workspace /path/to/project --input architecture.json
python3 pioneers.py --run-tool frontend_html_check --role engineer --workspace /path/to/project --input index.html
python3 pioneers.py --run-tool backend_api_check --role engineer --workspace /path/to/project --input openapi.json
```

内置检查返回 JSON 和退出码。架构 JSON 格式见 [架构设计 Skill](skills/architecture-design/SKILL.md)。HTML 和 OpenAPI 检查覆盖基本结构，用户流程和接口行为仍由项目测试验证。

### 工程设计与交付

| Skill | 分配 | 用途 |
| --- | --- | --- |
| [架构设计](skills/architecture-design/SKILL.md) | LD、交付工程师 | 识别复杂度，记录方案取舍、模块边界和共享契约。 |
| [编程设计](skills/engineering-design/SKILL.md) | 交付工程师 | 划分业务规则、状态、副作用和调度，按契约实现与测试。 |
| [代码评审](skills/code-review/SKILL.md) | LD、QA、交付工程师 | 核查实际变更、接口兼容和验收证据，区分阻塞问题和建议。 |

[创建待办](examples/todo.json) 演示单一工程师端到端交付。[多设备同步](examples/todo-sync.json) 演示共享契约、细分工程师任务、集成和独立验收。可用下面的命令检查角色和 Skill 的交接：

```bash
python3 pioneers.py examples/todo-sync.json
```

### MCP Tool

| 能力 | 角色与范围 |
| --- | --- |
| 本地 Git | LD、QA 查状态、差异和历史。交付工程师另外可按任务选择暂存、提交和创建分支。服务限制在目标仓库。 |
| 远端仓库、Issue 和 PR | 四个角色均可按任务读取获授权的内容。 |
| 浏览器 | 交付工程师和 QA 可以按任务操作测试页面、截图和查控制台消息。每次运行使用隔离的浏览器会话。 |

```bash
python3 pioneers.py --mcp-config engineer --workspace /path/to/project
```

配置中的 Git 服务使用 `uvx`，浏览器服务使用 `npx`，执行环境需提供对应的命令和可用浏览器。远端工具通过 MCP 客户端完成登录，或从执行环境中的 `GITHUB_PERSONAL_ACCESS_TOKEN` 读取凭证。配置文件只记录变量名。

本地执行器会收到相应角色的 MCP 配置。其他平台可用上述命令导出配置，并通过原生 MCP 功能连接。执行端应保留 `enabled_tools` 白名单。

### 第三方 Skill

- `frontend-design` 由交付工程师在设计 Web 界面时使用。
- `webapp-testing` 由交付工程师和 QA 按需使用，运行 Python 测试脚本时需要 Python Playwright 及对应浏览器。

两个 Skill 来自 [上游仓库的固定版本](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills)，在各自的目录保留了许可证、可用脚本和示例。

## 给目标，让 LD 自主分工

选择一个已配置的执行器，并指定一个可供修改的 Git 仓库或 worktree。使用自定义执行器时，通过 `--runner` 传入适配器的路径。适配器自行选择模型时，可省略 `--model`。

```bash
python3 pioneers.py --goal "做一个能创建待办的 Web 页面" \
  --live --runner /path/to/agent-runner --workspace /path/to/practice-repo
```

LD 先在只读模式下规划，选择角色、任务依赖、Skill、复杂度及验收项。CLI 校验生成的计划，保存为运行目录的 `workflow.json`，再按顺序执行。可以加 `--plan-only` 生成计划并查看分工。已有计划时可以直接传入 JSON 文件：

```bash
python3 pioneers.py examples/todo.json \
  --live --runner /path/to/agent-runner --workspace /path/to/practice-repo
```

如果初始化时用 `--model` 配置了默认模型，CLI 会在执行时传入该模型，运行参数可以覆盖这个设置。CLI 按任务调用执行器，传入角色和工作区权限要求。任务失败时，后续任务停止，之前的 Markdown 产物可继续查阅。

## 在不同执行端使用同一份计划

[workflow.schema.json](workflow.schema.json) 定义了统一的计划结构。智能体读取 [agent.md](agent.md) 后，可以用原生能力规划和执行，也可以将工作流交给本地 CLI。团队之间可以用同一份工作流、接口契约和验收记录交接。

本地 CLI 接入其他执行端时，在真实模式传入 `--runner /path/to/executable`。每次调用的当前目录是目标项目。运行器通过 stdin 读取一个 JSON 对象，字段为：

| 字段 | 说明 |
| --- | --- |
| `phase` | `plan`、`task` 或 `review` |
| `role`、`prompt` | 当前角色 ID 和完整任务提示 |
| `workspace`、`sandbox` | 工作目录和角色要求的权限，适配器负责落实 |
| `model` | 指定的模型，未指定时为 `null`，由执行平台选择 |
| `output_schema` | 规划或独立验收阶段的 JSON Schema，普通任务为 `null` |
| `mcp_servers`、`builtin_tools` | 当前任务的 MCP 配置和内置 Tool 白名单，由适配器落实 |

运行器用 stdout 输出逐行 JSON。可用 `{"type":"activity","message":"正在检查项目"}` 更新看板，成功时用 `{"type":"result","message":"交付内容"}` 返回结果并退出 0。规划和独立验收的 `message` 是符合对应 schema 的 JSON 字符串。运行器失败使用非零退出码，诊断写入 stderr。

## 查看执行进度

运行前会打印团队看板，列出已分配的任务、等待的依赖和未分配的角色。执行时，终端根据执行器上报的活动显示角色开工、当前操作、产物交接和阻塞情况。离线模式显示的是交接预览进度。

将启动时打印的 `Run output` 路径替换到下面的命令里：

```bash
python3 pioneers.py --watch /path/to/run-dir
python3 pioneers.py --status /path/to/run-dir
```

`--watch` 持续显示看板直到运行结束，`--status` 查看一次。最新快照保存在运行目录的 `status.json`，也可供其他工具读取。通过智能体平台执行时，协调者依据 [agent.md](agent.md) 在对话中主动汇报。
