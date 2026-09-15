"""Incident fingerprint — the cache key, relocated into the domain (decision 0015).

Preserved verbatim from the Step 0 brain: `service | normalized-error-signature | deploy-version`.
The signature strips digits and hex ids so repeats of the same error collapse to one key, and a
different deploy version yields a different fingerprint — forcing re-analysis so a stale cache cannot
misdiagnose a post-deploy incident. Pure function; no I/O.
"""

from __future__ import annotations

import hashlib
import re

from app.domain.incidents.log_lines import first_message


def fingerprint(ctx: dict) -> str:
    """Cache key: service + error signature + current change version.

    Never raises on a malformed context. It runs inside the ingest request, so anything it throws
    is a 500 on `POST /api/incidents` that names no field — which is what a `sample_logs` list of
    plain strings used to produce.
    """
    raw = first_message(ctx)
    if raw is None:
        raw = ctx.get("alert", "")
    if not isinstance(raw, str):
        raw = str(raw)
    error_sig = re.sub(r"[0-9a-f]{8,}|\d+", "", raw).strip()
    deploy = ctx.get("recent_deploy")
    version = deploy.get("version", "") if isinstance(deploy, dict) else ""
    if not isinstance(version, str):
        version = str(version)
    key = f"{ctx.get('service')}|{error_sig}|{version}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
