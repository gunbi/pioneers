# pioneers

面向不同 LLM 智能体的研发团队协作框架。pioneers 用统一的角色、Tool、Skill、工作流和验收规则，支持研发团队跨执行平台协作，并用可复用的计划和交付物在团队之间交接。

## 在智能体平台中使用

把仓库的 Git 地址交给能够读取仓库的智能体，并告诉它：

> 读取仓库根目录的 agent.md，用 pioneers 启动这次交付。派工前先给我运行看板，再按任务依赖领取、执行和回报。

[agent.md](agent.md) 定义了角色、能力分配、交接和进度汇报规则。pioneers 负责登记、调度和验收，平台提供实际执行能力。无法启动 pioneers 时，报告阻塞并暂停派工。[examples/todo.json](examples/todo.json) 展示了产品负责人、交付工程师和独立验证如何交接。

首次真实运行会在目标项目的 `AGENTS.md` 登记持续使用 Pioneers 的规则，保留项目原有说明。已完成的项目可以用 `python3 pioneers.py --bind-project --workspace PROJECT` 补登记。后续从项目根目录开始会话，能读取项目规则的 Agent 会沿用 Pioneers。将这段说明随项目版本保留，可让其他检出目录也获得同样的规则。

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
| [产品体验设计](skills/product-experience-design/SKILL.md) | PM、交付工程师、QA | 从用户任务确定导航、页面动线、布局、失败恢复和验收场景。 |
| [用户旅程地图](skills/customer-journey-mapping/SKILL.md) | PM | 在有访谈、反馈或行为数据时，梳理跨阶段触点和障碍。 |
| [变更影响面预检](skills/change-impact-preflight/SKILL.md) | LD、交付工程师 | 核对基线、共享契约、旧数据及回滚条件。 |
| [交付证据包](skills/verifiable-delivery-evidence/SKILL.md) | LD、交付工程师、QA | 将可复跑的结论绑定到实际提交或构建，按风险独立回读。 |
| [编程设计](skills/engineering-design/SKILL.md) | 交付工程师 | 划分业务规则、状态、副作用和调度，按契约实现与测试。 |
| [代码评审](skills/code-review/SKILL.md) | LD、QA、交付工程师 | 核查实际变更、接口兼容和验收证据，区分阻塞问题和建议。 |
| [系统化调试](skills/systematic-debugging/SKILL.md) | 交付工程师、QA | 复现问题、验证根因并复验修复结果。 |
| [变更集成](skills/change-integration/SKILL.md) | LD、交付工程师 | 组织并行变更合入，在实际集成版本上构建和验证。 |
| [测试工程](skills/test-engineering/SKILL.md) | 交付工程师、QA | 按风险选择测试层级，把关键行为和缺陷复现变成可重复运行的测试。 |

[创建待办](examples/todo.json) 演示单一工程师端到端交付。[多设备同步](examples/todo-sync.json) 演示共享契约、细分工程师任务、集成和独立验收。可用下面的命令检查角色和 Skill 的交接：

```bash
python3 pioneers.py examples/todo-sync.json
```

写入范围和验收条件明确、风险低且易于回滚时，Pioneers 的默认计划只有开发和独立 QA 两项。工程师完成实现、自测和必要集成，交付具体版本供 QA 复验。[单模块缺陷修复](examples/small-fix.json) 是可直接复用的两任务计划。涉及架构、共享契约、安全与权限或难以回滚的数据变更时，先评估影响并安排方案和审批，再派工实施。

关键界面改动先明确用户动线和布局，再按需加载 `frontend-design` 定视觉方向。复杂动线可以由 PM 先交付设计，简单布局可由工程师在同一个任务内先设计再实现，交 QA 按场景复验。

### 已有项目改版

[已有项目改版](examples/existing-project-redesign.json) 以既有商品分类页为例，依次记录旧版基线、评审新动线和布局、实现、冻结候选 commit、独立对照验收，再交接目标分支合入。位于不同任务的设计、基线和候选产物通过直接依赖交给 QA。

