"""Which alarms are mechanism, not incident.

Some alarms exist to make a control loop work rather than to tell anyone something is wrong. AWS
Application Auto Scaling creates a pair per scaling policy — `TargetTracking-<resource>-AlarmHigh-
<uuid>` and `-AlarmLow-<uuid>` — and the low one sits in ALARM for as long as the service is
comfortably under its target. That is the normal, healthy, desired state of an idle service, and it
was filling the triage queue with incidents whose correct resolution is "yes, autoscaling works".

Filtering them is a judgement about *AWS's own generated names*, not the user's: the pattern below
matches what the console creates, never something a person typed. That's what makes it safe to
apply by default — a hand-named alarm is never silently dropped, whatever it watches.

What this deliberately does **not** hide: a service that cannot scale, or one stuck at maximum
capacity. Those show up as ECS service events, task placement failures, or the account's own
capacity alarms — different signals, which still come through.
"""

from __future__ import annotations

import re

#: CloudWatch's generated name for an Application Auto Scaling target-tracking alarm. The trailing
#: UUID is what marks it as machine-generated; requiring it is why `TargetTracking-checkout-cpu`,
#: if a person ever named an alarm that, still reaches the queue.
_TARGET_TRACKING = re.compile(
    r"^TargetTracking-.+-Alarm(High|Low)-"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

__all__ = ["is_scaling_mechanism"]


def is_scaling_mechanism(alarm_name: str | None) -> bool:
    """True for an alarm that reports how autoscaling is working, not that something is wrong."""
    return bool(alarm_name and _TARGET_TRACKING.match(alarm_name.strip()))
