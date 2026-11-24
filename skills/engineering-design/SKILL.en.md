---
name: engineering-design
description: Use when implementing or refactoring code to assign responsibilities for business rules, state, side effects and execution control.
---

# Software design

[Chinese edition](SKILL.md)

1. Write the behavior contract first, including inputs, outputs, boundary conditions and failure feedback. Express caller requirements using the project's types, interfaces or explicit validation.
2. Separate rules from execution control. Rules determine which conditions hold and how to calculate results. Scheduling determines when to call them, in what order and with which retry policy. Express independently testable checks and transformations as deterministic functions.
3. Assign ownership of state. Have its owning object or module maintain transitions and invariants. Keep file, network and storage effects behind clear boundaries.
4. Consider interfaces, composition or delegation for reuse when multiple implementations share a behavior contract. Place logging and timing at call boundaries. Match abstractions to the variations the project actually needs to support.
5. Add verification in proportion to risk. Exercise rules and boundaries in focused tests, and verify actual dependencies and failure paths in integration tests. Deliver reproducible commands and their results.
