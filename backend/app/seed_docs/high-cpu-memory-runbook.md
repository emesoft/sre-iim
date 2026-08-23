---
title: High CPU / High Memory Runbook
source_type: runbook
tags: cpu, memory, ecs, rds, oom
---
# High CPU / High Memory Runbook

## Symptoms
An ECS service or RDS instance's CPUUtilization or MemoryUtilization alarm fires above 85% for
5+ minutes, or ECS tasks are being OOM-killed and restarted repeatedly.

## Common causes
1. A missing database index causing sequential scans on a large table.
2. A memory leak in the application (heap grows monotonically until OOM-kill).
3. A batch job, migration, or cron task running during business hours and competing for resources.
4. A recent deploy introduced a regression (check deploy time against the anomaly's start time).
5. Traffic spike beyond what the current task count/instance size can handle.

## Steps to resolve
1. Check `pg_stat_activity` (RDS) or the container's memory graph (ECS) to confirm which resource
   is actually exhausted and since when.
2. Correlate the anomaly's start time with the most recent deploy — if they line up, roll back
   first and investigate after service is restored.
3. For CPU: check for a missing index via `EXPLAIN ANALYZE` on the slowest queries.
4. For memory: check for an OOM-killed container's logs right before the kill for a stack trace
   or a steadily growing memory graph (leak signature) vs a sudden spike (single bad request).
5. If a batch job is the cause, pause it and reschedule outside business hours.
6. If it's genuine traffic growth, scale out (more tasks/instances) before scaling up (bigger
   instance size) — horizontal scaling recovers faster and costs less to reverse.

## Escalation
If the resource stays above 90% for 15+ minutes and none of the above resolves it, escalate to
the service owner or DBA on-call and consider a temporary instance-size increase while the root
cause is investigated.
