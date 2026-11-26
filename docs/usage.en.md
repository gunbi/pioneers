# Pioneers user guide

[简体中文](usage.md)

Run the commands in this guide from the repository root. For an introduction to how the team works, read the [project overview](../README.en.md).

## Using Pioneers on an agent platform

Give the repository's Git URL to an agent that can read it, along with this instruction.

> Read agent.md in the repository root and start this delivery with Pioneers. Give me the run dashboard before assigning work. Then claim, execute, and report tasks in dependency order.

The [coordination guide](../agent.en.md) defines roles, capability assignments, handoffs, and progress reporting. Pioneers registers work, schedules tasks, and records acceptance. The platform provides the agents and tools that do the work. If Pioneers cannot start, report the blocker and pause assignments. The [to-do example](../examples/todo.json) shows the handoff from the product owner to a delivery engineer and then to independent QA.

On the first host-coordinated run, if neither SiliconFlow nor OpenRouter is configured, the host asks whether to set up an API key. Reply with the path to a credential file on the execution machine, or say “skip” to continue with the host. You can connect a provider later.

Once a provider is available, the host asks how to choose models for this project. If credentials are already configured at startup, this is the first question. “Free models only” excludes paid models and models with unknown prices. “Prefer low latency” ranks matching models higher. Choosing the default allows both free and paid models. Subsequent iterations reuse your choice. You can change it in Model Management on the dashboard. [Provider credentials and dispatch](#model-providers-by-task)

The first live run adds a rule to the target project's `AGENTS.md` directing future sessions to use Pioneers. It preserves existing project instructions. To bind a project after a completed delivery, run `python3 pioneers.py --bind-project --workspace PROJECT`. Start subsequent sessions in the project root so agents that read project rules can follow Pioneers. Commit this rule with the project to make it available in other checkouts.

## Local CLI quick start

Use Python 3.9 or later. The CLI requires no additional Python packages.

```bash
python3 pioneers.py --list-roles
python3 pioneers.py examples/todo.json
```

The example rehearses an offline handoff. The product owner defines acceptance criteria, an engineer implements the change and tests it, and QA verifies it independently. The CLI prints the artifact directory, which defaults to a run under `.pioneers/runs/`. Each task produces a Markdown file previewing the instructions and upstream artifacts it would receive.

## Roles and workflows

| Role ID | Responsibility | Workspace access |
| --- | --- | --- |
| `pm` | Product owner. Defines the goal, scope, and acceptance criteria, and resolves ambiguous requirements. | Read-only |
| `ld` | Delivery lead. Coordinates assignments and dependencies, defines shared contracts, and names the integration owner. | Read-only |
| `engineer` | Delivery engineer. Designs, implements, tests the change, and supplies delivery evidence. | Read/write |
| `qa` | Independent QA. Checks the actual changes, execution results, and acceptance evidence. | Read-only |

Start with the [to-do workflow](../examples/todo.json). Define each task's role, dependencies, capabilities, and acceptance criteria. The local CLI schedules tasks sequentially in dependency order in one workspace. Existing hand-written workflows can continue to use the legacy technical role IDs.

When a QA task enables `review`, a live run saves a structured acceptance report. A failed implementation criterion can return a completed upstream implementation task via `changes_requested`. When several tasks qualify, set `repair_task` in the report to the responsible task ID. Pioneers keeps the previous evidence and requeues that task and its affected dependents. If any affected dependents are still running, wait for their reports before submitting the return decision. Refer ambiguous requirements to the product owner and environment problems to the delivery lead, and pause the current run.

Initialize a reusable team configuration with the following command.

```bash
python3 pioneers.py --create-team --workspace /path/to/practice-repo
```

This writes `.pioneers/team.json` in the target Git project and returns JSON. Running it again returns the existing team. Legacy default teams migrate to the four roles above and retain their model settings.

## Role capabilities

The [capability manifest](../capabilities.json) lists the tools and skills available to each role. Run `python3 pioneers.py --list-capabilities engineer` to see the delivery engineer's catalog.

Each engineering task selects its tools in `tools`, for example `["frontend_html_check", "pioneers_git.git_diff"]`. An empty array means the task requires no built-in or MCP tools. Use `skills` to preload guidance. Members can also read a skill assigned to their role when a task calls for it.

```bash
python3 pioneers.py --run-tool architecture_check --role engineer --workspace /path/to/project --input architecture.json
python3 pioneers.py --run-tool frontend_html_check --role engineer --workspace /path/to/project --input index.html
python3 pioneers.py --run-tool backend_api_check --role engineer --workspace /path/to/project --input openapi.json
```

Built-in checks return JSON and an exit code. The [architecture design skill](../skills/architecture-design/SKILL.en.md) documents the architecture JSON format. The HTML and OpenAPI checks verify basic structure. Project tests verify user flows and API behavior.

### Engineering design and delivery

| Skill | Assigned roles | Use |
| --- | --- | --- |
| [Architecture design](../skills/architecture-design/SKILL.en.md) | Delivery lead, engineer | Identify complexity and record trade-offs, module boundaries, and shared contracts. |
| [Product experience design](../skills/product-experience-design/SKILL.en.md) | Product owner, engineer, QA | Derive navigation, user flows, layouts, failure recovery, and acceptance scenarios from user tasks. |
| [Customer journey mapping](../skills/customer-journey-mapping/SKILL.en.md) | Product owner | Use interviews, feedback, or behavioral data to identify touchpoints and obstacles across stages of a journey. |
| [Change impact assessment](../skills/change-impact-preflight/SKILL.en.md) | Delivery lead, engineer | Check the baseline, shared contracts, existing data, and rollback conditions. |
| [Verifiable delivery evidence](../skills/verifiable-delivery-evidence/SKILL.en.md) | Delivery lead, engineer, QA | Tie reproducible findings to the actual commit or build. Independently read back the results where the risk warrants it. |
| [Engineering design](../skills/engineering-design/SKILL.en.md) | Engineer | Separate business rules, state, side effects, and scheduling. Implement and test against the contract. |
| [Code review](../skills/code-review/SKILL.en.md) | Delivery lead, QA, engineer | Examine the changes, interface compatibility, and acceptance evidence. Distinguish blocking defects from suggestions. |
| [Systematic debugging](../skills/systematic-debugging/SKILL.en.md) | Engineer, QA | Reproduce a problem, verify its cause, and retest the fix. |
| [Change integration](../skills/change-integration/SKILL.en.md) | Delivery lead, engineer | Merge parallel changes, then build and verify the integrated version. |
| [Test engineering](../skills/test-engineering/SKILL.en.md) | Engineer, QA | Choose the test level by risk. Turn critical behavior and reproduced defects into repeatable tests. |

The [to-do creation workflow](../examples/todo.json) shows one engineer delivering a feature end to end. The [multi-device sync workflow](../examples/todo-sync.json) adds a shared contract, separate engineering tasks, integration, and independent acceptance. Run the following example to inspect the role and skill handoffs.

```bash
python3 pioneers.py examples/todo-sync.json
```

When the write scope and acceptance criteria are clear, the risk is low, and the change is easy to roll back, the default plan has two tasks. An engineer implements, tests, and integrates the change as needed, then hands a specific version to independent QA for verification. The [single-module bug fix](../examples/small-fix.json) is a reusable two-task plan. Changes to architecture, shared contracts, security, permissions, or hard-to-reverse data require an impact assessment, a proposal, and approval before implementation.

For important UI changes, define the user flow and layout first, then load `frontend-design` as needed for visual direction. A product owner can deliver the design for a complex flow. For a simple layout, an engineer can design and implement it in one task and ask QA to verify the scenarios.

### Redesigning an existing project

The [existing project redesign](../examples/existing-project-redesign.json) uses a product category page. It records the existing baseline, reviews the new flow and layout, implements the design, freezes a candidate commit, runs an independent comparison, and hands the candidate over for merging into the target branch. QA receives the design, baseline, and candidate artifacts from other tasks through direct dependencies.

Baseline and repeat verification load `existing-ui-audit`, drawing on Impeccable's experience review and technical audit. Implementation loads `existing-ui-redesign`, which references TasteSkill's original redesign method. The reference is pinned to a commit containing the full text because upstream removed the skill from its main branch. Candidate integration and the merge handoff load `change-integration`, which follows Superpowers' branch completion guidance.

```bash
python3 pioneers.py examples/existing-project-redesign.json
```

This command rehearses the handoffs. For a live run, copy the example, replace the pages, scenarios, and acceptance criteria with those of the existing target project, and set actual `write_paths` for each writing task. Document the startup command, test data, and smoke checks in the project's `AGENTS.md` beforehand. When first connecting an existing project, run `python3 pioneers.py --bind-project --workspace PROJECT`, review the binding rule in `AGENTS.md`, commit it as part of the baseline, and start from a clean workspace. Prefer host mode with `--workspace PROJECT` pointing to a clean Git repository with at least one commit. Pioneers then allocates isolated worktrees. For `--live`, first create a separate candidate branch and worktree from the target branch and pass that worktree to `--workspace PROJECT`. The example uses `impact: feature_design` so the operator can approve the layout and flow after design delivery and before implementation.

Independent QA should first verify that the current HEAD equals the candidate SHA, then repeat the baseline scenarios. The workflow delivers the accepted candidate and merge steps. Once authorized to merge, the operator verifies the target branch and workspace in the target repository and merges the candidate according to project procedure. For a local delivery, use `git merge --ff-only <accepted-sha>` on a clean target branch. If the target has moved and fast-forwarding is impossible, or the merged HEAD differs from the accepted SHA, integrate again and verify the new delivery version. Record the actual target HEAD and smoke-test result. Record acceptance and merge as separate milestones. Remote pushes and deployments follow the project's respective authorization processes.

### MCP tools

| Capability | Roles and scope |
| --- | --- |
| Local Git | The delivery lead and QA can inspect status, diffs, and history. Engineers can also select staging, committing, and branch creation for a task. The service is restricted to the target repository. |
| Remote repositories, issues, and pull requests | All four roles can read authorized content as needed for a task. |
| Browser | Engineers and QA can interact with test pages, take screenshots, and inspect the console. Each run has an isolated browser session. |

```bash
python3 pioneers.py --mcp-config engineer --workspace /path/to/project
```

The Git service in this configuration uses `uvx`, and the browser service uses `npx`. The execution environment must supply the commands and a usable browser. Remote tools authenticate through the MCP client or read `GITHUB_PERSONAL_ACCESS_TOKEN` from the execution environment. The configuration file records only the variable name.

The local executor receives the MCP configuration for its role. Other platforms can export it with the command above and connect via their native MCP support. The executor should preserve the `enabled_tools` allowlist.

### Third-party skills

- Engineers use `frontend-design` when designing web interfaces.
- Engineers and QA load `webapp-testing` as needed. Its Python test scripts require Playwright for Python and the corresponding browser.
- Developers and QA load `chrome-extensions` for Manifest V3, extension debugging, and Chrome Web Store release preparation. The upstream references and Apache-2.0 license are included. [Google Chrome's upstream skill](https://github.com/GoogleChrome/modern-web-guidance/blob/64a6d708f5a43f2cd3fde5d4d7db3905ba1bdf65/skills/chrome-extensions/SKILL.md)
- Desktop developers and QA choose `desktop-framework-electron`, `tauri-app-builder`, `macos-spm-app-packaging`, or `winui-dev-workflow` according to the target stack. Native Windows projects can also load `winui-design`, `winui-code-review`, `winui-ui-testing`, `winui-packaging`, `winui-setup`, or `winui-wpf-migration` as appropriate. Each directory retains its upstream references and license.
- Engineers and QA load `ios-development` and `android-development` for relevant tasks. Legacy iOS and Android compatibility roles can also load the corresponding skill.
- Engineers and QA use `wechat-miniprogram-development` to implement WeChat mini programs, integrate APIs, and verify behavior independently. The host supplies WeChat DevTools and physical devices.
- Product owners use `requirements-design` to define requirements and acceptance criteria. QA uses `acceptance-testing` for independent verification.

The `frontend-design` and `webapp-testing` directories retain the licenses, available scripts, and examples from a [pinned version of Anthropic's upstream repository](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills).

The two Anthropic skill files are the repository's base versions. When adding or updating an open-source skill reference, credit the source in the local `SKILL.md` and register its GitHub repository and Markdown path under `skills.<id>.sources` in the [capability manifest](../capabilities.json). The existing refresh process automatically includes new entries.

Pioneers checks upstream sources against the current manifest when creating a host run, starting a live CLI run, resuming the CLI with `--continue RUN_DIR`, or reopening a host dashboard with `--dashboard RUN_DIR`. It writes the results to `skill-sources.json` in the run directory. Each `sources` entry has the form `[repository, Markdown path, optional branch or commit]`. The default branch is `main`. New assignments receive the refreshed content. Claimed tasks retain the content supplied at assignment. Refreshing the browser page only updates dashboard state.

After loading a local skill, a role sees upstream excerpts for the selected capability. Local conventions, scripts, and permissions take precedence. Refresh reads Markdown only. When the network is restricted, Pioneers uses the latest cache. A first offline run uses the local text. Set `PIONEERS_SKILLS_OFFLINE=1` to skip network refresh explicitly. Offline rehearsals do not access the network.

## Let the delivery lead plan from a goal

Both live CLI and host runs require the project's explicit `--workspace`. An existing project can use its directory or a dedicated Git worktree. To start a new project from the framework checkout, run `git init ../todo-app` and pass `--workspace ../todo-app`. Startup checks that the project directory is separate from the framework repository. The CLI launches tasks in the project. A host must use the `workspace` returned on claim as the working directory and enforce the access specified by `sandbox`. Use `--output-dir` to manage run records separately.

A live CLI run requires a Git project root or worktree. Configure a custom executor with `--runner`. If the adapter selects its model, you can omit `--model`.

```bash
python3 pioneers.py --goal "Build a web page that lets me create to-do items" \
  --live --runner /path/to/agent-runner --workspace /path/to/practice-repo
```

The delivery lead first plans in read-only mode, choosing roles, dependencies, skills, complexity, and acceptance criteria. The CLI validates the plan, saves it as `workflow.json` in the run directory, and executes it in order. Add `--plan-only` to generate the plan and inspect assignments. To use an existing plan, pass its JSON file directly.

```bash
python3 pioneers.py examples/todo.json \
  --live --runner /path/to/agent-runner --workspace /path/to/practice-repo
```

If you set a default model with `--model` during initialization, the CLI passes that model at execution time. Run arguments can override it. For each task, the CLI invokes the executor with the role and required workspace permissions. If the executor exits unexpectedly, follow the recovery rules below. Previously submitted artifacts remain available.

## Using one plan with different executors

The [workflow schema](../workflow.schema.json) defines a common plan structure. Both execution modes share Pioneers run records, upstream deliverables, and independent acceptance.

### Human approval and intervention

When an executor submits a live task, Pioneers saves the artifact and a judgment request. An LLM considers complexity, uncertainty in the evidence, visible disagreements between roles, and downstream impact. It records whether a person needs to confirm the result and why. Tasks depending on a result under judgment or awaiting confirmation cannot be claimed. Unrelated tasks can proceed.

| Workflow field | Rule |
| --- | --- |
| `impact: architecture` or `feature_design` | Produce a proposal before a major architectural or feature change. The proposal is a read-only assignment. A person must confirm it after submission before dependents can implement it. |
| `impact: costly_change` | For changes with high migration costs or extensive refactoring, confirm the scope before claim, then confirm the handoff after submission. |
| `effect: send / publish / payment / permissions / delete / irreversible` | Give the operation its own task. Once the prerequisites are available, a person must authorize the operation before claim. An LLM cannot grant this permission. |
| `--require-release` | After independent QA and task results are cleared, obtain human approval for release before external deployment. A retry after a failed release needs fresh approval. |

An LLM that detects a major design decision or a costly change in an ordinary task result also sends it for human confirmation. The host must declare external actions under `effect` before execution. Executors must stay within the scope and permissions returned by their claim.

In the dashboard's human review and intervention panel (人工确认与介入), enter a reason and select the relevant decisions. You can also use the CLI. Each confirmation, revision, pause, and resumption records the time and reason. In a cloud workspace, use the private HTTPS preview described below or open the local address via SSH forwarding.

```bash
python3 pioneers.py --approve RUN_DIR --decision D1 --decision D3 --reason "Approve both proposals"
python3 pioneers.py --approve RUN_DIR --all --reason "Checked each decision"
python3 pioneers.py --revise RUN_DIR --decision D2 --reason "Cover compatibility with existing data"
python3 pioneers.py --revise RUN_DIR --decision D2 --artifact revised.md --reason "Replace the design draft"
python3 pioneers.py --pause RUN_DIR --reason "The migration plan needs adjustment"
python3 pioneers.py --intervene RUN_DIR --from architecture --reason "Preserve the legacy client contract"
python3 pioneers.py --resume RUN_DIR --reason "Plan adjustment complete"
```

Revising a result retains the previous artifact, requests a new judgment, and reschedules dependent tasks. Pausing new assignments in the dashboard allows already claimed tasks to continue and report. In the plan adjustment panel, ask the delivery lead to update the plan or choose a task from which to restart. Restarting from a task (`--from` in the CLI) requeues it and its dependents. Save, then resume new assignments. When the lead revises the plan, Pioneers waits for running tasks to report, processes the request, validates and applies the new plan, and resumes automatically. Replanning cannot modify tasks that have started or received authorization. The original plan remains in `workflow.json` and later versions use `workflow.N.json`. If the CLI process has exited, submit the instruction and run `--continue RUN_DIR` to resume that run.

In host mode, use `--host-ready RUN_DIR` to inspect `replan.ready`. When ready, obtain the delivery lead's prompt with `--replan RUN_DIR` and use the returned `revision` to submit `--replan RUN_DIR --revision N --artifact PLAN.json`. If the host session has closed, restart the coordinator to process the request.

### Cloud dashboard controls

In a cloud workspace, use the host's private HTTPS port preview. Map the root of the preview domain to the dashboard port. The preview must forward GET requests, `POST /human-action`, and the browser's `Origin` and `X-Pioneers-Control` request headers. Replace the example origin with the actual preview domain.

```bash
python3 pioneers.py --dashboard RUN_DIR --port 8789 --dashboard-origin https://preview.example.com
```

The same options work with `--host-start`, `--live`, or `--continue RUN_DIR`. Open the configured HTTPS address in a new tab. On the first human action, enter the `Dashboard control key` printed in the launch terminal. The key stays in the current tab's memory and must be re-entered after a refresh. It expires one hour after startup. Restart the dashboard for the same run to obtain a new key. For an existing live run, restarting the separate dashboard service is sufficient.

The private preview controls who can read the dashboard. The control key grants permission to operate this dashboard, and everyone holding the same key has the same permissions. The key appears only on the launch terminal's stderr. Use a private terminal accessible to the operator. A preview without `--dashboard-origin` still permits viewing. In that case, submit human actions locally, through SSH forwarding, or via the CLI.

### Host-native execution

This mode is for hosts that provide a terminal and Python and can dispatch native agents. The coordinator generates a workflow and starts the dashboard in a terminal that can remain running. The first line of JSON returns `run_dir` and `dashboard_url`. Verify that the page and `/dashboard-data.json` respond, then open the entry point in a browser visible to the user. In a cloud workspace, use a private port preview or SSH forwarding and give the user the actual accessible address. Run the dispatch commands in another terminal, replacing `RUN_DIR` with the returned directory.

```bash
# Register the workflow and keep the dashboard service running.
python3 pioneers.py --host-start examples/todo.json --workspace /path/to/project

# Query ready tasks and blockers from a second terminal.
python3 pioneers.py --host-ready RUN_DIR

# Use the JSON returned by a successful claim as the assignment context.
python3 pioneers.py --host-claim RUN_DIR --task requirements

# Report progress, then attach the local deliverable on completion.
python3 pioneers.py --host-report RUN_DIR --task requirements --attempt 1 --state running \
  --used-skill requirements-design \
  --activity "Defining acceptance steps for blank titles and restoring state after refresh"
python3 pioneers.py --host-report RUN_DIR --task requirements --state completed \
  --activity "Documented three reproducible acceptance steps" --artifact /path/to/requirements.md
```

Each task card in the collaboration flow shows skills for that attempt. Planned skills (计划 Skill) come from the task definition. Loaded skills (已加载 Skill) were included in the execution prompt. Used skills (已使用 Skill) were reported by the host member. Replace `1` in the example with the `attempt` returned by the claim. Repeat `--used-skill` in the same report to record other authorized skills the member used. Each retry has its own record. For older runs, the card shows the information available for that attempt.

A completion report returns `judgment`, which contains `decision`, `task`, `attempt`, the prompt for the judging LLM, and the output schema. Give it to a read-only judgment session, then submit the JSON result. After a revision in the browser, the host can also query pending judgments and use `--judge RUN_DIR --decision D2` to retrieve the same request.

`--host-ready` is a read-only query. It orders `ready` by the number of steps in the longest unfinished dependency chain, longest first. `active` includes running tasks and tasks awaiting judgment. `waiting` lists missing artifacts, their states, or required approvals. The dashboard's dispatch view (派工判断) shows the same results and links directly to tasks and blocking approvals. Dispatch ready tasks together when host capacity and write scopes allow. As soon as an artifact is cleared, query again to start its dependents. Different members of the same role can claim independent tasks concurrently. Local `--live` execution runs sequentially in dependency order.

If a new host run starts from a clean Git workspace with an existing commit, engineers automatically receive their own worktree and branch on each claim. Set `write_paths` in the workflow to project-relative files or directories, for example `["checkout/pricing.py"]`. Tasks with overlapping write scopes wait for handoff. An empty array or a missing field in a legacy workflow covers the entire project. When the project has no commits, has local changes, or does not use Git, writing tasks run sequentially in a shared workspace.

Engineers work in the returned `workspace` on `git.branch` and commit local changes before reporting. Pioneers checks for uncommitted changes and files outside the allowed scope in the delivered revision. Express a dependency on a prior fix with `depends_on`. Workspaces for downstream implementation, integration, and QA merge cleared commits from their dependencies. A merge conflict stops the claim and leaves the conflicted worktree available. Resolve and commit the conflict, then retry. The delivery workflow arranges the final merge into the target branch. Pioneers retains task branches and worktrees for inspection.

While waiting for dashboard confirmation or when no task is ready, keep the coordinator session running and use `--host-ready RUN_DIR --wait 25`. The command returns immediately when approval unlocks work, a final release is authorized (`release_ready` becomes true), or the run ends. It also returns other actionable work, such as replanning or pending artifact judgments. If the wait expires, it returns `timed_out: true`. Wait again in the same session. A new host session can reconnect to the same run directory after the old one closes.

```bash
python3 pioneers.py --judge RUN_DIR --decision D2 --task requirements --attempt 1 --artifact judgment.json
```

An example `judgment.json` is `{"needs_confirmation":true,"reason":"The new data model affects three downstream interfaces","impact":"architecture","effect":null}`. A host task continues waiting if a valid judgment has not arrived in time.

A claim checks the run state and task dependencies. On completion, Pioneers archives the deliverable and generates downstream prompts that include prerequisite artifacts. A QA task configured for independent acceptance delivers its report as JSON. If the result is `returned`, claim the indicated `repair_task`, then claim the subsequent QA attempt. From the second attempt onward, include the `--attempt N` returned by the claim in every report. If execution fails or the environment blocks work, record the cause and stop claiming downstream tasks. After dispatch, the host must relay observable activity and results.

<a id="selecting-siliconflow-models-by-task"></a>

### Model providers by task

In host mode, configuring SiliconFlow or OpenRouter credentials enables automatic model selection from that provider's catalog. For most tasks, query the queue and claim work as usual.

```bash
python3 pioneers.py --host-ready RUN_DIR
python3 pioneers.py --host-claim RUN_DIR --task requirements
```

Each ready task eligible for automatic selection lists candidates in `model_candidates`, including `provider`, `model`, and `reason`. The host can use the task description, complexity, and `model_policy.preference` to choose one. For example, when condensing a short article, claim with `--model-choice siliconflow/Qwen/Qwen3.5-35B-A3B` if that model is listed for the task. Everything before the first slash identifies the provider. The remainder is the complete model ID. The choice applies to this claim only and leaves project policy intact. When the user explicitly requests local execution for a task, claim it with `--profile host --reason "user requested local execution for this task"` and supply their actual instruction. With credentials for an enabled provider, a local override for a remotely executable task requires a reason. Without one the task stays unclaimed. Claim normally for automatic routing, including independent QA and local tool tasks. Each attempt on the run dashboard shows its dispatch reason.

#### Built-in SiliconFlow quality preset

Starting a project saves this preset as its default. All later sessions and iterations share it. Older projects with no preset and an empty preference inherit it. Automatic claims and host candidate choices try eligible SiliconFlow models first, then other configured providers. Project pins, named configurations and explicit preferences such as free-only still apply.

When an authenticated provider rejects a request for insufficient account credit, the project disables that provider and shows the reason in Model Management. Safe retries use another eligible provider, or the host when none qualify. Accepted or ambiguous media submissions retain their receipts for reconciliation. Rate limits, timeouts and per-request budgets do not disable the account. After topping up, manually restore the provider in Model Management.

After connecting SiliconFlow, choose “SiliconFlow first, quality first” when the host asks how to select models. For an existing project, open Model Management → Dispatch Policy → Built-in Preset (内置策略), select it and click Apply. Expand the recommendations to preview the model order before applying.

| Task type | Preferred SiliconFlow models, in order |
| --- | --- |
| Text, including requirements, design and task breakdown | `deepseek-ai/DeepSeek-V4-Pro` |
| Code patches | `zai-org/GLM-5.3`, then `moonshotai/Kimi-K2.7-Code` |
| Text to image | `OpenMOSS-Team/MOSS-Image` |
| Text to video | `MiniMaxAI/MiniMax-H3` |
| Narration | `FunAudioLLM/CosyVoice2-0.5B` |

The preset allows paid models. Automatic dispatch filters by the account catalog and task capability, then tries the recommended models, other eligible SiliconFlow models, and other configured providers. If all candidates are unavailable, the host takes the task. Music and sound effects retain their existing automatic routing. Project bans and “free models only” still apply. Remote patches require a trusted project test command and an isolated worktree. The host runs browser tasks and independent QA.

To override one task type, pick a model under Project Model Selections and apply it. The next claim uses the new selection. To turn the preset off, select general automatic routing (通用自动选模) and click Apply. Project pins, bans and user preferences are preserved.

#### Selection order

1. The host executes independent QA, browser and MCP tool calls, and external actions. Remote code patches require an isolated Git workspace, a specific write scope, and a sandboxed test command supplied by the project.
2. If the project pins a model for the task type, dispatch to it. Next consider a named profile selected by the host through `--profile NAME`, then preference-matched profiles, task-type defaults, and role defaults. A claim reports a conflict if `--model-choice` disagrees with a fixed selection.
3. Without a fixed selection, candidates must have configured provider credentials, support the task, and satisfy project restrictions. Ranking uses complexity, project preferences, catalog descriptions, and known metadata. A short writing task may prioritize speed. A complex design task may prioritize reasoning. The host can also choose a candidate based on the meaning of the task.
4. Both free and paid models are eligible by default. Tell the host “free models only” when it asks about preferences, or save that restriction under Model Management → Dispatch Policy → User Preferences (模型管理 → 派工策略 → 用户偏好). Only models with verifiable free pricing are eligible. This excludes paid models, unknown prices, and dynamic routes whose free status cannot be confirmed. If a pinned model conflicts with this restriction, the claim reports the reason. Project bans are checked both at claim and before calling the model.

#### Execution and fallback

With automatic selection, a normal claim preflights candidates in order. If the first is unavailable, it tries the next. If all fail or credentials are missing, the task goes to the host. If a model explicitly selected by the user fails preflight, the claim returns an error and leaves the task available. Selection and preflight read catalogs and check availability. Model calls occur during execution and are billed under the provider's terms.

When a claim selects a remote model, Pioneers launches its executor in the background. The host can continue dispatching other work. If `executor.provider` is `host`, give the task to the relevant host member.


The executor renews the heartbeat and archives artifacts. The project's run usage view (运行用量) shows the actual model, selection rationale, elapsed time, and usage reported in the receipt. Once a request has been sent, failure or timeout enters recovery for the original task. An accepted media request resumes with the original provider and model, using its receipt to check status or reuse the artifact. If acceptance is uncertain, reconcile it first to prevent duplicate generation and charges.

#### Remote generation timeouts

SiliconFlow and OpenRouter text and code patch requests accept streaming responses. By default, Pioneers waits up to five minutes for the first model data and two minutes between subsequent reads. Early keepalive comments retain the initial wait budget. Text inference has a 15-minute overall limit. Patch workers reserve another two minutes for project tests. A completion must be complete before Pioneers delivers the text or applies a patch.

Image, narration and sound effect requests allow ten minutes per blocking network operation. Music streams allow ten minutes for both initial data and subsequent idle reads. Media workers have a 30-minute overall limit. Video submission allows five minutes. After receiving a job ID, Pioneers waits up to 30 seconds per status query and retries transient network or service errors up to three consecutive times against that ID. A SiliconFlow signed image download can retry the same URL once after a transient connection or service failure. An uncertain paid submission still requires reconciliation.

Set these environment variables before starting Pioneers to override the defaults for new executors. Values are integer seconds from 1 to 86400.

| Environment variable | Default seconds | Applies to |
| --- | ---: | --- |
| `PIONEERS_MODEL_FIRST_RESPONSE_SECONDS` | 300 | First data for text and patches, video submission |
| `PIONEERS_MODEL_STREAM_IDLE_SECONDS` | 120 | Idle reads for text and patches |
| `PIONEERS_MODEL_TOTAL_SECONDS` | 900 | Text and patch inference |
| `PIONEERS_MEDIA_RESPONSE_SECONDS` | 600 | Image, speech and sound effect calls, image download, music stream reads |
| `PIONEERS_MEDIA_TOTAL_SECONDS` | 1800 | Entire media worker |

#### Named profiles and catalogs

To set a default for a task type, specify video parameters, or provide a trusted patch test command, define named profiles in the project's `.pioneers/models.json`. Use the provider ID `siliconflow` or `openrouter` and copy the model ID from the project dashboard.

```json
{
  "profiles": {
    "writer": {"provider": "siliconflow", "model": "Qwen/Qwen3.5-35B-A3B", "mode": "text", "tags": ["short text"]},
    "router-writer": {"provider": "openrouter", "model": "openrouter/free", "mode": "text"},
    "router-film": {"provider": "openrouter", "model": "minimax/hailuo-3", "mode": "video", "duration": 5},
    "illustration": {"provider": "siliconflow", "model": "Tongyi-MAI/Z-Image-Turbo", "mode": "image"},
    "narrator": {"provider": "siliconflow", "model": "FunAudioLLM/CosyVoice2-0.5B", "mode": "speech"},
    "score": {"provider": "openrouter", "model": "google/lyria-3-clip-preview", "mode": "music"},
    "sound": {"provider": "openrouter", "model": "bytedance-seed/seed-audio-1-0", "mode": "sound_effect"},
    "film": {"provider": "siliconflow", "model": "MiniMaxAI/MiniMax-H3", "mode": "video", "duration": 5, "resolution": "480p"},
    "coder": {
      "provider": "siliconflow", "model": "deepseek-ai/DeepSeek-V3.2",
      "mode": "patch", "test_command": ["python3", "-m", "unittest"]
    }
  },
  "defaults": {"roles": {"pm": "writer"}, "types": {"patch": "coder", "video": "film", "host": "host"}}
}
```

When the user supplies a credential file, bind it to the delivery project on the machine that runs Pioneers. The file can contain `SILICONFLOW_API_KEY=...` and `OPENROUTER_API_KEY=...` on separate lines. To configure one provider, include its line only.

```bash
python3 pioneers.py --configure-provider-env "$HOME/env/apikey.env" --workspace PROJECT
```

The dashboard and new dispatch commands read the project's provider credentials from the bound file. Only its path is saved in the local user's configuration. Update the keys in the file or repeat the binding command to switch files. Refresh the catalog in Model Management to verify the result. When upgrading a dashboard started with an older release, restart that service once on the original port with the same project and run records. Use `python3 pioneers.py --clear-provider-env --workspace PROJECT` to remove the binding and resume using process environment variables.

On the dashboard's model management page (模型管理), search the account's full catalog, filter by capability, pin models, ban or unban them, and save selection preferences. Iterations of the same project share these settings. The single-run dashboard also has a read-only link to the project's models page.

To pause a provider for the whole project, click Disable Provider (禁用提供商) on its catalog card or use the corresponding button under Dispatch Policy → Provider Dispatch (派工策略 → 提供商派工). This covers current models and any models the provider adds later. Automatic dispatch tries other eligible providers, then falls back to the host. Claimed model calls are checked again before execution. Restoring the provider makes its saved pins available again and preserves individual model bans.

SiliconFlow's catalog starts with `GET /v1/models` without a type filter. Category queries annotate capabilities. Dispatch checks the particular capability required, such as `sub_type=chat` for text and `sub_type=text-to-video` for text-to-video. If refreshing fails, the dashboard retains the last successful catalog and its timestamp. [Official catalog API](https://api-docs.siliconflow.cn/docs/api/models-get)

OpenRouter's catalog is browsable without credentials. The dashboard combines the complete `GET /models?output_modalities=all` catalog with video parameters from `GET /videos/models` and marks eligible tasks by input, output, and endpoint. Other listings serve purposes such as batch-only processing, image input, or video editing. Text and patches use `POST /chat/completions`, after a preflight check of the account's `GET /models/user` catalog. Text-to-video uses `POST /videos` and `GET /videos/{id}`. Profiles may specify `duration`, `resolution`, and `ratio`. Omitted values use model defaults, and unknown duration is shown as unknown. OpenRouter bans apply to runtime variants of the same model. When an OpenRouter ban exists, it also restricts dynamic routes such as `openrouter/free` and `~` aliases. Usage is attributed to the actual returned model, with the originally selected model retained in the details. [Model catalog](https://openrouter.ai/docs/api/api-reference/models/get-models) · [Video generation](https://openrouter.ai/docs/guides/overview/multimodal/video-generation)

#### Execution requirements by task type

- **Text artifacts** suit specifications, acceptance examples, and other read-only writing tasks. Requests include the instructions, acceptance criteria, selected skills, and upstream artifacts. Choose a provider that may receive these materials.
- **Media plan corrections.** For a poster or a finished video, declare `deliverable_type: image` or `video` and supply the matching generation fields. Before dispatch, a missing generation task, a mismatched type, or generation registered as ordinary development returns the plan to the LD. Retrieve the reasons with `--replan RUN_DIR` and submit a correction for the returned revision. Existing artifacts and attempts remain available. Separate generation, post-production, and QA using dependencies. The live CLI also uses the media plugin. If no model is eligible, the task stays unclaimed. After updating the project model settings, resume with `--continue RUN_DIR`.
- **Text-to-video** tasks set `video_prompt` and leave `tools` and `write_paths` empty. Only the creative prompt goes to the model. SiliconFlow uses `POST /video/submit` and `POST /video/status`. Pioneers saves a submission marker first and then the provider's request ID. After a disconnect, query the original ID. If submission is uncertain, an operator must reconcile it. SiliconFlow video links last about an hour. OpenRouter provides an expiring signed link or an official video content endpoint that requires the local API key. Save any video you need to retain before access expires. [Video API documentation](https://api-docs.siliconflow.cn/docs/api/video-status-post)
- **Patch tasks** send the individually listed regular UTF-8 files in `write_paths` to the model. They require a clean, isolated Git workspace and support up to 24 files totaling 192 KiB. The model can update files or create new ones. The local executor runs the project's configured tests in a temporary copy and commits changes to the task branch only after they pass. macOS uses `sandbox-exec`. Linux requires a working `bwrap`. Test dependencies must be available in the sandbox. Select `host` if tests need network access, desktop tools, or other host capabilities.
- **Image and audio tasks** set a `media` object in the workflow, for example `"media": {"type": "image", "prompt": "Paint a watercolor of a paper boat in a stream"}`. For narration, use `speech` with the script in `prompt` and, optionally, a supported `voice`. For music, use `music` and describe the style, instruments, or scene. For sound effects, use `sound_effect` and describe what the listener should hear. The project can pin a model or select a named profile for each of these four types. SiliconFlow and OpenRouter support image generation and narration. OpenRouter can also dispatch Lyria music and Seed Audio effects. Open or save the generated files from the task page. They reside under `media/` in the run directory.
- **Run usage** is shown by project, model, and task attempt. It records the tokens, image and audio file counts, video counts, and video duration in seconds reported by the provider. A file or video request reused during recovery counts once. Independent acceptance, tools, and external operations continue to run on the host.

### Including release in delivery

For container releases to Alibaba Cloud ECS, planning and acceptance tasks can load `aliyun-ecs-container-deployment`. Make deployment itself a separate engineering task following acceptance, set `"skills": ["aliyun-ecs-container-deployment"]` and `"effect": "publish"`, and schedule independent QA after deployment. Following approval, the host provides the tools and authorization for the target environment.

Add `--require-release` to `--host-start` or `--live` for workflows that require release. Pioneers checks that independent QA covers every implementation task. When task acceptance finishes, the run enters the accepted state and waits for the delivery lead to register the release outcome. The host manages calls to external release platforms and permission to operate them. After the CLI exits, use `--dashboard RUN_DIR` to follow release progress.

```bash
python3 pioneers.py --approve RUN_DIR --decision D9 --reason "Confirm the version and release scope"
python3 pioneers.py --release RUN_DIR --state publishing --activity "Releasing the accepted version"
python3 pioneers.py --release RUN_DIR --state published --activity "Deployment succeeded" --url https://example.com/
```

On failure, report `--state publish_failed --activity "Specific reason for failure"`. Register `publishing` again for a retry. After success, the dashboard shows the accessible deployment URL.

### Calling a runner from the CLI

To connect the local CLI to another executor, pass `--runner /path/to/executable` in live mode. The target project is the working directory for every invocation. The runner reads one JSON object from stdin with these fields.

| Field | Meaning |
| --- | --- |
| `phase` | `plan`, `task`, `review`, or `approval` |
| `role`, `prompt` | Current role ID and full task prompt |
| `workspace`, `sandbox` | Working directory and required role permissions, enforced by the adapter |
| `model` | Selected model. When unspecified, the value is `null` and the execution platform chooses. |
| `output_schema` | JSON Schema for planning or independent acceptance, or `null` for an ordinary task |
| `mcp_servers`, `builtin_tools` | MCP configuration and built-in tool allowlist for the task, enforced by the adapter |

The runner writes JSON Lines to stdout. Use `{"type":"activity","message":"Inspecting the project"}` to update the dashboard. On success, emit `{"type":"result","message":"Deliverable content"}` and exit with code 0. For planning, independent acceptance, and approval judgment, `message` is a JSON string conforming to the corresponding schema. On failure, exit with a nonzero code and write diagnostics to stderr. If CLI judgment fails, the result goes to human confirmation.

The local operator uses the local controls. In cloud mode, the private preview controls viewing access and the dashboard key authorizes actions on that instance. For per-person approval permissions, the host must authenticate individual users and isolate workspaces and external tool permissions. Pioneers manages registered task and release state. The platform's permission policy governs commands initiated independently outside that workflow.

## Executor recovery

After claiming a task, use `--host-exec` to start a local executor, such as a browser test command or an executor adapter. Pioneers launches it in the assigned working directory. A separate parent process renews the lease every 60 seconds without waiting for command output. Each attempt permits one executor. When the command exits or Pioneers receives an interrupt, the framework stops renewing and cleans up the process group it launched. On Windows it cleans up the direct child. The host receives the command's exit code. Keep the adapter in the foreground until the task finishes, and keep child browsers and test services in the same process group. The host remains responsible for reporting completion, failure, or loss.

```bash
python3 pioneers.py --host-exec RUN_DIR --task verify --attempt 1 --exec-command python3 verify_browser.py
```

**Remote models** run automatically after the framework persists the assignment. If the coordinator exits between saving the assignment and starting its executor, query `--host-ready RUN_DIR` on reconnect to resume that attempt. A per-attempt lock prevents repeated queries from duplicating an inference that has already started. The executor renews the 180-second lease throughout inference.

**Native host agents** can spend long periods reasoning or operating a browser. Use the host platform to verify each member is active, then report those observations with your normal queue query, for example `--host-ready RUN_DIR --observed-active fix:1 --observed-active verify:2`. After 180 seconds without a fresh observation, the dashboard shows “Awaiting host verification” and retains the assigned member and workspace. Late heartbeats, checkpoints and delivery use the original attempt. A targeted heartbeat or a progress report also confirms liveness. Reading the queue or dashboard does not renew assignments. Report `--state lost` once the original executor is confirmed stopped, or reconcile existing artifacts before recovery. Use the supervised entry point above for foreground commands.

```bash
python3 pioneers.py --host-heartbeat RUN_DIR --task fix --attempt 1
python3 pioneers.py --host-report RUN_DIR --task fix --attempt 1 --state lost --activity "Executor process exited unexpectedly"
```

If the CLI scheduler exits unexpectedly, use `--continue RUN_DIR` to reopen the same run. After acquiring the scheduling lock, Pioneers inspects unfinished assignments and retains cleared artifacts, pending decisions, and the plan. A second scheduler cannot take over while the first is active.

- Read-only tasks and ordinary writing tasks in isolated Git worktrees can requeue automatically, up to two automatic recoveries per task. Each assignment receives a new `attempt`. Stale reports are rejected. The previous workspace and attempt records remain available.
- Shared-workspace writes, external actions such as publishing, costly tasks, and tasks that reach the automatic recovery limit wait for manual recovery. A pending shared-workspace write continues blocking other writes in the run until reconciled. When failure occurred outside the framework, the original process or external operation might still be running. The operator must check its actual state.
- After confirming the original executor stopped, select the option to rerun after inspection (检查后重跑) in the dashboard's waiting queue, under the dispatch view, or use the CLI. Rerunning a task that needs authorization creates a new authorization request. If an external operation already completed, use `--artifact` to adopt the verified result and send it through the original acceptance and approval flow, avoiding duplicate execution.
- Host tasks already running before an upgrade may lack leases. After verifying the old executor has stopped, reconcile and resume the task with `--recover`.

```bash
python3 pioneers.py --continue RUN_DIR
python3 pioneers.py --recover RUN_DIR --task fix --attempt 1 --reason "The old process exited and the workspace was checked"
python3 pioneers.py --recover RUN_DIR --task send --attempt 1 --artifact evidence.md --reason "Verified the external receipt"
```

### Continuing a member's work and recording operations

After a failure, `--host-ready` supplies a `preferred_member`. A claim without `--member` reuses the original member. Select a different member explicitly if the original is unavailable. The next assignment includes `continuation` with the previous member, workspace, most recent checkpoint, and operation states. The handoff summary in the prompt lists external operations and items needing reconciliation first. Worktree isolation still applies. Inspect uncommitted changes in the old worktree before carrying any forward selectively.

During a task, the host can use the following command to record one JSON event. It validates the current `attempt` and renews the lease. A custom `--runner` can emit the same event format on stdout and receive continuation data in the task request's `task`, `attempt`, and `continuation` fields.

```bash
python3 pioneers.py --host-checkpoint RUN_DIR --task fix --attempt 1 --artifact event.json
```

```jsonl
{"type":"checkpoint","summary":"Located the parser. Next, add a regression case for empty input.","session_id":"executor-session-1"}
{"type":"operation","id":"read-config","kind":"read","status":"completed","summary":"Checked the configuration format"}
{"type":"operation","id":"patch","kind":"workspace_write","status":"started","summary":"Editing the parser"}
```

The operation `kind` can be `read`, `workspace_write`, `external`, or `tool`. Report `started`, `completed`, or `failed` against the same ID. On interruption, operations with unknown outcomes are marked for reconciliation. Record only the summary needed to resume. Omit secrets, raw tool output, and full conversations. The built-in Codex executor keeps the latest work description and identifiable operation summaries from its event stream and uses them to start a new execution. Hosts or adapters that can resume native sessions can use `resume_session_id`. Pioneers returns this reference only after the previous executor is confirmed stopped, the member and workspace are unchanged, and there are no external actions or unresolved writing operations. The browser dashboard does not expose session references.

Reconcile registered external operations against actual receipts. Record outcomes for the operations awaiting reconciliation with `--recover ... --recovery-checks checks.json`. A checks file can contain the following record.

```json
[{"id":"send-1","outcome":"completed","reason":"The external system returned a delivery receipt"}]
```

When an operation is confirmed to have occurred, adopt the evidence with `--artifact`, then follow the original acceptance and approval process. If it did not occur, set `outcome` to `not_executed` and request fresh execution authorization. Pioneers tracks observed or reported actions. Verify the actual state of external systems using their receipts. External operations still require `effect` to be declared in advance.

### Adopting work already merged in Git

In host mode, an ordinary implementation task awaiting assignment or recovery can adopt a commit already merged into the current project branch. Confirm that the original executor has stopped. In the task queue, choose the option to adopt a merged commit (接续合入提交) and provide the SHA, reason, delivery evidence, and outstanding checks. The corresponding CLI command is shown below.

```bash
python3 pioneers.py --host-adopt RUN_DIR --task integration --attempt 2 \
  --commit COMMIT_SHA --artifact EVIDENCE.md --reason "Verified the merged commit and previous executor"
```

Pioneers verifies the current attempt, branch ancestry, cleared dependencies, and write scope, then sends the commit through the original artifact judgment. Once cleared, downstream QA continues from that commit and must report the actual acceptance outcome. Tasks requiring prior authorization or external actions retain their original approval and recovery process.

## Project dashboard and iteration history

Each delivery project reuses one dashboard address. Start the project dashboard in a persistent terminal and use the same project directory for later iterations. The default local address is [http://127.0.0.1:8790/](http://127.0.0.1:8790/). When running several projects, give each a fixed `--port`. In the cloud, expose this port through the private preview described above. Configure `--dashboard-origin` as well to approve actions through the dashboard.

```bash
python3 pioneers.py --project-dashboard --workspace /path/to/project --detach
python3 pioneers.py --project-history --workspace /path/to/project
python3 pioneers.py --host-start WORKFLOW.json --workspace /path/to/project --register-only
```

With `--detach`, the dashboard listens in a separate process. The command prints `dashboard_url` and `pid`, then returns. The dashboard can still accept approvals after the coordinator session exits. To reconnect, run `--host-ready RUN_DIR` on the original directory and claim cleared tasks. Stop the detached dashboard by ending the PID returned at startup. Omit `--detach` to run it in the foreground.

Starting an iteration with `--register-only` returns a `project_run_url` linking to its details on the existing project dashboard. Keep that dashboard running to claim work. Live CLI deliveries are registered automatically too. The home page lists current and past iterations and pending decisions across iterations. Open a pending decision for its approval controls. Open the collaboration flow to inspect handoffs, rework, and progress.

At completion, Pioneers writes an iteration summary to `status.json`. It includes the goal, each role's deliverables, acceptance evidence, reasons for approvals, registered Git revisions, and the release result. The dashboard links to the execution plan and artifacts and offers a Markdown download. Reopening an iteration for rework removes its previous closing summary until the updated one is generated at completion. To inspect just the last delivery summary, read `latest_delivery` from `--project-history`.

Git projects store the index in shared Git metadata so dedicated worktrees can use the same index. Other projects store it in `.pioneers/iterations/` under the project. The index refers to the original run directories. Keep those directories and their artifacts. Startup also discovers and registers older runs for the same project in the default output directory. If old runs used a custom output directory, pass `--output-dir` when starting the project dashboard or querying history.

## Monitoring progress

Live CLI and host runs start a dashboard and print its address by default. Host mode keeps the dashboard in the foreground and claims work from another terminal. Use `--register-only` to register and exit, then use an existing project dashboard, or start a single-run dashboard with `--dashboard RUN_DIR` before claiming. A host claim checks that its dashboard responds. Already claimed tasks can still report after the service stops.

On a local desktop, Pioneers tries to open a browser by default. Use `--no-open-browser` to disable this behavior. In the cloud, the host provides the private preview URL. The dashboard refreshes every second and shows current role activity, tasks that can run in parallel, and outstanding dependencies and approvals. Restart the dashboard after completion to inspect its records. Offline mode shows the progress of the handoff rehearsal.

Replace the example path with the `Run output` directory printed at startup.

```bash
python3 pioneers.py --watch /path/to/run-dir
python3 pioneers.py --status /path/to/run-dir
python3 pioneers.py --dashboard /path/to/run-dir
```

`--watch` shows progress continuously until the run ends. `--status` takes one snapshot. `--dashboard` groups handoffs by the member who claimed the work. Record the assignee with `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name "Display name"`. Use the same ID for that member throughout the run. A retry can use a different member while preserving the assignee on the previous attempt. Unclaimed tasks appear as unassigned. Historical tasks without recorded members and CLI tasks executed by role remain in role columns.

Approvals appear on the handoff lines they control. A separate arrow and rework record show when QA returns a task. Every attempt retains evidence links. Dashboard approvals can include the approver's name. The CLI also supports `--approve ... --approver "Display name"` for a self-reported operator name. The run directory's `status.json` stores the latest task state, the 100 most recent actions, and earlier evidence records.
