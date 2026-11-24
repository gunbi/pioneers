---
name: code-review
description: Review a patch, commit or deliverable against the actual diff, requirements contract and test evidence. Give findings the recipient can act on.
---

# Code review

[Chinese edition](SKILL.md)

1. Check the task goal, acceptance criteria and interface contract. Use the Git tools available to the role to inspect the actual diff and the callers it affects.
2. Check behavior, regressions, edge cases and error handling first. Then check interface compatibility, state ownership, module dependencies and maintainability. Keep the review focused on the change and its actual impact.
3. For connection pools, external clients, asynchronous work, error codes or logging, check who creates and closes resources, whether exceptional paths release them and whether instances can be reused. Check that logs or other instrumentation distinguish policy decisions, graceful degradation and actual failures, and that frequent normal operations do not raise misleading alerts.
4. Verify test coverage for critical success and failure scenarios. Record commands and results for tests you can run. Mark checks with insufficient evidence as unverified.
5. For each finding, give the file location, trigger, impact and evidence. Separate blocking findings from suggestions for improvement. Mark each acceptance criterion as passed, failed or unverified and identify its evidence. Include an accessible link to the change or report at handoff.
