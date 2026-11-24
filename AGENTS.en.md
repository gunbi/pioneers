# Pioneers

[Chinese edition](AGENTS.md)

When the user selects this repository to assemble or run an agent team, read the [team coordination guide](agent.en.md) first. Follow its workflow for the user's goal.

Projects already using Pioneers keep using it for subsequent development and fixes. Start a new run for each iteration after a completed run. Reuse the project dashboard and read the previous iteration's summary before planning. Members with active assignments should report against their existing claims. These rules are recorded in the target project's `AGENTS.md` for future sessions.

For a delivery request, create a Pioneers run and register the workflow. Each role works only on tasks dispatched by Pioneers. When a runner is available, the Pioneers CLI dispatches the tasks. A host-native agent can execute a claimed task. If Pioneers cannot start, report the specific blocker before dispatching or implementing anything.

Start the dashboard by default for real runs. Before the first assignment, verify the page and the status endpoint as described in the [team coordination guide](agent.en.md), then give the user a reachable address. Open it if you can control a browser visible to the user. In the cloud, use the host's private preview URL. If the environment cannot expose a reachable entry point, explain why and report progress in the conversation using the run records.

While a run is paused, awaiting human approval, or awaiting a replan requested from the dashboard, follow the [team coordination guide](agent.en.md) to wait for and handle its state. At the end of a stage, check for pending work, approvals and planning requests before ending delivery.

When adding or updating an open-source skill reference, credit the source in the local `SKILL.md` and register the upstream GitHub repository and Markdown path in `skills.<id>.sources` in the [capability manifest](capabilities.json). At the start or restart of a real run, refresh every registered source before dispatch. Each role loads its authorized skills as needed for its task. When offline or when upstream is unavailable, use the available cache and local content.
