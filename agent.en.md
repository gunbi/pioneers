# Pioneers team coordination guide

[Chinese edition](agent.md) · [English documentation index](docs/english.md)

When a user asks you to deliver a project with this repository, register the workflow and track task state in Pioneers. The host platform executes tasks already assigned by Pioneers. When handing work to another team, share the workflow, artifact paths and acceptance evidence.

Once a target project has adopted Pioneers, use it for subsequent requests both in the current session and in later sessions. Register a new run after the previous iteration has finished. The first real run records this convention in the target project's `AGENTS.md`. To bind an existing project, run `python3 pioneers.py --bind-project --workspace PROJECT`.

## Assemble the team

| Role ID | Responsibility |
| --- | --- |
| `pm` | Product lead. Clarifies the user goal, first-release scope and acceptance criteria, and resolves ambiguous requirements. |
| `ld` | Delivery lead. Defines assignments, dependencies and shared contracts, and identifies who owns final integration. |
| `engineer` | Delivery engineer. Owns a user journey or a clearly scoped task, designs and implements it, runs self-tests and submits evidence. |
| `qa` | Independent QA. Checks the code, observed behavior and test evidence against the acceptance criteria. |

When the user asks only to assemble a team, provide the roster, responsibilities and execution method. When the user also provides a project task, activate the roles needed for that task.

## Assign tools and skills by role

Read `roles` in the [capability manifest](capabilities.json) for the tools and skills available to each role. Several roles can share a capability. Delivery engineers choose web, API or client capabilities according to their task.

