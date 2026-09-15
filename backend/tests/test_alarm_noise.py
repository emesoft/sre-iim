"""Unit tests for which alarms are mechanism rather than incident — no DB, no AWS.

The risk here is entirely one-sided. Failing to suppress noise costs a cluttered queue; suppressing
something real costs a missed outage, silently. So the pattern is deliberately narrow, and these
tests exist mostly to pin what must *not* match.
"""

from app.domain.integrations.noise import is_scaling_mechanism

_UUID = "b90a64fc-e73f-47a9-89b2-89aacf4fe5c1"


def test_a_target_tracking_scale_in_alarm_is_mechanism():
    """The one that started this: permanently in ALARM for any comfortably idle service, and its
    correct resolution is "yes, autoscaling works"."""
    name = f"TargetTracking-service/ecs-easyrx-prod-cluster/ecs-easyrx-prod-svc-AlarmLow-{_UUID}"
    assert is_scaling_mechanism(name)


def test_the_scale_out_half_of_the_pair_is_too():
    """AlarmHigh means the policy is adding capacity — the loop working, not a failure. Suppressing
    one half and not the other would leave the queue half-noisy and the rule half-explainable."""
    assert is_scaling_mechanism(f"TargetTracking-service/cluster/svc-AlarmHigh-{_UUID}")


def test_a_hand_named_alarm_is_never_suppressed():
    """The trailing UUID is what marks a name as machine-generated. Without that requirement this
    rule would start deciding things about names people chose, which is not its business."""
    assert not is_scaling_mechanism("TargetTracking-checkout-cpu")
    assert not is_scaling_mechanism("TargetTracking-service/cluster/svc-AlarmLow")


def test_real_alarms_from_this_account_still_come_through():
    """Sampled from the live database — the regression that matters is one of these disappearing."""
    for name in (
        "ECS-CPUReservation-ecs-evp-datalink-qa",
        "AWS/RDS-aurora-evp-datalink-pg-qa-writer-freeable-memory-low",
        "cloudtrail-unauthorized_api_calls",
        "rds-mysql-freeable-memory-prod",
        "medusa-LowECSCpuAlarm",
    ):
        assert not is_scaling_mechanism(name), name


def test_a_name_that_merely_mentions_scaling_is_not_enough():
    assert not is_scaling_mechanism("autoscaling-failed-to-launch-instances")
    assert not is_scaling_mechanism(f"MyTargetTracking-svc-AlarmLow-{_UUID}")


def test_a_missing_name_is_not_suppressed():
    assert not is_scaling_mechanism(None)
    assert not is_scaling_mechanism("")
