# Performance, storage and availability

[Chinese edition](performance-storage-reliability.md)

Consult the sections relevant to the current design and add the conclusions to the design record. Label measurements, assumptions to verify and tradeoffs separately.

## Performance

1. Define targets and workload, including peak request volume, tail latency, data volume and the read/write ratio.
2. Inspect the critical path and bottlenecks on a single instance first, including indexes, connection management, concurrency and external dependencies. Use the same workload to compare results before and after the change.
3. When scaling is necessary, identify the bottleneck that caching, clustering or load balancing addresses and the additional resource costs and failure scenarios it introduces. Record the metrics that would trigger an upgrade.

## Storage and consistency

1. Choose storage based on access patterns, transaction boundaries, query requirements and recovery constraints. Identify the authoritative data owner.
2. For caching or separate read and write paths, document the invalidation strategy and when callers can read their own writes. Specify allowed behavior during cache failures, replication lag and network partitions.
3. For retries, duplicate requests or concurrent writes, specify idempotency or conflict-handling rules and design scenarios that verify them.

## Availability and recovery

1. Perform failure mode and effects analysis (FMEA) for critical operations. Record possible failures, the extent of their impact, detection, recovery and verification.
2. Choose timeout, isolation, graceful degradation, replication or failover strategies according to impact. Identify signals that trigger degradation, failover or rollback.
3. Provide reproducible drills and acceptance criteria for recovery. For designs such as active-active deployment across regions, explain the availability requirements, data consistency tradeoffs and operating costs.