- In the workflow, `tools` lists the tools selected for the task and `skills` lists skills to provide in advance. A role may also read an authorized `SKILL.md` on demand.
- For work spanning clients or modules, use the architecture design skill to establish responsibilities, interfaces and verification methods, then hand the contract to the developers. Use the engineering design skill to separate state from side effects during implementation and refactoring. Use the code review skill for independent inspection of actual changes.
- Select additional skills as needed. Use `systematic-debugging` for a build failure, unexpected behavior or a QA return. Use `change-integration` to bring parallel implementations together or test across modules. Use `test-engineering` to cover critical behavior or turn a defect into a regression test. Independent acceptance still requires evidence for each criterion.
- Select `chrome-extensions` for Chrome extension development and independent verification. Consult its bundled references for the Manifest V3 capabilities involved.
- For desktop development, choose `desktop-framework-electron`, `tauri-app-builder`, `macos-spm-app-packaging` or `winui-dev-workflow` according to the target stack. Use `winui-ui-testing` for native Windows UI verification when appropriate. For packaging, signing and migration, select the relevant reference or skill.
- When a terminal is available, use `--run-tool` to run checks granted to the role. Preserve the returned results as handoff evidence. The host platform manages its native tools and permissions. [User manual](docs/usage.en.md#role-capabilities)
- To connect Git, a remote repository or a browser through MCP, obtain the role configuration with `--mcp-config ROLE --workspace PATH`. The executor enforces the tool allowlist and authentication. Assign Git writes to development roles. Remote repository operations through this interface are read-only.

## Start the run and dispatch work

Identify a separate working directory for the deliverable. For an existing product, use its repository root or a dedicated Git worktree. For a new product, create it under a writable directory authorized by the platform, for example a `todo-app/` directory alongside Pioneers, and initialize it with `git init`. Pass `--workspace PROJECT` explicitly. You can run coordination commands from the Pioneers checkout. Run development, tests and previews in the delivery project directory.

If the user has already given a provider credential file path, run `python3 pioneers.py --configure-provider-env FILE --workspace PROJECT` with that path. If they have already chosen to skip setup for this project, run `python3 pioneers.py --skip-provider-setup --workspace PROJECT` and dispatch to the host. The machine running Pioneers must be able to read the file. Ask for its path only. Keep API key values out of chat.

When the first `--host-start` result contains `provider_setup.action` set to `ask`, ask `provider_setup.question` in the user's language alongside the dashboard URL before dispatch. Bind the supplied file. If the path fails, ask for a correction or offer to skip. When the user skips provider setup, record that choice and continue with the host.

Check both `--configure-provider-env` and `--host-start` results for `model_preference_setup`. When its `action` is `ask`, ask its `question` in the user's language before dispatch. If the user already supplied a preference with the file path, use their words directly. Save the answer with `python3 pioneers.py --set-model-preference "free models only" --policy-revision N --workspace PROJECT`, replacing the sample text with the user's preference and `N` with the prompt's `revision`. For an explicit “use the default” or “skip preferences,” pass an empty string (`""`). The project default tries SiliconFlow first and allows both free and paid models. If the revision is stale, check the latest policy on the dashboard and confirm any change with the user. Reuse an existing answer across iterations. The user can change it under Model Management → Dispatch Policy → User Preferences.

When `model_preference_setup.presets` includes “SiliconFlow first, quality first,” present it as a choice. If the user picks it, run `python3 pioneers.py --set-model-preset siliconflow-quality --policy-revision N --workspace PROJECT`. Dispatch will try the recommended models for each task type and may use paid models. Save any additional constraint, such as “free models only,” as a project preference, using the new revision returned by the preceding write.

Reuse one project dashboard. First query `--project-history --workspace PROJECT` for previous runs and the last delivered summary. When planning, the LD checks current code, decisions, acceptance results and outstanding issues. If the project dashboard is down, start it independently with `--project-dashboard --workspace PROJECT --detach`. Keep the returned URL and PID. The default port is 8790. Reuse a previously chosen port or private cloud preview. Retain the original run directory when recovering or intervening in an existing iteration.

Start the executor in the `workspace` returned by the claim and apply the permissions indicated by `sandbox`. If the host cannot set the working directory, report the blocker and pause dispatch.

Pioneers refreshes all registered open-source skill sources when starting a new run, continuing with `--continue RUN_DIR`, or reopening it from the host with `--dashboard RUN_DIR`. After a host restart, wait for the dashboard service to recover before claiming new work.

### Choose an iteration workflow

Define the smallest deliverable for the iteration. When the write scope and acceptance criteria are clear and the change is low risk and easy to roll back, plan two tasks by default. Reuse existing tasks and completed verification when replanning.

1. An `engineer` confirms the criteria, implements the change, runs self-tests, performs any necessary integration, and delivers a specific version with evidence in a single task.
2. A `qa` task depends directly on the implementation, sets `review: true`, and independently checks the main user journey, relevant regressions and coverage gaps on the delivered version.

For architecture, shared contracts, security or permissions, data changes that are hard to reverse, or critical user journeys, first schedule read-only design work. Describe the impact, verification and rollback. Mark the design as `architecture` or `feature_design` as appropriate and make implementation depend on its approval. Follow with independent QA. Use the existing `costly_change` and `effect` approvals for expensive implementation and external actions respectively. If the scope or risk is unclear, establish the impact and acceptance boundaries first.

### Register and execute the workflow

1. The LD plans the work according to the [workflow schema](workflow.schema.json), or generates it with the CLI's `--goal`. Minimize the time to a deliverable that passes its acceptance criteria. Schedule decisions and shared contracts that unblock several tasks first. In `depends_on`, list only the artifacts required to start. Run independently editable, testable and deliverable tasks in parallel as soon as possible. Once a contract is settled, the frontend can work with a mock while QA prepares its checks. When several people change one project, specify ownership, workspace isolation and the integration owner. Give every implementation task a downstream independent QA task and mark actual acceptance work with `review: true`.
   - Give the iteration a short single-line `title` for the run dashboard. Keep the full requirement, scope and constraints in `goal`.
   - Select `change-impact-preflight` for shared contracts, defaults or staged rollouts, legacy data or concurrent writes, shared resources, permissions and cross-module merges. Select `verifiable-delivery-evidence` when implementation or independent QA needs to bind evidence to the actual delivered version. For a localized fix with targeted verification, see the [single-module fix example](examples/small-fix.json).
   - Schedule read-only design for a new page, a full redesign or a change to a critical journey that needs a significant interaction decision. Assign `product-experience-design` to the designer and, where needed, to QA. Deliver inspectable designs covering layout, navigation and icons, important states, interactions and narrow-screen behavior. Implementation tasks list their design task IDs in both `ui_design` and `depends_on` and wait for design approval. Independent QA depends directly on both design and implementation. For a well-scoped local fix, an engineer can document the intended behavior before implementing and self-testing.
   - Designers and developers can use the [existing project UI redesign example](examples/existing-project-redesign.json). When interviews, feedback or behavioral data need synthesis, the PM may select `customer-journey-mapping`.
2. When a runner is available, start `pioneers.py ... --live` and let the CLI dispatch. For host-native agents, reuse the project dashboard and register a new iteration with `--host-start WORKFLOW --workspace PROJECT --register-only`. The JSON includes `run_dir`, `project_dashboard_url` and `project_run_url`. If the project dashboard is unavailable, `--dashboard RUN_DIR` starts a single-run dashboard. Restore a stopped dashboard before claiming tasks.
3. The host coordinator uses `--host-ready RUN_DIR` to inspect `ready`, `active` and `waiting`. When members have capacity, claim independent tasks in the same batch and prioritize longer dependency chains. Claim with `--host-claim RUN_DIR --task TASK_ID --member MEMBER_ID --member-name DISPLAY_NAME`. If conflicting writes block a claim, check the queue again after the related handoff. Give the executor the returned prompt, role, working directory, branch, complexity and reporting instructions. Recheck the queue after artifact review, approval, recovery or a return for changes. Hosts without concurrency execute tasks in queue order.
   - Read `model_candidates` on ready items returned by `--host-ready`. Select a model according to the task, complexity and `model_policy.preference`. Free and paid models are eligible by default. A “free only” preference allows only models whose price can be confirmed as zero. Honor models specified by the project and applicable configurations. For other tasks, claim directly so the framework preflights eligible SiliconFlow models first under the project default, then other providers, or use `--model-choice PROVIDER/MODEL_ID` on the claim to select a candidate for that assignment. To run locally at the user's request use `--profile host`. Use `--profile NAME` for a named project configuration.
   - Give independent QA, browser work, MCP tools and external operations to the host. Remote patches require an isolated workspace, a precise file scope and a project test command. Use `video_prompt` for video. Set `media.type` and `media.prompt` for images, narration, music or sound effects. Check the returned `executor.provider`. Run remote assignments with `--host-exec RUN_DIR --task ID --attempt N` and give host tasks to the relevant member. Resume a submitted media request using its original assignment and receipt. [Model selection and recovery](docs/usage.en.md#model-providers-by-task)
4. The coordinator or executor uses `--host-report` for milestones, blockers and completion. Declare precise `write_paths`. Treat unknown scope as the entire project. In a clean Git project with an existing commit, Pioneers allocates workspaces and branches automatically. Engineers commit locally on the returned branch and then report. Store test evidence in the run directory or in delivery files outside the project. Use `depends_on` for tasks that need to pick up code. Schedule downstream integration and independent QA for parallel fixes. When claimed, the integration workspace merges handed-off commits from its dependencies. Give the user the integration branch, workspace and verification results.
5. QA reports evidence for each criterion using the [review schema](review.schema.json). Return implementation defects with `changes_requested` and `repair_task`. Pioneers requeues the implementation task and affected downstream tasks, and preserves earlier evidence. Use the `attempt` from the new claim when reporting fixes and verification. Route ambiguous requirements to the PM and missing environments, dependencies or evidence to the LD.
   - For UI fidelity, compare the approved design with real browser screenshots of the candidate using the same data and viewport. Check layout, visual hierarchy, icon semantics and the user's path through the interface. Record acceptable font-rendering or small pixel differences. Return critical deviations to implementation, or get design approval before changing the baseline. On acceptance, include the design, actual screenshots, viewport and scenario in `visual_evidence`. If a browser or screenshots are unavailable, report `blocked` and mark the affected criteria `unverified`.
6. When the goal requires release, start the run with `--require-release`. After acceptance, the LD uses `--release` to record the start, outcome and real access URL. The dashboard displays “Released” only after successful release.
7. Mark serious architectural decisions and important feature designs with the corresponding `impact`. Produce a read-only proposal before handing work downstream. Mark costly implementation as `costly_change`. Model sending externally, publishing, making payments, changing permissions, deleting and similar operations as separate `effect` tasks. Once prerequisites are ready, a person authorizes the scope of execution.
8. After every role submits an artifact, give the `judgment` in its report to a read-only LLM and record the result with `--judge`. Respect dependency blocks until this review and any human confirmation are complete. Only actual user actions can trigger `--approve`, `--revise`, `--pause`, `--intervene` or `--resume`. On a pause, stop new claims and collect reports from tasks already started.
9. Treat `replan` returned by `--host-ready` as pending LD work. When `replan.ready` is true, use `--replan RUN_DIR` to obtain the user's instruction, current plan and state. Give the returned prompt to a read-only LD. Submit the LD's JSON with `--replan RUN_DIR --revision N --artifact PLAN.json`, then query the queue again and dispatch updated tasks.
10. When the run is unfinished but nothing can execute, tell the user what the dashboard is waiting for. In the same coordinator session, run `--host-ready RUN_DIR --wait 25`. Continue waiting after `timed_out`. Recheck the queue after finishing a stage. When ready, claim tasks, process artifacts awaiting judgment, replan, or start release within the approved scope if `release_ready` is true. After a user pauses new assignments, wait for “Resume claiming new tasks” on the dashboard. After the host session closes, reconnect to the same run directory.

Use the claim's `attempt` for subsequent heartbeats, reporting and redispatch. Start command-based executors with `--host-exec RUN_DIR --task TASK_ID --attempt N --exec-command COMMAND ...`. The framework renews the lease while the process lives and cleans it up on exit. Keep the command in the foreground and put run records where the framework process can write. For native sessions that cannot be wrapped as a local command, the host supplies independent liveness checks and renewal. If it has observed the executor is still active, it can renew every 60 seconds with `--host-heartbeat RUN_DIR --task TASK_ID --attempt N`. An actual progress report also renews the lease. If an executor exits unexpectedly, report `--host-report ... --state lost` and check `--host-ready` for recovered work. If the task is awaiting manual recovery, show the original executor, task version and evidence so the operator can verify them and retry or continue the artifact. Re-running an external operation requires fresh approval. After the CLI scheduler exits, reconnect with `--continue RUN_DIR`.

When a member needs to resume a task, prefer the `preferred_member` returned by `--host-ready`. Pass `continuation` to the executor with the assignment. Use `--host-checkpoint` to save a stage summary and operation states. Record the start and verifiable outcome of external operations. When the previous executor is stopped and a `resume_session_id` is available, the host can use its native session-resume capability. Otherwise start a new execution from the checkpoint and operation records within the current workspace and approval scope. If the result of an external operation is unclear, the operator verifies the receipt and registers recovery checks to avoid repeating it.

If the project branch already contains a merged implementation for a task awaiting dispatch or recovery, verify the commit, evidence, outstanding checks and previous executor state. The user can register it from the dashboard, or authorize `--host-adopt`. Continue artifact review and outstanding downstream QA. Bind the acceptance report to the commit actually verified.

See the [README](README.en.md) for usage examples and progress entry points.

## Report progress to the user

When a host member uses an authorized skill, include `--attempt N --used-skill SKILL_ID` in their next `--host-report`. Repeat the flag for multiple skills. Pioneers records skills included in the execution prompt automatically and displays member-reported use separately.

Before the first assignment, verify that the project dashboard and the iteration's `dashboard-data.json` endpoint respond, then show the initial assignments. Provide a reachable project URL when available. The dispatch view shows who is executing, what can run in parallel and which tasks are waiting for dependencies or approval. On a local desktop, try to open the browser by default.

If a cloud host can deploy and view a private live site, use its tools to expose the dashboard. Prefer an existing private entry point or a private HTTPS port preview connected to the running service. Verify both the page and the iteration's `dashboard-data.json` through the remote entry point before sharing the URL. For dashboard approvals, configure forwarding and the control credential as described in [Cloud dashboard controls](docs/usage.en.md#cloud-dashboard-controls).

If the host lacks deployment or viewing capabilities, skip cloud deployment. If deployment is skipped or the remote entry point cannot be verified, continue dispatch and report progress in the conversation from the run records using role, task, status and current activity.

- If the user needs to act through a cloud dashboard, the operator starts it from a private terminal as described in the README and obtains the control credential there. Give the user the preview URL after verification. Only the operator should hold the credential.
- Update each role's state when work starts, is handed off, becomes blocked or finishes. Briefly report the current action or evidence produced.
- Report tangible progress during longer tasks, for example, “The frontend has connected the form and is now checking save and refresh.” Ask independent sessions to report these milestones.
- Report the status actually returned by the executor. Until the host receives a new message from a role, show its last confirmed activity or the reason it is waiting.

After delivery, open the iteration summary on the project dashboard and verify task reports and acceptance evidence. Hand over the summary URL with known outstanding issues or work for the next iteration. Pioneers generates the summary from registered artifacts and decisions.

## Local CLI entry point

If the user chooses the local CLI, create the team configuration from the Pioneers checkout with Python 3.9 or newer.

```bash
python3 pioneers.py --create-team --workspace /path/to/project
```

The tool writes the team configuration to `.pioneers/team.json` in the target Git project. The returned JSON includes `status`, `manifest` and `team.roles`. To set a default model at creation, add `--model MODEL_ID`. See the [README](README.en.md) for how to run the team.

The local CLI can also plan from a goal.

```bash
python3 pioneers.py --goal "Build a web page where users can create to-do items" \
  --live --runner /path/to/agent-runner --workspace /path/to/project
```

The terminal shows role activity during execution. In another terminal you can also run `python3 pioneers.py --watch RUN_DIR`, using the `Run output` path printed at startup.
