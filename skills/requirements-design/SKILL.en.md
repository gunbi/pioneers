---
name: requirements-design
description: Turn a feature request into scope, user scenarios and reproducible acceptance criteria shared by implementation and QA.
---

# Requirements and acceptance design

[Chinese edition](SKILL.md)

1. Read the user's goal and relevant handoffs. Describe the users in scope, the trigger and the outcome that should change. Distinguish current behavior, confirmed requirements and assumptions to verify.
2. Define what belongs in this iteration and what is deferred. For complex tasks, add affected paths, dependencies and constraints that need a decision. If ambiguity would change scope or an acceptance decision, identify the affected items and ask a specific question.
3. Write acceptance criteria for the main path, critical failure scenarios and recovery behavior. Give each one a starting state, action and observable outcome. Give engineers and QA the same version of the criteria, word for word.

For example, a list has three incomplete items. Select two and complete them in bulk. The selected items become complete and the third keeps its previous state. Add separate criteria for undo and for restoring state after refresh when those behaviors need verification.

## Sources

- Define scope and testable requirements. [Product on Purpose · Deliver PRD](https://github.com/product-on-purpose/pm-skills/blob/main/skills/deliver-prd/SKILL.md)
- Describe the main journey and observable acceptance scenarios. [Agent Skills · Deliver Acceptance Criteria](https://github.com/yuusakuri/agent-skills/blob/main/skills/deliver-acceptance-criteria/SKILL.md)
