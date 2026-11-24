---
name: existing-ui-audit
description: Before redesigning an existing web interface or comparing old and new versions during acceptance, inspect representative user journeys for hierarchy, responsiveness, accessibility and feedback, and record reproducible findings.
---

# Audit an existing interface

[Chinese edition](SKILL.md)

1. Fix the version, entry point, test data and viewport for the audit. Start with critical user tasks affected by the redesign. Follow each from its real entry point to the result that tells the user they have finished.
2. On critical screens, check whether users can find the entry point, understand the current state and decide what to do next. Inspect content hierarchy, labels, primary actions, back navigation and recovery. Record findings along the journey, distinguishing functional failures, usability obstacles and visual suggestions.
3. Independently check applicable technical conditions, including narrow-screen reflow, zoom, keyboard focus, control names, visible feedback and observable loading issues. Record what you actually tested. If only static inspection was possible or the environment was limited, list the items that still need browser verification.
4. For each finding, give its severity, page, steps, observed result, affected task and a screenshot or other reviewable evidence. After the redesign, use the same data, viewport and steps to retest the relevant findings and behavior that must be preserved.

## Sources

- Review tasks, hierarchy and interaction. [Impeccable · Critique, Apache 2.0](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/critique.md)
- Check accessibility, responsiveness and technical behavior. [Impeccable · Audit, Apache 2.0](https://github.com/pbakaus/impeccable/blob/main/plugin/skills/impeccable/reference/audit.md)
