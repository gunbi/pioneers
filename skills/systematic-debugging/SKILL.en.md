---
name: systematic-debugging
description: When a build fails, behavior is unexpected or QA returns a task, reproduce the problem, test the root cause and demonstrate that the fix works.
---

# Systematic debugging

[Chinese edition](SKILL.md)

1. Record the observed behavior, expected behavior, reproduction command or steps, environment and relevant versions. Start with the smallest reproducing input. For intermittent problems, record the triggering conditions and how many observations you made.
2. Compare passing and failing paths to find the first boundary where the result diverges from expectations. Inspect the related diff, configuration and dependencies. When needed, use logs or assertions to check inputs, outputs and state on each side of the boundary.
3. Test one hypothesis with an observable outcome at a time. Change only the conditions relevant to that hypothesis. If evidence is still insufficient, hand off the possibilities already ruled out and the next most informative verification step.
4. An executor with write access makes the smallest repair that addresses the cause. Use the original reproduction steps to show the difference before and after the fix, then rerun affected paths. For a reliably reproducible defect, follow the test engineering practice to add or update regression coverage.
5. Hand off the reproduction steps, root cause evidence, change location, retest result and conditions still unverified. A read-only executor hands off evidence and a repair target for the assigned engineer to change.

## Failures across system layers

For failures across instances or data centers, unusual latency or issues that are hard to reproduce locally, first compare affected and healthy instances. Put errors, latency and retries from the caller, service entry point and dependency on one timeline. Trace which part of the actual path owns each connection, queue, thread or shared resource. Choose a controlled comparison, such as single versus concurrent requests or same-domain versus cross-domain calls, and change one condition. Record mitigation results, established causes and hypotheses still to test separately.

## Sources

- Move from reproduction and boundary evidence to testing a single hypothesis. [Superpowers · Systematic Debugging](https://github.com/obra/superpowers/blob/main/skills/systematic-debugging/SKILL.md) ([MIT](https://github.com/obra/superpowers/blob/main/LICENSE))
