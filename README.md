# pioneers

面向不同 LLM 智能体的研发团队协作框架。pioneers 用统一的角色、Tool、Skill、工作流和验收规则，支持研发团队跨执行平台协作，并用可复用的计划和交付物在团队之间交接。

## 在智能体平台中使用

把仓库的 Git 地址交给能够读取仓库的智能体，并告诉它：

> 读取仓库根目录的 agent.md，用 pioneers 启动这次交付。派工前先给我运行看板，再按任务依赖领取、执行和回报。

[agent.md](agent.md) 定义了角色、能力分配、交接和进度汇报规则。pioneers 负责登记、调度和验收，平台提供实际执行能力。无法启动 pioneers 时，报告阻塞并暂停派工。[examples/todo.json](examples/todo.json) 展示了产品负责人、交付工程师和独立验证如何交接。

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

QA 任务开启 `review` 时，真实运行会保存结构化验收报告。存在失败的实现验收项时，可用 `changes_requested` 退回已完成的上游实现任务。存在多个候选时，通过报告里的 `repair_task` 指定任务 ID。Pioneers 保留旧证据，重新排队该任务和受其影响的下游。如果受影响的下游仍在执行，等它们回报后再提交退回决定。需求歧义和环境问题分别交回产品与 LD，并暂停当前运行。

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
| [系统化调试](skills/systematic-debugging/SKILL.md) | 交付工程师、QA | 复现问题、验证根因并复验修复结果。 |
| [变更集成](skills/change-integration/SKILL.md) | LD、交付工程师 | 组织并行变更合入，在实际集成版本上构建和验证。 |
| [测试工程](skills/test-engineering/SKILL.md) | 交付工程师、QA | 按风险选择测试层级，把关键行为和缺陷复现变成可重复运行的测试。 |

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
- `ios-development` 和 `android-development` 由交付工程师或 QA 按任务加载，旧版 iOS 与 Android 兼容角色也可以加载对应能力。
- `wechat-miniprogram-development` 由交付工程师或 QA 按任务加载，覆盖小程序实现、接口联调和独立验证。微信开发者工具和真机由宿主提供。
- `requirements-design` 供 PM 定义需求和验收条件，`acceptance-testing` 供 QA 独立验证。

两个 Skill 来自 [上游仓库的固定版本](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills)，在各自的目录保留了许可证、可用脚本和示例。

两份 Anthropic 文件是仓库里的基础版本。新增或修改开源 Skill 引用时，在本地 `SKILL.md` 注明来源，并将 GitHub 仓库和 Markdown 路径登记到 [capabilities.json](capabilities.json) 的 `skills.<id>.sources`。现有刷新逻辑会自动包含新增条目。

新建宿主运行、启动真实 CLI、用 `--continue RUN_DIR` 续跑 CLI，或用 `--dashboard RUN_DIR` 重开宿主运行的看板时，都会按当前清单检查上游，把刷新结果写入运行目录的 `skill-sources.json`。`sources` 条目格式为 `[仓库, Markdown 路径, 可选分支]`，默认分支为 `main`。新派发的任务使用此次刷新后的版本，已领取的任务继续使用派工时收到的内容。浏览器刷新页面只会更新看板状态。

角色加载本地 Skill 后会看到选中能力的上游摘录。本地约定、脚本和权限优先。刷新只读取 Markdown。网络受限时使用最近的缓存，首次离线继续使用本地正文。可设置 `PIONEERS_SKILLS_OFFLINE=1` 直接跳过网络刷新。离线演练不联网。

## 给目标，让 LD 自主分工

CLI 真实运行和宿主模式都要求显式传入项目的 `--workspace`。已有项目可以使用项目目录或其专用 Git worktree。新项目可以从 pioneers 检出目录执行 `git init ../todo-app`，然后传入 `--workspace ../todo-app`。启动时会检查项目目录与框架仓库是否分离。CLI 在目标目录启动任务。宿主须使用领取结果的 `workspace` 作为工作目录，并按 `sandbox` 落实访问权限。运行记录由 `--output-dir` 单独管理。

