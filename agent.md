# pioneers 团队协作说明

[English](agent.en.md)

当用户把这个仓库交给你执行项目任务时，由 pioneers 登记工作流并管理任务状态。宿主平台负责执行 pioneers 已分派的任务。涉及跨团队交接时，共享工作流、交付物路径和验收证据。

目标项目接入后，后续会话和同一会话里的新需求均沿用 Pioneers。上一轮已完成时登记新的运行。首次真实运行会在目标项目的 `AGENTS.md` 保留这项规则。为旧项目补登记时可运行 `python3 pioneers.py --bind-project --workspace PROJECT`。

## 组建团队

| 角色 ID | 职责 |
| --- | --- |
| `pm` | 产品负责人，明确用户目标、首版范围和验收条件，处理需求歧义。 |
| `ld` | 交付负责人，明确分工、依赖、共享契约和最终集成负责人。 |
| `engineer` | 交付工程师，负责一条用户路径或明确的子任务，完成设计、实现、自测并提交证据。 |
| `qa` | 独立验证，对照验收条件核查代码、实际行为和测试证据。 |

用户只要求组建团队时，交付团队名单、角色职责和执行方式。用户同时给出项目任务时，启用完成该任务需要的角色。

## 为角色分配 Tool 和 Skill

从 [capabilities.json](capabilities.json) 的 `roles` 读取角色可用的 Tool 和 Skill。同一项能力可以分配给多个角色。交付工程师按任务使用 Web、API 或客户端能力。

- 工作流的 `tools` 给出任务选用的 Tool，`skills` 给出需要预先提供的 Skill。做任务时也可按需读取角色获配的 `SKILL.md`。
- 跨端或跨模块时，用架构设计 Skill 先确定职责、接口和验证方式，再把契约交给各开发角色。实现与重构时用编程设计 Skill 划分状态和副作用，独立核对实际变更时用代码评审 Skill。
- 根据任务选择新增能力。构建失败、行为异常或 QA 退回后用 `systematic-debugging`。并行实现汇合或跨模块联调用 `change-integration`。新增关键行为或沉淀缺陷回归测试时用 `test-engineering`，独立验收任务仍按验收条件逐项给出证据。
- Chrome 扩展开发和独立验证任务选择 `chrome-extensions`，按涉及的 Manifest V3 能力查阅该 Skill 随附的参考资料。
- 桌面应用按目标技术栈选择 `desktop-framework-electron`、`tauri-app-builder`、`macos-spm-app-packaging` 或 `winui-dev-workflow`。Windows 原生 UI 验证可选择 `winui-ui-testing`。打包、签名和迁移按任务选择对应的参考资料或 Skill。
- 有终端时，使用 [README.md](README.md) 的 `--run-tool` 命令运行角色获配的检查。保留工具返回的结果作为交接证据。平台的原生工具和访问权限仍由平台管理。
- 连接 Git、远端仓库或浏览器 MCP 时，使用 `--mcp-config ROLE --workspace PATH` 获取该角色的配置。执行端负责启用其中的工具白名单和认证。Git 写操作分配给开发角色，远端仓库操作为只读。

## 启动与派工

先确认交付项目的独立工作目录。维护已有产品时用项目仓库根目录或它的专用 Git worktree。创建新产品时，在平台授权的可写目录下新建项目，例如与 pioneers 并列的 `todo-app/`，并用 `git init` 初始化。为这次运行显式传入 `--workspace PROJECT`。启动协调命令可以在 pioneers 检出目录执行，开发、测试和预览都在交付项目目录执行。

如果用户在对话中给出提供商密钥文件，先在运行看板和派工的机器上执行 `python3 pioneers.py --configure-provider-env "$HOME/env/apikey.env" --workspace PROJECT`，将示例路径换成用户给出的路径。启动或复用看板后，在「模型管理」核对提供商连接状态并刷新目录；只向用户报告状态。宿主与用户机器不共享文件时，请用户把文件提供给执行机器。

为项目复用一个运营看板。先用 `--project-history --workspace PROJECT` 查询运行和上一轮已交付摘要，LD 规划时核对当前代码、决策、验收和遗留事项。若项目看板尚未启动，用 `--project-dashboard --workspace PROJECT --detach` 独立启动，保留命令返回的地址和 PID，默认端口 8790。已使用其他固定端口或云端私有预览时，复用现有入口。仅恢复或介入已有迭代时保留原运行目录。

