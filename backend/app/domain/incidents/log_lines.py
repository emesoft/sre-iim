"""Log lines, in whatever shape the sender actually used.

The canonical `sample_logs` entry is `{ts, level, message}` — what `tests/samples/` and the pollers
produce. But the field arrives straight off an HTTP body, and the obvious thing for a person holding
a CloudWatch dump to send is a list of plain strings. Three places called `.get()` on the entries
unguarded, and each failed differently: `fingerprint()` runs inside `POST /api/incidents`, so the
whole ingest answered **500** without naming the field; both prompt builders run in the background
analysis task, where the same `AttributeError` surfaces only as an incident stuck in `failed`.

Normalizing once, here, is what stops the three from drifting apart about the shape again. Anything
unreadable degrades to `[]` rather than raising — a malformed log field is not worth failing an
incident over, and the alert text is still there to fingerprint and analyse on.
"""

from __future__ import annotations

#: Rendered and fingerprinted; anything else a caller sends along is dropped rather than carried
#: into a prompt, where unknown keys are just tokens nobody reads.
_KEYS = ("ts", "level", "message", "count")


def log_lines(ctx: dict) -> list[dict]:
    """`ctx["sample_logs"]` as a list of dicts, whatever it arrived as.

    A bare string is split on newlines, because that is what pasting a log dump into one field
    produces and one 40-line blob is not one log line.
    """
    raw = ctx.get("sample_logs")
    if isinstance(raw, str):
        raw = [line for line in raw.splitlines() if line.strip()]
    if not isinstance(raw, (list, tuple)):
        return []
    return [_line(entry) for entry in raw]


def _line(entry: object) -> dict:
    """One entry, normalized.

    A dict keeps exactly the keys it had — `message` absent stays absent, because `fingerprint()`
    reads it as `""` and existing recurrence chains hash on that value.
    """
    if isinstance(entry, dict):
        line = {k: entry[k] for k in _KEYS if entry.get(k) is not None}
        if "message" in line and not isinstance(line["message"], str):
            line["message"] = str(line["message"])
        return line
    return {"message": entry if isinstance(entry, str) else str(entry)}


def first_message(ctx: dict) -> str | None:
    """The first log line's text, or None when there are no lines to read.

    None and `""` are different answers: no logs at all means fall back to the alert text, while a
    first line with no `message` fingerprints as empty — which is what it did before this module
    existed, so the chains built on it still match.
    """
    lines = log_lines(ctx)
    if not lines:
        return None
    return lines[0].get("message", "")