真实 CLI 运行需要 Git 项目根目录或 worktree。通过 `--runner` 配置自定义执行器。适配器自行选择模型时，可省略 `--model`。

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

[workflow.schema.json](workflow.schema.json) 定义了统一的计划结构。两种执行端共用 pioneers 的运行记录、上游交付物和独立验收。

### 审批与人工介入

真实任务提交后，Pioneers 先保存产物和判断请求。LLM 结合复杂度、证据的不确定性、可见的角色分歧和下游影响，给出是否要确认及理由。结果仍在判断中或待人工确认时，依赖它的任务不能领取。与这些决策无依赖关系的任务可以继续。

| 工作流字段 | 规则 |
| --- | --- |
| `impact: architecture` 或 `feature_design` | 大型架构或重要功能先形成方案。执行端按只读派发，提交后必须确认，下游才能按方案实施。 |
| `impact: costly_change` | 高迁移成本、大范围重构等，在领取前确认操作范围，产物提交后再确认交接。 |
| `effect: send / publish / payment / permissions / delete / irreversible` | 把执行操作单独列为任务。前置产物齐备后先由人授权，再领取。LLM 无法授予执行权限。 |
| `--require-release` | 独立 QA 和任务结果放行后，先由人确认发布，再执行外部部署。发布失败后重试要重新确认。 |

LLM 在常规任务结果里识别到上述重大设计或高成本改动时，也会将结果送人工确认。宿主应在真正执行外部操作前按 `effect` 登记，且执行端按领取返回的权限与范围运行。

在看板的「人工确认与介入」填写理由并勾选决策，也可以直接用 CLI。每条确认、重审、暂停和恢复都会记录时间和原因。云工作区可使用下文的私有 HTTPS 预览，或通过 SSH 转发打开本机地址。

```bash
python3 pioneers.py --approve RUN_DIR --decision D1 --decision D3 --reason "同意这两项方案"
python3 pioneers.py --approve RUN_DIR --all --reason "已逐项核对"
python3 pioneers.py --revise RUN_DIR --decision D2 --reason "补充兼容旧数据的要求"
python3 pioneers.py --revise RUN_DIR --decision D2 --artifact revised.md --reason "替换设计稿"
python3 pioneers.py --pause RUN_DIR --reason "需要调整迁移计划"
python3 pioneers.py --intervene RUN_DIR --from architecture --reason "保留旧客户端契约"
python3 pioneers.py --resume RUN_DIR --reason "方案调整完成"
```

修订结果保留旧产物，重新判断并重排该结果的下游。看板点击「暂停新任务」后，已领取的任务可以继续执行和回报。「调整任务计划」里可以交给 LD 更新计划，也可以指定从某项任务重新开始。指定任务重跑（CLI 的 `--from`）会让该任务及下游重新排队，保存后点「继续领取新任务」。交给 LD 更新计划时，Pioneers 等进行中的任务回报后处理请求，验证并应用新计划后自动继续。已开始或已获授权的任务不能通过重规划修改。原始计划保存在 `workflow.json`，更新版本保存在 `workflow.N.json`。如果 CLI 进程已经退出，提交指令后用 `--continue RUN_DIR` 继续同一次运行。

宿主模式通过 `--host-ready RUN_DIR` 查看 `replan.ready`。就绪后用 `--replan RUN_DIR` 取得 LD 提示，按返回的 `revision` 用 `--replan RUN_DIR --revision N --artifact PLAN.json` 提交。已关闭的宿主会话需要重新开启协调端来处理请求。

### 云端看板操作

在云工作区使用宿主的私有 HTTPS 端口预览，将域名根路径映射到看板端口。预览需转发 GET、`POST /human-action`、浏览器的 `Origin` 和 `X-Pioneers-Control` 请求头。用实际预览域名替换下面的示例：

```bash
python3 pioneers.py --dashboard RUN_DIR --port 8789 --dashboard-origin https://preview.example.com
```

