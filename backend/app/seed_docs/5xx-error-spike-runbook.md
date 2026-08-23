---
title: 5xx Error Rate Spike Runbook
source_type: runbook
tags: alb, ecs, 5xx, errors, deploy
---
# 5xx Error Rate Spike Runbook

## Symptoms
An ALB target group's HTTPCode_Target_5XX_Count alarm fires, or the error-rate SLO alert triggers
for a service.

## Common causes
1. A recent deploy introduced a bug (check deploy time vs anomaly start time first — this is the
   single most common cause of a sudden 5xx spike).
2. A downstream dependency (database, third-party API, another internal service) is down or
   timing out, and the service isn't handling that failure gracefully.
3. Unhealthy targets being routed to before they've fully started (missing/misconfigured health
   check grace period).
4. Connection pool exhaustion causing request timeouts under load.

## Steps to resolve
1. Compare the alarm's start time against `recent_deploy` in the incident context — if they're
   within a few minutes of each other, the deploy is the prime suspect.
2. Check the target group's healthy-host count — if it dropped around the same time, look at task
   startup/health-check logs for the failing tasks.
3. Sample the actual 5xx response bodies/logs to distinguish "our bug" (stack trace, 500) from
   "downstream failure" (502/504, timeout) from "client behavior" (malformed requests).
4. If a deploy is the cause, roll back to the previous task definition/image immediately — restore
   service first, root-cause after.
5. If a downstream dependency is the cause, check its own health/status page and add a circuit
   breaker or timeout if the service doesn't already have one.

## Escalation
If the error rate doesn't recover after a rollback, or the cause isn't a recent deploy, escalate to
the on-call engineer for the affected service with the sampled error bodies and healthy-host graph.
