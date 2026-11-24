---
name: acceptance-testing
description: Design risk-based coverage from the acceptance criteria, reproduce the behavior independently, and provide evidence for each criterion.
---

# Acceptance and regression testing

[Chinese edition](SKILL.md)

1. Read the acceptance criteria, current attempt, implementation handoff and relevant changes. Start with the main user journey and the regressions most likely to be affected. Record the starting data, actions, expected result and actual result for each check.
2. Choose project tests, simulated interactions or real-device checks as appropriate. Record the environment, commands and evidence another reviewer can inspect. If the evidence only establishes static behavior, mark live interaction as awaiting verification.
3. In the structured report, quote each acceptance criterion verbatim in `checks` and mark it `pass`, `fail` or `unverified`. Return `accepted` only when all checks pass. For an implementation defect, return `changes_requested` and name the task to repair. Use `needs_clarification` for ambiguous requirements and `blocked` when required evidence is missing. On retest, rerun the previously failing steps first, then check the other affected criteria.

## Sources

- Design coverage around risk and trace results back to evidence. [BMad · Test Engineering Architect](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise)
- Execute scenarios and verify the test against the observed result. [GitHub Awesome Copilot · Playwright Generate Test](https://github.com/github/awesome-copilot/blob/main/skills/playwright-generate-test/SKILL.md)
