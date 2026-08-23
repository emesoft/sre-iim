---
title: Deploy Rollback Runbook
source_type: runbook
tags: deploy, rollback, incident-response
---
# Deploy Rollback Runbook

## When to roll back
Roll back first, investigate after, whenever an incident's anomaly start time lines up with a
recent deploy — restoring service takes priority over root-causing while customers are affected.
Don't wait for a full root-cause analysis before rolling back a suspect deploy.

## Steps
1. Identify the last known-good version (the task definition/image tag/commit deployed right
   before the anomaly started).
2. Roll back the service to that version through the normal deploy pipeline — don't hand-edit
   running infrastructure, since that leaves the next real deploy in an inconsistent state.
3. Confirm the metric that triggered the alarm (error rate, CPU, latency, etc.) actually recovers
   after the rollback — a rollback that doesn't fix the symptom means the deploy wasn't the cause,
   and the search for the real cause continues.
4. Once confirmed stable, notify the team that owns the rolled-back change so they can fix forward
   before re-attempting the deploy.

## After the rollback
Write up what broke and why once the incident is resolved — this becomes a postmortem document in
the knowledge base, so the same class of regression is caught by review or tests next time.