新运行也可以在 `--host-start`、`--live` 或 `--continue RUN_DIR` 命令后加相同的参数。在新标签页打开配置的 HTTPS 地址，首次提交人工操作时输入启动终端里的 `Dashboard control key`。凭证在当前标签页内存中保留，刷新后需要重新输入。凭证在启动后一小时失效，重启同一运行的看板可获得新凭证。在已有的真实运行中，只需重启独立运行的看板服务即可更新凭证。

看板内容的浏览权限由私有预览入口控制。控制凭证用于当前看板的操作权限，所有持同一凭证的人拥有相同权限。凭证只显示在启动终端的 stderr，请用用户可访问的私有终端接收。未启用 `--dashboard-origin` 的预览仍可查看，人工操作通过本机入口、SSH 转发或 CLI 提交。

### 平台原生执行

适用于有终端和 Python、可直接分派原生 Agent 的宿主。协调者先生成工作流，在一个可保持运行的终端启动看板。首行 JSON 返回 `run_dir` 和 `dashboard_url`。确认页面和 `/dashboard-data.json` 正常响应后，在用户可见的浏览器打开入口。云工作区使用私有端口预览或 SSH 转发，再将实际入口提供给用户。后续派工命令在另一个终端运行，把 `RUN_DIR` 替换为返回的目录。

```bash
# 登记工作流并保持看板服务运行。
python3 pioneers.py --host-start examples/todo.json --workspace /path/to/project

# 从另一终端查询就绪任务和阻塞原因。
python3 pioneers.py --host-ready RUN_DIR

# 领取成功后，把返回的 JSON 作为派工上下文。
python3 pioneers.py --host-claim RUN_DIR --task requirements

# 执行端有新进展时上报，完成时附上本地交付物文件。
python3 pioneers.py --host-report RUN_DIR --task requirements --state running \
  --activity "正在定义空白标题和刷新后恢复的验收步骤"
python3 pioneers.py --host-report RUN_DIR --task requirements --state completed \
  --activity "已整理三条可复现的验收步骤" --artifact /path/to/requirements.md
```

完成回报返回 `judgment`，包含 `decision`、`task`、`attempt`、给判断 LLM 的提示和输出结构。宿主将它交给只读判断会话，再提交 JSON。浏览器重审后，宿主也可主动查询待判断项，使用 `--judge RUN_DIR --decision D2` 取得同一请求。

`--host-ready` 是只读查询。`ready` 按最长未完成依赖链的步数降序排列，`active` 包括执行中和待判断的任务，`waiting` 列出缺少的产物、对应状态或审批。看板「派工判断」显示同一结果，直接链接到任务和阻塞它的审批。宿主在可用容量和任务写入范围允许时同批派发就绪任务，某个产物放行后即可重查并启动其下游。同一角色的不同成员可同时领取独立任务。单机 `--live` 仍按依赖串行执行。

```bash
python3 pioneers.py --judge RUN_DIR --decision D2 --task requirements --attempt 1 --artifact judgment.json
```

`judgment.json` 例如 `{"needs_confirmation":true,"reason":"新数据模型会影响三个下游接口","impact":"architecture","effect":null}`。未按时收到有效判断的宿主任务继续等待。

领取时会校验运行状态和任务依赖。完成时，pioneers 存档交付文件，并为下游生成含前置交付物的提示。开启独立验收的 QA 任务使用 JSON 报告作为交付物。返回 `returned` 时，继续领取返回的 `repair_task`，再领取复验任务。第二轮起，每次回报都需要传入领取时返回的 `--attempt N`。执行失败或环境阻塞时，登记原因并停止领取后续任务。宿主必须在派发后转发可见的动作和结果。

### 将发布纳入交付

向阿里云 ECS 发布容器时，方案和验收任务可加载 `aliyun-ecs-container-deployment`。把实际部署安排成验收后的独立交付工程师任务，设置 `"skills": ["aliyun-ecs-container-deployment"]` 和 `"effect": "publish"`，并安排部署后的独立 QA 复验。审批通过后，由宿主提供所需工具和目标环境的授权。

