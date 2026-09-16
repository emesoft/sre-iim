"""Collect the facts an analyst would look up, before the LLM is asked anything.

An alarm notification says almost nothing: "CPUReservation is in ALARM, 5 missing datapoints".
Without more, the best an analyzer can honestly produce is a hypothesis plus a list of things for a
human to go and check — which is what it was producing, and which is barely worth the call.

So this fetches what that human would: the alarm's own definition (threshold, dimensions, the
reason CloudWatch itself gave), the real metric datapoints around the alarm, and for an ECS service
the desired/running task counts and why the last tasks stopped. The prompt already has slots for
all of it (`ecs`, `metrics` in domain/incidents/prompts.py) — the analyzer needed evidence, not a
new template.

Two rules hold throughout:

* **Nothing here may break analysis.** Credentials expire, an SSO session lapses, a role is missing
  one permission. Every lookup is individually guarded and a failure just means that section is
  absent — an analysis with less evidence, never no analysis.
* **Dimensions come from the alarm, never from its name.** `ECS-CPUReservation-ecs-evp-datalink-qa`
  looks parseable and isn't: the cluster and service in it are a naming convention, not data.
  `describe_alarms` returns the real dimensions, so the ECS lookup asks about the resource the
  alarm actually watches.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.domain.integrations.entities import Integration
from app.domain.integrations.ports import IntegrationRepository
from app.infrastructure.cloud.credential_resolver import CredentialResolver
from app.infrastructure.security.encryptor import Encryptor

logger = logging.getLogger(__name__)

_QUOTED = re.compile(r"'([^']+)'")
#: How far back to pull datapoints. An hour at 5-minute resolution is enough to show whether the
#: metric was flat, climbing, or simply absent, without turning the prompt into a spreadsheet.
_WINDOW = timedelta(hours=1)
_PERIOD_SECONDS = 300
_MAX_STOPPED_TASKS = 3
#: How far back to look for deployments. Wide enough that a rollout which took a while to surface
#: as an alert is still in frame, narrow enough that "nothing deployed" stays a meaningful answer.
_DEPLOY_WINDOW = timedelta(hours=2)


@dataclass
class AwsLookups:
    """The three read-only questions worth asking about an alarm, over one boto3 session.

    Shared by the pre-analysis enricher and the incident-chat MCP tools so both reach AWS the same
    way. They were going to diverge otherwise: the chat had no AWS access at all and answered by
    handing the reader a list of `aws` commands to run themselves, which is the same "summary, not
    investigation" gap the enricher was built to close.
    """

    session: object
    region: str | None = None

    def alarm(self, alarm_name: str) -> dict | None:
        """The alarm's own definition and the reason CloudWatch gave for its current state."""
        found = _first(
            self._cloudwatch().describe_alarms(AlarmNames=[alarm_name]).get("MetricAlarms", [])
        )
        if found is None:
            return None
        return {
            **_alarm_facts(found),
            **_metric_identity(found),
            "dimensions": {d["Name"]: d["Value"] for d in found.get("Dimensions", [])},
        }

    def metrics(
        self, namespace: str, metric_name: str, dimensions: dict, minutes: int = 60
    ) -> dict:
        return _metric_datapoints(
            self._cloudwatch(),
            {"Namespace": namespace, "MetricName": metric_name},
            dimensions,
            window=timedelta(minutes=minutes),
        )

    def deployments(self, since: datetime, max_clusters: int = 3, max_services: int = 12) -> dict:
        """What has been deployed in this account lately, and when.

        Answers the question almost every analysis ends on — "did something change?" — for incidents
        that have no CloudWatch alarm to look up, which is every New Relic one. Both answers are
        useful: a rollout minutes before the alert points straight at it, and *no* deployment in the
        window rules out a whole class of cause that would otherwise stay open as a hypothesis.

        Bounded on purpose. This runs on the analysis path, so it walks a few clusters rather than
        enumerating a large estate — a slow enrichment would delay every triage to answer a question
        that is usually settled by the most recent handful of services.
        """
        ecs = self.session.client("ecs", region_name=self.region)
        clusters = (ecs.list_clusters(maxResults=max_clusters).get("clusterArns") or [])
        recent: list[dict] = []
        checked = 0
        for cluster in clusters:
            arns = (
                ecs.list_services(cluster=cluster, maxResults=max_services).get("serviceArns") or []
            )
            if not arns:
                continue
            for described in ecs.describe_services(cluster=cluster, services=arns).get(
                "services", []
            ):
                checked += 1
                for deployment in described.get("deployments") or []:
                    created = deployment.get("createdAt")
                    if not created or created < since:
                        continue
                    recent.append(
                        {
                            "service": described.get("serviceName"),
                            "cluster": cluster.rsplit("/", 1)[-1],
                            "task_definition": (deployment.get("taskDefinition") or "").rsplit(
                                "/", 1
                            )[-1],
                            "started_at": _iso(created),
                            "rollout": deployment.get("rolloutState"),
                        }
                    )
        recent.sort(key=lambda d: d["started_at"] or "", reverse=True)
        # The count is reported so "none" reads as "we looked at 14 services and none had deployed",
        # not as "we didn't check".
        return {"services_checked": checked, "since": _iso(since), "deploys": recent[:8]}

    def ecs(self, cluster: str, service: str) -> dict:
        return _ecs_state(self.session.client("ecs", region_name=self.region), cluster, service)

    def _cloudwatch(self):
        return self.session.client("cloudwatch", region_name=self.region)


