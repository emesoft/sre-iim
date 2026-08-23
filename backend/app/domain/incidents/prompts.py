"""The Step 0 analysis brain's prompt + context rendering, relocated into the domain (decision 0015).

`SYSTEM_PROMPT` and `build_user_message()` are preserved verbatim from the original
`backend/ai/analyze_incident.py` — the anti-hallucination rules and the compact "only print fields
that are present" rendering are the heart of the analysis and must not be rewritten. They are pure
(no I/O), so they belong in the domain and are shared by the analyzer (M2) and, later, the diagnosis
and critic agents (M3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.documents.entities import RetrievedChunk

# Appended to SYSTEM_PROMPT when retrieved knowledge is supplied (SPEC 6.2). Shared by any
# RAG-grounded analyzer.
RETRIEVED_KNOWLEDGE_RULES = """

RETRIEVED KNOWLEDGE RULES:
- The user message may include a "Retrieved knowledge" section with excerpts from runbooks, past
  postmortems, architecture docs, and vendor docs.
- Treat these as reference context, not ground truth about THIS incident. Still conclude only from the
  incident data provided.
- When a conclusion is supported by a retrieved excerpt, cite it by its [source_type: title] tag.
- If retrieved knowledge conflicts with the incident data, trust the incident data and say so.
- Never invent an excerpt or a citation that is not present in the Retrieved knowledge section."""


# The core of Step 0: anti-hallucination rules + a strict JSON output schema.
SYSTEM_PROMPT = """You are a senior SRE triaging a production incident. \
You receive automatically-collected context and must analyze it quickly and accurately so on-call can act immediately. \
The context may be an infrastructure incident (tasks dying, 5xx...) OR non-infrastructure (third-party API cost over budget, queue backlog...). \
Not every incident has every kind of data — work with what is provided.

MANDATORY RULES:
- Base conclusions ONLY on the provided data. Do NOT invent metrics, service names, or events not present in the context.
- If the data is insufficient to determine the cause, say so explicitly and point out what else to check.
- When reasoning about root cause, connect timestamps (when the anomaly started vs when a deploy/change happened) and cite concrete evidence from the context.
- Write concisely, in the voice of an engineer working the incident. No rambling.

Return ONLY a single valid JSON object, with NO explanation or markdown, following this schema:
{
  "severity": "critical" | "warning" | "info",
  "summary": "1-2 sentences: what is happening and the impact",
  "root_cause": "root-cause reasoning with evidence and timestamps; if uncertain, state the most likely hypothesis with a confidence level",
  "recommended_action": "concrete action(s) to take now, prioritizing stopping the damage first. If there is more than one distinct step, put EACH step on its own line, numbered '1. ', '2. ', etc. A single obvious action can stay as one plain sentence.",
  "confidence": "high" | "medium" | "low"
}"""


def build_retrieval_query(ctx: dict) -> str:
    """Build a concise retrieval query from the incident's key signals (pure).

    Used to embed and search the knowledge base. A focused query (service + error signature + alert +
    deploy) embeds better than the full context dump. The M3 triage agent will refine this later.
    """
    parts: list[str] = []
    if ctx.get("service"):
        parts.append(f"service {ctx['service']}")
    if ctx.get("alert"):
        parts.append(str(ctx["alert"]))
    logs = ctx.get("sample_logs")
    if logs:
        parts.append(str(logs[0].get("message", "")))
    ecs = ctx.get("ecs")
    if ecs and ecs.get("stopped_reason"):
        parts.append(str(ecs["stopped_reason"]))
    dep = ctx.get("recent_deploy")
    if dep and dep.get("version"):
        parts.append(f"deploy {dep['version']}")
    return " | ".join(p for p in parts if p)


def build_user_message(ctx: dict, evidence: "list[RetrievedChunk] | None" = None) -> str:
    """Assemble the context into a COMPACT LLM input. Only prints sections that have
    data → handles both infra and non-infra incidents, and saves tokens. When `evidence` is
    supplied, a "Retrieved knowledge" section is appended for RAG grounding + citation."""
    lines = [f"Service: {ctx.get('service')}"]
    if ctx.get("cluster"):
        lines[0] += f"  |  Cluster: {ctx.get('cluster')}"
    if ctx.get("started_at"):
        lines.append(f"Started at: {ctx.get('started_at')}")

    # Alert description (if the alert carries one — e.g. a cost-threshold violation)
    if ctx.get("alert"):
        lines.append(f"\n[ALERT] {ctx['alert']}")

    ecs = ctx.get("ecs")
    if ecs:
        lines.append(
            f"\n[ECS] running/desired={ecs.get('running')}/{ecs.get('desired')}, "
            f"stopped_reason={ecs.get('stopped_reason')}, restart_5m={ecs.get('restarts_5m')}, "
            f"task_memory={ecs.get('task_memory')}"
        )

    alb = ctx.get("alb")
    if alb:
        lines.append(
            f"[ALB] healthy/total={alb.get('healthy')}/{alb.get('total')}, "
            f"5xx_rate={alb.get('error_rate')}, p95_latency={alb.get('p95_latency')}, "
            f"req_per_min={alb.get('rpm')}"
        )

    dep = ctx.get("recent_deploy")
    if dep:
        lines.append(
            f"[RECENT CHANGE] {dep.get('service')} -> {dep.get('version')} "
            f"by {dep.get('by')}, {dep.get('relative_time')}"
        )

    metrics = ctx.get("metrics")
    if metrics:
        lines.append("[METRICS] " + ", ".join(f"{k}={v}" for k, v in metrics.items()))

    logs = ctx.get("sample_logs")
    if logs:
        lines.append("\n[SAMPLE LOGS] (filtered, with repeat counts)")
        for lg in logs:
            rep = f" x{lg['count']} times" if lg.get("count") else ""
            lines.append(f"  {lg.get('ts')} {lg.get('level')} {lg.get('message')}{rep}")

    if ctx.get("runbook"):
        lines.append(f"\n[RUNBOOK] {ctx['runbook']}")

    if evidence:
        lines.append("\n[RETRIEVED KNOWLEDGE] (reference only — cite as [source_type: title])")
        for chunk in evidence:
            lines.append(f"[{chunk.source_type}: {chunk.title}]\n{chunk.content}")

    return "\n".join(lines)