宿主启动执行端时，用领取结果中的 `workspace` 设置工作目录，并按 `sandbox` 落实访问权限。宿主无法指定执行目录时，报告阻塞并暂停派工。

新运行启动、CLI 用 `--continue RUN_DIR` 续跑，或宿主用 `--dashboard RUN_DIR` 重新打开已有运行时，Pioneers 会按能力清单刷新全部开源 Skill 来源。宿主重新启动后，等看板服务恢复再领取新任务。

### 选择迭代流程

先确定本轮最小可交付范围。写入范围和验收条件明确、风险低且易于回滚时，默认只安排两项任务。已有任务和已完成的验收在重规划时继续复用。

1. `engineer` 在同一任务中确认验收条件，完成实现、自测和必要的集成，交付具体版本和证据。
2. `qa` 直接依赖实现任务，设置 `review: true`，在交付版本上独立核验主路径、相关回归和未覆盖项。

涉及架构、共享契约、安全与权限、难以回滚的数据变更或重要用户动线时，先安排只读方案，说明影响、验证和回滚方式。按影响标记 `architecture` 或 `feature_design`，让实现依赖获确认的方案，再继续独立验收。高成本实施和对外操作分别使用原有的 `costly_change` 和 `effect` 审批。范围或风险还不清楚时，先核对影响和验收边界。

### 登记和执行工作流

1. LD 按 [workflow.schema.json](workflow.schema.json) 拆出工作计划，或用 CLI 的 `--goal` 生成。以满足验收标准后的最短交付时间为目标。先安排会阻塞多个下游的决定与共享契约；`depends_on` 只列开工前必要的产物。让可独立修改、验证和交接的任务尽早并行。共享契约确定后，前端可用 Mock 开发，QA 可同时准备验收。多人修改同一项目时明确各自范围、隔离方式和集成负责人。每项实现任务安排下游独立 QA，实际验收任务标记 `review: true`。
   - 给本轮迭代填写简短的单行 `title`，用于运行看板。将完整需求、范围与约束保留在 `goal`。
   - 涉及共享契约、默认值或灰度、旧数据或并行写入、共享资源、权限及跨模块合入的任务，选择 `change-impact-preflight`。需要绑定实际交付版本的实现与独立验收任务，选择 `verifiable-delivery-evidence`。可局部修复并定向复验的任务，可参照 [单模块缺陷修复示例](examples/small-fix.json)。
   - 需要确定重要交互方案的新页面、整页改版或关键动线变更先安排只读设计，并为设计角色选择 `product-experience-design`，QA 按需选择同一能力。交付可核对的设计图、页面布局、导航与图标、重点状态、交互和窄屏规则。实现任务用 `ui_design` 列出设计任务 ID，同时把它们列入 `depends_on`，按现有流程等待设计放行。独立 QA 直接依赖设计和实现。边界清楚的局部小修可由工程师先写预期表现，再实现和自测。
   - 设计和开发可使用 [已有项目 UI 改版示例](examples/existing-project-redesign.json)。有访谈、反馈或行为数据需要归纳时，PM 按需选择 `customer-journey-mapping`。
