"""Triage lanes: grouping incident statuses by what they ask a person to do.

The status field answers "where is this in the pipeline"; a lane answers "is it my problem right
now". They are not the same question — `new` and `failed` sit at opposite ends of the pipeline and
mean exactly the same thing to an on-call engineer: nobody has looked at this yet.

Kept in the domain so the list filter, the counts and the UI tabs can't drift into three different
opinions about which statuses belong together.
"""

from __future__ import annotations

#: lane -> the statuses it covers, in the order a person works through them.
LANES: dict[str, tuple[str, ...]] = {
    "triage": ("new", "failed"),
    "working": ("analyzing", "analyzed"),
    "ticketed": ("ticketed",),
    "done": ("resolved",),
}

__all__ = ["LANES", "statuses_for"]


def statuses_for(lane: str | None) -> tuple[str, ...] | None:
    """The statuses a lane covers; None for "no lane filter" — never an empty tuple, which would
    silently mean "match nothing" (same trap as ProjectScope.names)."""
    if lane is None:
        return None
    try:
        return LANES[lane]
    except KeyError as exc:
        raise ValueError(f"unknown lane: {lane!r}") from exc
