# pioneers 团队协作说明

当用户把这个仓库交给你执行项目任务时，由 pioneers 登记工作流并管理任务状态。宿主平台负责执行 pioneers 已分派的任务。涉及跨团队交接时，共享工作流、交付物路径和验收证据。

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
- 有终端时，使用 [README.md](README.md) 的 `--run-tool` 命令运行角色获配的检查。保留工具返回的结果作为交接证据。平台的原生工具和访问权限仍由平台管理。
- 连接 Git、远端仓库或浏览器 MCP 时，使用 `--mcp-config ROLE --workspace PATH` 获取该角色的配置。执行端负责启用其中的工具白名单和认证。Git 写操作分配给开发角色，远端仓库操作为只读。

## 启动与派工

先确认交付项目的独立工作目录。维护已有产品时用项目仓库根目录或它的专用 Git worktree。创建新产品时，在平台授权的可写目录下新建项目，例如与 pioneers 并列的 `todo-app/`，并用 `git init` 初始化。为这次运行显式传入 `--workspace PROJECT`。启动协调命令可以在 pioneers 检出目录执行，开发、测试和预览都在交付项目目录执行。

宿主启动执行端时，用领取结果中的 `workspace` 设置工作目录，并按 `sandbox` 落实访问权限。宿主无法指定执行目录时，报告阻塞并暂停派工。

新运行启动、CLI 用 `--continue RUN_DIR` 续跑，或宿主用 `--dashboard RUN_DIR` 重新打开已有运行时，Pioneers 会按能力清单刷新全部开源 Skill 来源。宿主重新启动后，等看板可访问再领取新任务。

1. LD 按 [workflow.schema.json](workflow.schema.json) 拆出工作计划，或用 CLI 的 `--goal` 生成。以满足验收标准后的最短交付时间为目标。先安排会阻塞多个下游的决定与共享契约；`depends_on` 只列开工前必要的产物。让可独立修改、验证和交接的任务尽早并行。共享契约确定后，前端可用 Mock 开发，QA 可同时准备验收。交接成本高的轻量任务由一名工程师端到端完成。多人修改同一项目时明确各自范围、隔离方式和集成负责人。每项实现任务安排下游独立 QA，实际验收任务标记 `review: true`。
2. 有可用运行器时启动 `pioneers.py ... --live`，由 CLI 派工。使用宿主原生 Agent 时，在可保持运行的终端执行 `--host-start WORKFLOW --workspace PROJECT`，默认启动看板。首行 JSON 返回 `run_dir` 和 `dashboard_url`，后续派工在另一个终端进行。需要分两步启动时，先用 `--host-start ... --register-only` 登记，再用宿主能维持的运行方式启动 `--dashboard RUN_DIR`。看板停止后，先重开看板再领取任务。
3. 宿主协调者用 `--host-ready RUN_DIR` 查询 `ready`、`active` 和 `waiting`。有可用成员时，同批领取并启动安全独立的就绪任务，容量不足时优先较长的未完成依赖链。领取用 `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name DISPLAY_NAME`，为实际接手的成员使用稳定 ID。写入范围发生冲突时先隔离改动或串行安排，并说明阻塞。领取成功后，把任务提示、角色、工作目录、复杂度和回报方式交给执行端，由平台选择模型和推理强度。每次产物放行、审批通过、恢复或退回后，重查队列并派发新就绪任务，避免等整批结束。没有并发能力的宿主按队列顺序执行。启动 CLI 或登记失败时，报告具体阻塞并暂停派工和实现。
4. 宿主协调者或执行端使用 `--host-report` 回写运行记录。首次动作、可见的里程碑、交接、阻塞和完成都要回写。完成时附上可读取的文件作为交付物，里面记录变更文件、测试结果或其他可核对的证据。下游领取时会获得上游交付物。
5. QA 用 [review.schema.json](review.schema.json) 逐项报告验收证据。实现问题用 `changes_requested` 和 `repair_task` 退回。Pioneers 会重新排队实现任务和受影响的下游，保留上一轮证据。领取后按照返回的 `attempt` 上报修复和复验。需求歧义交回产品负责人，环境、依赖或证据缺失交回 LD。
6. 目标需要发布时，启动运行时加 `--require-release`。验收通过后，由 LD 通过 `--release` 登记开始、结果和真实访问地址。只有发布成功后，看板才显示「已发布」。
7. 在计划里标记重大架构和重要功能方案的 `impact`，先形成只读方案再交下游。高成本实施标 `costly_change`。对外发送、发布、付款、权限变更、删除等操作用独立 `effect` 任务。前置产物齐备后由人授权执行范围。
8. 每次角色提交产物后，宿主把回报里的 `judgment` 交给只读 LLM 判断，再用 `--judge` 回写。LLM 判断和人工确认未完成时，遵循 Pioneers 的依赖阻塞。只有实际用户操作才能调用 `--approve`、`--revise`、`--pause`、`--intervene` 或 `--resume`。收到暂停信号时，停止领取新任务，并收集已开始任务的回报。
9. 把 `--host-ready` 返回的 `replan` 当作待处理的 LD 任务。等 `replan.ready` 为真后，用 `--replan RUN_DIR` 获取用户指令、当前计划和状态，将返回的提示交给只读 LD。将 LD 返回的 JSON 用 `--replan RUN_DIR --revision N --artifact PLAN.json` 提交。随后重新查询并派发更新后的任务。只要有暂停、未处理的重规划或在途任务，继续在宿主允许的时间内轮询运行状态，阶段结束时也要重查 `--host-ready`。如果宿主会话已关闭，重新打开协调会话，从同一运行目录继续。

命令示例与进度入口见 [README.md](README.md)。

## 向用户同步进度

首次派工前，确认页面和 `/dashboard-data.json` 正常响应，再向用户展示入口和初始分工。通过「派工判断」查看谁在执行、哪些任务可以并行、哪些任务在等待依赖或审批。本地桌面默认尝试打开浏览器，云端使用宿主提供的私有预览地址。宿主无法提供用户可访问的入口时，说明限制并在对话内展示「角色｜任务｜状态｜当前动作」，执行中从 pioneers 运行记录同步更新。

- 用户需要在云端看板提交人工操作时，由操作者在私有终端按 README 的云端看板方式启动，并从该终端取得控制凭证。确认预览入口可用后给用户预览地址。控制凭证应只由操作者持有。
- 每次角色开工、交接、遇到阻塞或完成时，更新对应角色的状态，并简短汇报当前动作或已产生的证据。
- 耗时任务有实质进展时也要汇报，例如「前端已接好表单，正在验证保存和刷新」。协调者应让独立会话汇报这些里程碑。
- 根据执行端实际返回的状态汇报。如果平台尚未提供某角色的新消息，就显示最近确认的动作或等待原因。

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