2. 有可用运行器时启动 `pioneers.py ... --live`，由 CLI 派工。使用宿主原生 Agent 时，优先复用项目看板，用 `--host-start WORKFLOW --workspace PROJECT --register-only` 登记新迭代。JSON 返回 `run_dir`、`project_dashboard_url` 和 `project_run_url`。项目看板不可用时，可用 `--dashboard RUN_DIR` 启动单轮看板。看板停止后，先恢复看板服务再领取任务。
3. 宿主协调者用 `--host-ready RUN_DIR` 查询 `ready`、`active` 和 `waiting`。有成员容量时，同批领取独立的任务并优先较长的依赖链。领取用 `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name DISPLAY_NAME`。若领取因写入冲突被阻塞，等关联任务交接后重查队列。领取成功后交给执行端返回的提示、角色、工作目录、分支、复杂度和回报方式。每次产物放行、审批通过、恢复或退回后重查队列。没有并发能力的宿主按队列顺序执行。
   - 从 `--host-ready` 读取就绪项的 `model_candidates`，结合任务说明、复杂度和 `model_policy.preference` 选型。默认可选免费和收费型号，项目要求「仅免费」时只选可确认免费的候选。项目已指定型号或有适用配置时沿用既定选择。其余任务可直接领取，由框架顺序预检候选，也可结合交付要求从候选中选定一个，在领取时传 `--model-choice PROVIDER/MODEL_ID`。该选择只用于当前任务。用户希望仅在本机执行时用 `--profile host`，需要沿用项目命名配置时用 `--profile NAME`。
   - 独立验收、浏览器、MCP 和外部操作交给宿主。远端补丁需要隔离工作区、明确的文件范围和项目测试命令。视频使用 `video_prompt`，图片、旁白、配乐、音效填写 `media.type` 和 `media.prompt`。核对领取返回的 `executor.provider`，远端任务用 `--host-exec RUN_DIR --task ID --attempt N` 执行，宿主任务交给相应成员。遇到已提交的媒体请求，沿用原派工和回执接续。[模型选择与恢复](docs/usage.md#按任务选用模型供应商)
4. 宿主协调者或执行端使用 `--host-report` 回写里程碑、阻塞和完成情况。工作流用 `write_paths` 声明确切写入路径，未知范围按整个项目安排。Pioneers 在干净且已提交的 Git 项目上自动分配工作区及分支。工程师在返回的分支本地提交后回报，将测试证据写到运行目录或项目外的交付文件。需要接续代码的任务用 `depends_on`，并行修复安排下游集成及独立 QA。集成工作区领取时会合入相应依赖的已交接提交。最后向用户提供集成分支、工作区和验证结果。
5. QA 用 [review.schema.json](review.schema.json) 逐项报告验收证据。实现问题用 `changes_requested` 和 `repair_task` 退回。Pioneers 会重新排队实现任务和受影响的下游，保留上一轮证据。领取后按照返回的 `attempt` 上报修复和复验。需求歧义交回产品负责人，环境、依赖或证据缺失交回 LD。
   - UI 还原验收用同一数据场景和视口，把确认的设计稿与候选版本的真实浏览器截图对照，核对布局、视觉层级、图标语义和操作路径。可接受的字体渲染或少量像素差异写进报告。关键偏差退回实现，或走设计确认后更新验收基线。通过时在 `visual_evidence` 附设计稿、实际截图、视口和场景。浏览器不可用或缺少截图时用 `blocked`，把相关项标为 `unverified`。
6. 目标需要发布时，启动运行时加 `--require-release`。验收通过后，由 LD 通过 `--release` 登记开始、结果和真实访问地址。只有发布成功后，看板才显示「已发布」。
7. 在计划里标记重大架构和重要功能方案的 `impact`，先形成只读方案再交下游。高成本实施标 `costly_change`。对外发送、发布、付款、权限变更、删除等操作用独立 `effect` 任务。前置产物齐备后由人授权执行范围。
8. 每次角色提交产物后，宿主把回报里的 `judgment` 交给只读 LLM 判断，再用 `--judge` 回写。LLM 判断和人工确认未完成时，遵循 Pioneers 的依赖阻塞。只有实际用户操作才能调用 `--approve`、`--revise`、`--pause`、`--intervene` 或 `--resume`。收到暂停信号时，停止领取新任务，并收集已开始任务的回报。
9. 把 `--host-ready` 返回的 `replan` 当作待处理的 LD 任务。等 `replan.ready` 为真后，用 `--replan RUN_DIR` 获取用户指令、当前计划和状态，将返回的提示交给只读 LD。将 LD 返回的 JSON 用 `--replan RUN_DIR --revision N --artifact PLAN.json` 提交，随后重新查询并派发更新后的任务。
10. 暂时无任务可执行且运行未结束时，告知用户在看板需要处理的事项，然后在同一协调会话运行 `--host-ready RUN_DIR --wait 25` 等待。收到 `timed_out` 后继续等待，阶段交付结束也先重查队列。状态就绪后直接领取任务、处理待判断的交付物、重规划，或在 `release_ready` 为真时按已审批范围开始发布。若用户手动暂停了新任务，等看板「继续领取新任务」再派发。宿主会话已关闭时，从同一运行目录重新接入。

宿主领取返回的 `attempt` 用于之后的心跳、回报和重派。命令型执行端用 `--host-exec RUN_DIR --task TASK_ID --attempt N --exec-command COMMAND ...` 启动，由框架负责进程存活期间的续约和退出清理。保持执行命令前台运行，并把运行记录放在框架进程有写权限的目录。无法包装为本地命令的原生会话，由宿主提供独立的存活检查和续约能力。宿主能观察执行端存活时，可每 60 秒用 `--host-heartbeat RUN_DIR --task TASK_ID --attempt N` 续约，前提是已观察到执行端仍活跃。实际进度回报也会续约。发现执行器异常退出时可用 `--host-report ... --state lost`；重新查询 `--host-ready` 处理回收后就绪的任务。看到「待人工恢复」时，呈现原执行端、任务版本和已有证据，由操作者核对后重跑或接续产物。需要重跑的外部操作仍按新的授权审批派发。CLI 调度进程退出后用 `--continue RUN_DIR` 接回原运行。

需要成员续接时，优先使用 `--host-ready` 返回的 `preferred_member`。把 `continuation` 随当前分配转交给执行端。宿主可用 `--host-checkpoint` 保存阶段摘要和操作状态，外部操作应记录开始和可确认的结果。旧执行端已停止并收到 `resume_session_id` 时，可以使用宿主的原生会话续接能力。否则用检查点和操作记录开启新执行，按当前工作区和审批范围工作。涉及结果不明的外部操作，由操作者查验回执并登记恢复检查，避免重放已发生的操作。

发现项目分支已有待派发或待恢复实现任务的合入提交时，先核对实际提交、证据、待验证项及旧执行端状态。由用户在看板登记，或经用户授权用 `--host-adopt` 接续。继续处理产物判断及尚未通过的下游 QA，验收报告应绑定实际验证的提交。

命令示例与进度入口见 [README.md](README.md)。

## 向用户同步进度

首次派工前，确认看板服务内的项目入口和当轮详情的 `dashboard-data.json` 正常响应，再向用户展示初始分工。有可访问的项目地址时一并提供。通过「派工判断」查看谁在执行、哪些任务可以并行、哪些任务在等待依赖或审批。本地桌面默认尝试打开浏览器。

在云端运行时，若宿主具备部署和查看私有实时站点的能力，使用宿主提供的工具部署看板。优先复用已有私有入口或使用私有 HTTPS 端口预览，连接当前运行的看板服务。部署后从远程入口验证页面和当轮的 `dashboard-data.json`，再向用户提供已验证的地址。涉及看板审批时，按 [云端看板操作](README.md#云端看板操作) 配置转发和控制凭证。

宿主缺少部署或查看能力时，跳过云端部署。如果跳过部署，或者部署后无法验证远程入口，继续派工并用运行记录在对话内同步「角色｜任务｜状态｜当前动作」。

- 用户需要在云端看板提交人工操作时，由操作者在私有终端按 README 的云端看板方式启动，并从该终端取得控制凭证。确认预览入口可用后给用户预览地址。控制凭证应只由操作者持有。
- 每次角色开工、交接、遇到阻塞或完成时，更新对应角色的状态，并简短汇报当前动作或已产生的证据。
- 耗时任务有实质进展时也要汇报，例如「前端已接好表单，正在验证保存和刷新」。协调者应让独立会话汇报这些里程碑。
- 根据执行端实际返回的状态汇报。如果平台尚未提供某角色的新消息，就显示最近确认的动作或等待原因。

交付完成后，在项目看板打开当轮迭代摘要，核对任务回报和验收证据。向用户交接时提供摘要入口，以及已知的遗留问题或下一轮需处理的事项。摘要由 Pioneers 根据已登记的产物和决策生成。

## 本地执行入口

用户选择本地 CLI 时，可以在 pioneers 的检出目录创建团队配置。需要 Python 3.9 或更高版本。

```bash
python3 pioneers.py --create-team --workspace /path/to/project
```

工具把团队配置写入目标 Git 项目的 `.pioneers/team.json`，并以 JSON 返回 `status`、`manifest` 和 `team.roles`。如需设置默认模型，创建时可追加 `--model MODEL_ID`。运行方式见 [README.md](README.md)。

本地 CLI 也能从目标自主分工：

```bash
python3 pioneers.py --goal "做一个能创建待办的 Web 页面" \
  --live --runner /path/to/agent-runner --workspace /path/to/project
```

运行工作流时，终端显示角色动态。也可以在另一个终端运行 `python3 pioneers.py --watch RUN_DIR`，其中 `RUN_DIR` 是启动时打印的 `Run output` 路径。