@dataclass
class AwsContextEnricher:
    """Fills in `ecs` / `metrics` / `alarm` context from the project's own AWS integration."""

    integrations: IntegrationRepository
    encryptor: Encryptor

    async def enrich(self, service: str | None, context: dict) -> dict:
        if not service:
            return {}
        # By provider, not by capability: a project whose alarms come from New Relic can still
        # have an AWS account attached, and that account is what these lookups need.
        integration = await self.integrations.for_provider(service, "aws")
        if integration is None:
            return {}
        # An alarm name is no longer required to get here. It used to be, which meant every New
        # Relic incident — the ones with no CloudWatch alarm to look up at all — was enriched with
        # nothing and analysed on its notification text alone. Deploy history needs no alarm.
        alarm_name = _alarm_name(context)
        try:
            return await asyncio.to_thread(self._collect, integration, alarm_name)
        except Exception as exc:  # noqa: BLE001 - evidence is a bonus; analysis must still run
            logger.warning("AWS enrichment failed for %s/%s: %s", service, alarm_name, exc)
            return {}

    # --- everything below runs in a worker thread (boto3 is blocking) --------------------------

    def _collect(self, integration: Integration, alarm_name: str | None) -> dict:
        lookups = AwsLookups(
            session=CredentialResolver(self.encryptor).resolve(integration),
            region=(integration.config or {}).get("region"),
        )
        out: dict = {}

        # First, and independent of any alarm: "did something change?" is where nearly every
        # analysis ends up, and it is answerable for every incident rather than only the ones
        # carrying a CloudWatch alarm name. A *negative* answer is worth as much as a positive one —
        # no deployment in the window closes off a whole class of cause that otherwise stays open.
        deploys = _guarded(
            "deployments",
            lambda: lookups.deployments(datetime.now(timezone.utc) - _DEPLOY_WINDOW),
        )
        if deploys:
            out["deployments"] = deploys

        if not alarm_name:
            return out
        alarm = lookups.alarm(alarm_name)
        if alarm is None:
            return out

        dimensions = alarm.pop("dimensions", {})
        namespace, metric_name = alarm.get("namespace"), alarm.get("metric_name")
        out["alarm"] = alarm  # assigned, not reassigned — `out` already carries the deploy history

        if namespace and metric_name:
            datapoints = _guarded(
                "metric datapoints",
                lambda: lookups.metrics(namespace, metric_name, dimensions),
            )
            if datapoints:
                # Merged into the existing `metrics` dict rather than a new key: the prompt already
                # renders that section, and the namespace/metric name recorded at ingest belong
                # with the numbers they describe.
                out["metrics"] = {"namespace": namespace, "metric_name": metric_name, **datapoints}

        cluster, ecs_service = dimensions.get("ClusterName"), dimensions.get("ServiceName")
        if cluster and ecs_service:
            ecs = _guarded("ECS state", lambda: lookups.ecs(cluster, ecs_service))
            if ecs:
                out["ecs"] = ecs
        return out


