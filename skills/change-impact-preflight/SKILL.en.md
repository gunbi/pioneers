---
name: change-impact-preflight
description: Before changing shared contracts, defaults or staged rollouts, existing data or concurrent writes, shared resources, permissions or cross-module integration, establish the impact, verification method and rollback conditions.
---

# Assess change impact before implementation

[Chinese edition](SKILL.md)

1. Confirm the target workspace, branch, baseline commit and scope. Inspect uncommitted changes and existing callers. Record the smallest useful verification command and distinguish known baseline failures from new failures. Preserve the starting state.
2. Compare interface fields, error codes, defaults and data semantics with the previous version. Identify the affected versions and callers using call sites, contracts, configuration or run records. For a feature flag, record its default, the identity under which it is enabled and the rollout scope. Use a repeatable comparison to verify behavior while it is off.
3. Check legacy formats, which data requires backfill, version coexistence, dual-write failures and reconciliation according to the actual risk. For shared resources, permissions or routes, identify the resource owner, the actual execution identity at each stage and the impact of failure. Write verification cases for the relevant combinations of old and new readers and writers.
4. Document the rollback action, its trigger and the resulting data state. If compensation, reconciliation or replay is needed, explain the procedure and verification results. Record whether rollback has been rehearsed.
5. Summarize the baseline, affected parties, verification commands or evidence, rollback conditions and remaining risks. The delivery lead coordinates risks involving shared contracts, legacy data, parallel writes, shared resources, permissions or rollback. QA verifies independently. Use the small-change workflow only for a localized change without those risks that can be retested directly.