旧版基线和复验加载 `existing-ui-audit`，使用 Impeccable 的体验评审与技术审计思路。实现阶段加载 `existing-ui-redesign`，参考 TasteSkill 的原版改版方法。上游主分支已移除此 Skill，引用固定在含完整正文的提交。候选集成和合入交接加载 `change-integration`，沿用 Superpowers 的分支收尾参考。

```bash
python3 pioneers.py examples/existing-project-redesign.json
```

这条命令演练任务交接。真实运行时，复制示例，按已存在的目标项目替换页面、场景和验收条件，给写入任务填写实际的 `write_paths`。提前在项目 `AGENTS.md` 说明启动命令、测试数据和冒烟检查。 首次接入时先运行 `python3 pioneers.py --bind-project --workspace PROJECT`，审阅项目 `AGENTS.md` 的绑定规则并纳入所用的基线提交，然后从干净工作区启动改版。优先用宿主模式，将 `--workspace PROJECT` 指向干净且已有提交的目标仓库，由 Pioneers 分配隔离 worktree。使用 `--live` 时，先从目标分支创建独立的候选分支和 worktree，再把该 worktree 传给 `--workspace PROJECT`。示例使用 `impact: feature_design`，由操作者在设计交付后确认布局与动线，再放行实现。

独立 QA 应先确认当前 HEAD 等于候选 SHA，再按旧版场景复验。工作流结束会交付已验收候选和合入步骤。获得合入授权后，由操作者在目标仓库核对目标分支和工作区，按项目流程合入该候选。单机本地可在干净目标分支上用 `git merge --ff-only <accepted-sha>`。如果目标分支变化导致无法快进，或合入后的 HEAD 与验收 SHA 不同，重新集成并复验新的交付版本。记录目标分支实际 HEAD 和冒烟结果，分别登记「已验收」和「已合入」。有远端推送或部署时，按项目的授权流程另行处理。

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
- `chrome-extensions` 供开发和 QA 按任务加载，覆盖 Manifest V3、扩展调试和 Chrome Web Store 发布准备。保留 [Google Chrome 上游技能](https://github.com/GoogleChrome/modern-web-guidance/blob/64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65/skills/chrome-extensions/SKILL.md) 的参考文档和 Apache-2.0 许可证。
- 桌面应用开发和 QA 按技术栈选择 `desktop-framework-electron`、`tauri-app-builder`、`macos-spm-app-packaging` 或 `winui-dev-workflow`。Windows 原生开发可按任务补充 `winui-design`、`winui-code-review`、`winui-ui-testing`、`winui-packaging`、`winui-setup` 和 `winui-wpf-migration`。各目录保留上游参考资料和许可证。
- `ios-development` 和 `android-development` 由交付工程师或 QA 按任务加载，旧版 iOS 与 Android 兼容角色也可以加载对应能力。
- `wechat-miniprogram-development` 由交付工程师或 QA 按任务加载，覆盖小程序实现、接口联调和独立验证。微信开发者工具和真机由宿主提供。
- `requirements-design` 供 PM 定义需求和验收条件，`acceptance-testing` 供 QA 独立验证。

`frontend-design` 和 `webapp-testing` 来自 [Anthropic 上游仓库的固定版本](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills)，在各自的目录保留了许可证、可用脚本和示例。

两份 Anthropic 文件是仓库里的基础版本。新增或修改开源 Skill 引用时，在本地 `SKILL.md` 注明来源，并将 GitHub 仓库和 Markdown 路径登记到 [capabilities.json](capabilities.json) 的 `skills.<id>.sources`。现有刷新逻辑会自动包含新增条目。

新建宿主运行、启动真实 CLI、用 `--continue RUN_DIR` 续跑 CLI，或用 `--dashboard RUN_DIR` 重开宿主运行的看板时，都会按当前清单检查上游，把刷新结果写入运行目录的 `skill-sources.json`。`sources` 条目格式为 `[仓库, Markdown 路径, 可选分支或提交]`，默认分支为 `main`。新派发的任务使用此次刷新后的版本，已领取的任务继续使用派工时收到的内容。浏览器刷新页面只会更新看板状态。

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

如果初始化时用 `--model` 配置了默认模型，CLI 会在执行时传入该模型，运行参数可以覆盖这个设置。CLI 按任务调用执行器，传入角色和工作区权限要求。执行器异常退出时按下文的恢复规则处理，已提交的产物可以继续查阅。

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

新建宿主运行时，如果目标 Git 工作区干净且已有提交，工程师每次领取会自动获得自己的 worktree 和分支。工作流的 `write_paths` 填项目相对文件或目录，例如 `["checkout/pricing.py"]`。范围重叠的写入任务等待交接，空数组或旧工作流缺少此字段时按整个项目处理。项目目录未提交、已有本地改动或没有 Git 时，写入任务在共用工作区顺序执行。

工程师从领取结果的 `workspace` 和 `git.branch` 工作，提交本地改动后再回报。回报时会检查未提交改动及交付版本的越界文件。需要接续上一项修复时，用 `depends_on` 表达依赖。下游实现、集成和 QA 的工作区会合入依赖任务的已放行提交。合入冲突时，领取失败并保留发生冲突的 worktree，解决并提交后可重试。最终合并到目标分支由交付流程安排，Pioneers 保留各任务分支和工作区供核查。

等待看板确认或暂时无任务可领取时，宿主协调者保持会话运行，并使用 `--host-ready RUN_DIR --wait 25` 等待就绪。审批使下游可领取、最终发布获授权（`release_ready` 为真），或运行结束时，命令立即返回。命令也会返回其他可处理的工作，例如 LD 重规划和待判断的产物。等待到期会返回 `timed_out: true`，协调者可以在同一会话继续等待。宿主结束会话后，可用同一个运行目录重新接入。

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

## 执行器故障恢复

宿主领取任务后，使用 `--host-exec` 启动本地执行器（例如浏览器测试命令或执行器适配器）。Pioneers 在分配的工作目录启动该命令，并在独立的父进程中每 60 秒续约，无需等待命令输出。同一次尝试只允许一个执行器。命令退出或 Pioneers 收到中断时，框架停止续约，并清理它启动的进程组（Windows 上为直接子进程）。命令退出码传回宿主。适配器应在实际任务结束前保持前台运行，派生的浏览器和测试服务应留在同一进程组。宿主仍负责回报完成、失败或失联。

```bash
python3 pioneers.py --host-exec RUN_DIR --task verify --attempt 1 --exec-command python3 verify_browser.py
```

当需要框架托管时，将执行端作为前台命令或本地适配器启动，并用 `--output-dir` 把运行记录放到启动进程有写权限的目录。无法通过本地命令启动的原生会话，应由宿主提供独立的存活检查和续约能力。宿主领取任务时会获得 `attempt` 和 180 秒租约。执行器仍活跃时，宿主至少每 60 秒续约一次。带 `--state running` 的进度回报也会续约。只有确认执行器或其会话仍活跃时才续约。确认执行器异常退出时，可以报告 `--state lost`。宿主查就绪队列或尝试领取时，也会回收租约超时的任务。

```bash
python3 pioneers.py --host-heartbeat RUN_DIR --task fix --attempt 1
python3 pioneers.py --host-report RUN_DIR --task fix --attempt 1 --state lost --activity "执行端进程异常退出"
```

CLI 调度进程意外退出后，用 `--continue RUN_DIR` 重新打开同一运行。Pioneers 取得调度锁后检查未完成的分配，保留已放行的产物、待审批决策和计划。仍有活动 CLI 调度进程时，第二个进程无法接管。

- 只读任务和在独立 Git worktree 中运行的普通写入任务可自动重新排队。每个任务最多自动恢复两次。每次分配使用新的 `attempt`，旧回报会被拒绝。旧工作区和旧尝试的记录保留。
- 共用工作区的写入、发布等外部操作、高成本任务或达到自动恢复上限时，任务进入「待人工恢复」。共用工作区写入会继续阻塞同一运行中的其他写入，直至完成核对。失败发生在框架外部时，原进程或外部操作仍可能进行，由操作者核对实际状态。
- 核对旧执行端已停止后，可以在看板「派工判断 → 等待中」使用「检查后重跑」，也可以调用命令行。需要授权的任务重跑时会生成新的授权审批。如果外部操作已经完成，可用 `--artifact` 接续已核对的产物，交给原有验收及审批流程，避免重复执行。
- 升级前在途的宿主任务可能没有租约。确认旧执行端已停止后，用 `--recover` 核对并接续这项任务。

```bash
python3 pioneers.py --continue RUN_DIR
python3 pioneers.py --recover RUN_DIR --task fix --attempt 1 --reason "旧进程已退出，已核对工作区"
python3 pioneers.py --recover RUN_DIR --task send --attempt 1 --artifact evidence.md --reason "已核对外部回执"
```

### 成员续接与操作记录

故障重派时，`--host-ready` 给出 `preferred_member`，未指定 `--member` 的领取会沿用原成员。原成员不可用时显式指定新成员。新的分配会附带 `continuation`，包含上次执行的成员、工作区、最近检查点和操作状态。提示词内的交接摘要会优先列出外部操作和待核对事项。新 worktree 仍按现行隔离规则创建，原 worktree 的未提交改动需先核对，再有选择地接续。

宿主在任务进行中通过以下命令记录一条 JSON 事件。命令会校验当前 `attempt` 并续约。自定义 `--runner` 可以把同格式的事件写到 stdout，并从任务请求的 `task`、`attempt` 和 `continuation` 字段接收续接资料。

```bash
python3 pioneers.py --host-checkpoint RUN_DIR --task fix --attempt 1 --artifact event.json
```

```jsonl
{"type":"checkpoint","summary":"已定位到解析器。下一步增加空输入的回归用例。","session_id":"executor-session-1"}
{"type":"operation","id":"read-config","kind":"read","status":"completed","summary":"已核对配置格式"}
{"type":"operation","id":"patch","kind":"workspace_write","status":"started","summary":"正在修改解析器"}
```

操作 `kind` 支持 `read`、`workspace_write`、`external` 和 `tool`。用同一 ID 上报 `started`、`completed` 或 `failed`。中断时，没有确定结果的操作会标成待核对。记录只填续接所需摘要，避免写入密钥、原始工具输出和完整会话。内置 Codex 执行器从事件流保留最后的工作说明和可辨识的操作摘要；它用这些资料开启新执行。支持原生会话的宿主或适配器可以使用 `resume_session_id`。Pioneers 只在确认旧执行端已停止、仍是原成员和原工作区、且没有外部或待核对的写入类动作时交回此引用。浏览器看板不返回会话引用。

已登记的外部操作需要核对真实回执。用 `--recover ... --recovery-checks checks.json` 登记待核对操作的结果，例如：

```json
[{"id":"send-1","outcome":"completed","reason":"外部系统返回已投递回执"}]
```

确认已发生时，通过 `--artifact` 接续证据，再走原验收和审批流程。确认未发生时，把 `outcome` 填成 `not_executed`，之后重新申请执行授权。Pioneers 只跟踪已观察或上报的动作，外部系统的真实状态需通过回执核对。外部操作仍须事先声明 `effect`。

### 接续已合入 Git 的产物

宿主模式下，普通实现任务还在待派发或待恢复时，可以登记已经合入当前项目分支的提交。先确认原执行端已停止，再在任务队列选「接续合入提交」，填写提交 SHA、接续原因、交付证据及待验证项。命令行入口如下。

```bash
python3 pioneers.py --host-adopt RUN_DIR --task integration --attempt 2 \
  --commit COMMIT_SHA --artifact EVIDENCE.md --reason "已核对合入提交与旧执行端"
```

Pioneers 核对当前尝试、分支合入关系、已放行依赖和写入范围，并把提交送回原有产物判断。任务通过判断后，后续 QA 按该提交继续，仍需回报实际验收结果。涉及前置授权或外部操作的任务沿用原来的审批和恢复流程。

## 项目看板与迭代记录

同一交付项目复用一个入口。先在可持续运行的终端启动项目看板，后续迭代沿用同一个项目目录。默认本地地址为 [http://127.0.0.1:8790/](http://127.0.0.1:8790/)。同时运行多个项目时，为每个项目固定一个 `--port`。云端按上面的私有预览方式映射此端口，需要在看板上审批时同时配置 `--dashboard-origin`。

```bash
python3 pioneers.py --project-dashboard --workspace /path/to/project --detach
python3 pioneers.py --project-history --workspace /path/to/project
python3 pioneers.py --host-start WORKFLOW.json --workspace /path/to/project --register-only
```

使用 `--detach` 时，看板在独立进程中监听，命令输出 `dashboard_url` 和 `pid` 后返回。协调会话退出后仍可在看板审批。重新接入时，对原运行目录执行 `--host-ready RUN_DIR`，并领取已放行的任务。停止独立看板时，结束启动命令返回的 PID。省略 `--detach` 可在前台运行。

宿主用 `--register-only` 开新一轮时，返回的 `project_run_url` 指向同一项目看板里的当轮详情。保持项目看板运行即可领取任务。CLI 的真实交付也会自动登记。首页集中展示当前迭代、历史迭代和跨迭代待确认事项。点待确认事项可以进入对应审批，点协作流程可以查看任务交接、返工和进度。

每轮完成后，Pioneers 将结项摘要写入其 `status.json`。摘要包括目标、各角色交付、验收证据、审批理由、已登记的 Git 版本及发布结果。看板提供执行计划和产物链接，并支持下载 Markdown。重新打开迭代返工时会先移除结项摘要，重新完成后更新。单独查看上一轮的摘要时，可从 `--project-history` 读取 `latest_delivery`。

Git 项目在共享 Git 元数据中保存索引，专用 worktree 可共用。非 Git 项目把索引保存在项目下的 `.pioneers/iterations/`。索引引用原运行目录，需保留这些目录及其产物。启动时还会按项目匹配并补登默认输出目录内的旧运行。旧运行用了自定义输出目录时，启动项目看板或查询历史时传入 `--output-dir`。

## 查看执行进度

CLI 真实运行和宿主模式默认启动看板并打印地址。宿主模式在前台保持看板运行，从另一终端领取任务。`--register-only` 可先登记并退出，之后可使用已经运行的项目看板，或用 `--dashboard RUN_DIR` 启动单轮看板再领取。宿主领取时会检查对应看板能否响应。服务停止后，已领取任务仍可回报。

本地桌面默认尝试打开浏览器，可用 `--no-open-browser` 关闭自动开页。云端通过宿主提供私有预览地址。看板每秒更新，展示各角色的当前动作、可并行任务、等待的依赖和审批。运行结束后可以重新启动看板查看记录。离线模式显示交接预览进度。

将启动时打印的 `Run output` 路径替换到下面的命令里：

```bash
python3 pioneers.py --watch /path/to/run-dir
python3 pioneers.py --status /path/to/run-dir
python3 pioneers.py --dashboard /path/to/run-dir
```

`--watch` 持续显示看板直到运行结束，`--status` 查看一次。`--dashboard` 按领取成员展示任务交接。宿主用 `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name "显示名"` 记录接手人，同一成员在一次运行中使用同一个 ID。重跑可换成员，旧轮次保留原成员。未领取任务显示在「待分配」，未记录成员的历史任务和 CLI 角色执行仍显示角色列。

审批显示在它控制的交接线上，QA 退回用单独的箭头和返工记录展示。每轮保留证据链接。看板确认时可填写确认人，CLI 同样可用 `--approve ... --approver "显示名"` 记录操作者自填姓名。最新任务状态、最近 100 条动作及更早的证据记录保存在运行目录的 `status.json`。
