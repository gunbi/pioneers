---
name: change-integration
description: Use when parallel changes converge or modules need integration testing. Establish the target and integration order, then verify the integrated version.
---

# Integrate changes

[Chinese edition](SKILL.md)

1. Identify the integration owner, target project workspace, target branch or baseline, and the commit or file scope of each change. Record uncommitted changes and existing test failures. Preserve each executor's original artifacts.
2. Merge ready changes in dependency order. Align shared contracts and configuration first, then integrate implementations. Use the workflow's integration directory or branch. Perform Git operations within task permissions and follow Pioneers authorization for approvals and remote operations.
3. When resolving a conflict, check the intent, interfaces and tests on both sides. If contributors disagree on the behavior contract, record the disagreement and ask the responsible owners to confirm it.
4. Run tests, builds and critical-path smoke checks for affected modules on the specific merged version. For changes across clients or services, prioritize actual interfaces, configuration, error handling and compatibility. If integration fails, record the failing version, reproduction command and source of the blocker.
5. Hand off the integration branch or commit, included changes, conflict resolutions, verification commands and results. If the baseline or candidate version changes, retest the affected scope.

## Sources

- Confirm the baseline and verify the merged result. [Superpowers · Finishing a Development Branch](https://github.com/obra/superpowers/blob/main/skills/finishing-a-development-branch/SKILL.md) ([MIT](https://github.com/obra/superpowers/blob/main/LICENSE))
