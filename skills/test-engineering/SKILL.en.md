---
name: test-engineering
description: Choose the test level for a new behavior, defect or high-risk change, and write or review repeatable regression tests.
---

# Test engineering

[Chinese edition](SKILL.md)

1. Extract observable, assertable behavior from the acceptance criteria and actual changes. Rank it by impact and likelihood. Protect core paths, boundaries, failure recovery and previously observed regressions first.
2. Use the project's existing test framework and select the smallest test level that reliably catches the risk. Use unit tests for rules and boundaries, contract or integration tests for behavior between modules and external dependencies, and end-to-end tests for a few critical user journeys.
3. For a reproducible defect, write a test that exposes it before the fix. Confirm that it fails for the targeted behavior, then run the same test after the fix. Control data and timing and assert results through public behavior.
4. Run the new or affected tests. Record commands, expectations, results and remaining risks. Delivery engineers leave maintainable tests in the project. Read-only QA reviews coverage, reliability of reproduction and failure signals, and hands missing tests back to engineering.
5. Deliver test locations, corresponding behavior or risk, reproduction commands and results. Independent acceptance still requires evidence for each acceptance criterion.

## Sources

- Validate a regression assertion by observing it fail before the fix. [Superpowers · Test-Driven Development](https://github.com/obra/superpowers/blob/main/skills/test-driven-development/SKILL.md) ([MIT](https://github.com/obra/superpowers/blob/main/LICENSE))
- Determine coverage based on risk and test level. [BMad · Test Engineering Architect](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise/blob/main/README.md) ([MIT](https://github.com/bmad-code-org/bmad-method-test-architecture-enterprise/blob/main/LICENSE))
