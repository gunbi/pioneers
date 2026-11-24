---
name: verifiable-delivery-evidence
description: Tie conclusions to a specific commit or build and provide reproducible evidence when handing off an implementation, independently accepting it, or verifying migrations, traffic shifts, releases and performance changes.
---

# Delivery evidence

[Chinese edition](SKILL.md)

1. Before starting, identify the acceptance target, environment or sample, comparison baseline and pass conditions. For an ordinary fix, use precise reproduction steps and expected behavior. For staged delivery, verify platform status, approvals, actual deployment and traffic or data processing at each stage. Provide evidence within task scope.
2. Verify the commit, build or release batch actually delivered. Record the version, environment, command or action, expected and observed results, and unverified scope. Rerun affected checks after integration or a change of delivery version. Redact credentials from commands and evidence.
3. For high-risk changes such as migrations, traffic shifts or capacity changes, first test a representative sample, then read back results through an independent path. According to data risk, compare totals, key dimensions, aggregates or representative records. Record time windows, tool versions, samples and truncation boundaries. For an ordinary fix, QA can independently rerun the same command against the delivered version. Add a second verification path when a shared source could give both checks the same misleading result.
4. When a counterexample appears, preserve the original measurement definition and observed result. Check the version, precision, sampling, pagination and truncation, and investigate whether there is a real defect. If the definition must change, record the reason and keep both sets of results.
5. Deliver evidence per acceptance criterion and identify verified items, unverified items and outstanding issues. Engineers submit their own test results, and QA retests according to the project's acceptance rules. Send conclusions involving commitments to other teams or external parties to the delivery lead for consolidation. Follow Pioneers authorization for external actions.