def _alarm_name(context: dict) -> str | None:
    """The quoted alarm name in the alert text — the same one build_headline reads."""
    alert = context.get("alert")
    if not alert:
        return None
    match = _QUOTED.search(str(alert))
    return match.group(1) if match else None


def _first(items: list) -> dict | None:
    return items[0] if items else None


def _guarded(what: str, call):
    """Run one lookup, letting a missing permission cost only that section."""
    try:
        return call()
    except Exception as exc:  # noqa: BLE001
        logger.info("enrichment skipped %s: %s", what, exc)
        return None


def _alarm_facts(alarm: dict) -> dict:
    """What CloudWatch itself says — including its own `StateReason`, which is usually the single
    most informative sentence available and was being thrown away."""
    return {
        "state": alarm.get("StateValue"),
        "reason": alarm.get("StateReason"),
        "since": _iso(alarm.get("StateUpdatedTimestamp")),
        "threshold": f"{alarm.get('ComparisonOperator')} {alarm.get('Threshold')}",
        "treat_missing_data": alarm.get("TreatMissingData"),
    }


def _metric_identity(alarm: dict) -> dict:
    return {"namespace": alarm.get("Namespace"), "metric_name": alarm.get("MetricName")}


def _metric_datapoints(
    cloudwatch, alarm: dict, dimensions: dict, window: timedelta = _WINDOW
) -> dict:
    """The actual numbers behind the alarm.

    This is what turns "CPU alarm fired" into "CPU averaged 11% and never exceeded 14%" — or, as
    often, into "no datapoints at all", which means something entirely different and is invisible
    from the notification alone.
    """
    now = datetime.now(timezone.utc)
    response = cloudwatch.get_metric_statistics(
        Namespace=alarm["Namespace"],
        MetricName=alarm["MetricName"],
        Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
        StartTime=now - window,
        EndTime=now,
        Period=_PERIOD_SECONDS,
        Statistics=["Average", "Maximum"],
    )
    points = sorted(response.get("Datapoints", []), key=lambda d: d["Timestamp"])
    if not points:
        # Stated explicitly rather than left as a missing key: "we looked and there was nothing"
        # is evidence, and it's the difference between a dead service and a healthy one.
        return {"datapoints": 0, "note": "no datapoints reported in this window"}
    return {
        "datapoints": len(points),
        "avg": round(sum(p["Average"] for p in points) / len(points), 2),
        "max": round(max(p["Maximum"] for p in points), 2),
        "latest": round(points[-1]["Average"], 2),
        "unit": points[-1].get("Unit"),
    }


def _ecs_state(ecs, cluster: str, service: str) -> dict:
    """Desired vs running, and why the last tasks stopped.

    `running=0` against `desired=2` settles in one line what the analyzer could otherwise only
    guess at, and the stop reason separates the two cases that matter: a deliberate scale-to-zero
    reads completely differently from a crash loop.
    """
    described = _first(
        ecs.describe_services(cluster=cluster, services=[service]).get("services", [])
    )
    if described is None:
        return {}
    state: dict = {
        "cluster": cluster,
        "service": service,
        "desired": described.get("desiredCount"),
        "running": described.get("runningCount"),
        "pending": described.get("pendingCount"),
    }

    stopped = ecs.list_tasks(cluster=cluster, serviceName=service, desiredStatus="STOPPED").get(
        "taskArns", []
    )[:_MAX_STOPPED_TASKS]
    if stopped:
        tasks = ecs.describe_tasks(cluster=cluster, tasks=stopped).get("tasks", [])
        reasons = [t.get("stoppedReason") for t in tasks if t.get("stoppedReason")]
        if reasons:
            state["stopped_reason"] = reasons[0]
            state["restarts_5m"] = _recent_stops(tasks)
    # The service's own event log names scaling actions and placement failures in plain words.
    events = [e.get("message") for e in (described.get("events") or [])[:2] if e.get("message")]
    if events:
        state["recent_events"] = events
    return state


def _recent_stops(tasks: list[dict]) -> int:
    """How many of the sampled tasks stopped in the last five minutes — a crude but honest crash
    -loop signal, and the prompt already has a slot for it."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=5)
    return sum(1 for t in tasks if (t.get("stoppedAt") or cutoff) > cutoff)


def _iso(value) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


__all__ = ["AwsContextEnricher", "AwsLookups"]
