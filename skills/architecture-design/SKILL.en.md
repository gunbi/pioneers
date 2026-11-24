---
name: architecture-design
description: Use when a change spans modules, needs an interface contract or involves tradeoffs in performance, availability or future evolution.
---

# Architecture design

[Chinese edition](SKILL.md)

1. Identify the user journey, current constraints and the source of complexity. Consider performance, availability, extensibility, security, scale and cost as the project requires. Record existing evidence and flag assumptions that still need testing.
2. Start with the simplest design that meets the constraints. For cross-module or complex work, compare viable alternatives and explain their benefits, added complexity, cost and implementation difficulty. Record why you chose the design and which changes would warrant a fresh evaluation.
3. Define module responsibilities, dependency direction, state ownership and persistence boundaries. For work across clients or services, specify request fields, successful responses, error behavior and compatibility requirements. For retries or data synchronization, agree on behavior for duplicate requests and stale reads. Give each contributor a shared contract for implementations, mocks and tests.
4. Match the deliverable to the complexity. For a small change, document boundaries, interfaces and verification. For a complex task, include alternatives, critical flows, failure scenarios and conditions for future changes. For questions about performance, consistency or availability, consult [Performance, storage and availability](references/performance-storage-reliability.en.md).
5. When the task requests a structured deliverable, provide `architecture.json` or equivalent structured content. Include at least `summary`, `modules` (each entry has `name` and `responsibility`) and `interfaces` (each entry has `name` and `contract`, and the array may be empty when there are no cross-module interfaces). If the file is accessible, run `architecture_check` to validate its structure. Use project tests to verify actual behavior.