需发布的工作流在 `--host-start` 或 `--live` 加上 `--require-release`。Pioneers 校验实现任务均有独立 QA 覆盖，任务验收完成后进入「验收通过」，等 LD 登记发布结果。外部发布平台的调用和操作权限由宿主管理。CLI 退出后，可用 `--dashboard RUN_DIR` 持续查看发布进度。

```bash
python3 pioneers.py --approve RUN_DIR --decision D9 --reason "确认版本和发布范围"
python3 pioneers.py --release RUN_DIR --state publishing --activity "开始发布已验收版本"
python3 pioneers.py --release RUN_DIR --state published --activity "部署成功" --url https://example.com/
```

如果发布失败，使用 `--state publish_failed --activity "具体失败原因"`，重试时再次登记 `publishing`。完成后看板展示可访问的部署地址。

### CLI 调用运行器

本地 CLI 接入其他执行端时，在真实模式传入 `--runner /path/to/executable`。每次调用的当前目录是目标项目。运行器通过 stdin 读取一个 JSON 对象，字段为：

| 字段 | 说明 |
| --- | --- |
| `phase` | `plan`、`task`、`review` 或 `approval` |
| `role`、`prompt` | 当前角色 ID 和完整任务提示 |
| `workspace`、`sandbox` | 工作目录和角色要求的权限，适配器负责落实 |
| `model` | 指定的模型，未指定时为 `null`，由执行平台选择 |
| `output_schema` | 规划或独立验收阶段的 JSON Schema，普通任务为 `null` |
| `mcp_servers`、`builtin_tools` | 当前任务的 MCP 配置和内置 Tool 白名单，由适配器落实 |

运行器用 stdout 输出逐行 JSON。可用 `{"type":"activity","message":"正在检查项目"}` 更新看板，成功时用 `{"type":"result","message":"交付内容"}` 返回结果并退出 0。规划、独立验收和审批判断的 `message` 是符合对应 schema 的 JSON 字符串。运行器失败使用非零退出码，诊断写入 stderr。CLI 判断失败时，结果进入人工确认。

本机人工入口由本机操作者使用。云端模式由私有预览控制浏览权限，看板凭证授予本次操作权限。需要按人区分审批权限时，由宿主提供个人身份认证，并隔离工作目录与外部工具权限。Pioneers 管理经登记的任务与发布状态，平台外独立发起的命令由平台的权限策略控制。

## 查看执行进度

CLI 真实运行和宿主模式默认启动看板并打印地址。宿主模式在前台保持看板运行，从另一终端领取任务。`--register-only` 可先登记并退出，之后用 `--dashboard RUN_DIR` 启动看板再领取。宿主领取时会检查对应看板能否响应。服务停止后，已领取任务仍可回报。

本地桌面默认尝试打开浏览器，可用 `--no-open-browser` 关闭自动开页。云端通过宿主提供私有预览地址。看板每秒更新，展示各角色的当前动作、可并行任务、等待的依赖和审批。运行结束后可以重新启动看板查看记录。离线模式显示交接预览进度。

将启动时打印的 `Run output` 路径替换到下面的命令里：

```bash
python3 pioneers.py --watch /path/to/run-dir
python3 pioneers.py --status /path/to/run-dir
python3 pioneers.py --dashboard /path/to/run-dir
```

`--watch` 持续显示看板直到运行结束，`--status` 查看一次。`--dashboard` 按领取成员展示任务交接。宿主用 `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name "显示名"` 记录接手人，同一成员在一次运行中使用同一个 ID。重跑可换成员，旧轮次保留原成员。未领取任务显示在「待分配」，未记录成员的历史任务和 CLI 角色执行仍显示角色列。

审批显示在它控制的交接线上，QA 退回用单独的箭头和返工记录展示。每轮保留证据链接。看板确认时可填写确认人，CLI 同样可用 `--approve ... --approver "显示名"` 记录操作者自填姓名。最新任务状态、最近 100 条动作及更早的证据记录保存在运行目录的 `status.json`。
