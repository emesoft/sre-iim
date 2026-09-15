"""`sample_logs` arrives off an HTTP body, so every shape someone can send has to be survivable.

A list of plain strings — what pasting a CloudWatch dump produces — used to raise `AttributeError`
in three places. In `fingerprint()` that is a 500 on `POST /api/incidents` naming no field; in the
two prompt builders it is an incident stuck in `failed`, because they run in the background
analysis task where nothing surfaces the traceback.

No database: these are pure domain functions.
"""

from __future__ import annotations

import pytest

from app.domain.incidents.fingerprint import fingerprint
from app.domain.incidents.log_lines import first_message, log_lines
from app.domain.incidents.prompts import build_retrieval_query, build_user_message

CANONICAL = {
    "service": "gcm",
    "sample_logs": [
        {"ts": "14:02:09", "level": "FATAL", "message": "java.lang.OutOfMemoryError: Java heap"},
        {"ts": "14:02:10", "level": "ERROR", "message": "Container killed - exit 137"},
    ],
    "recent_deploy": {"version": "v1.4.2"},
}


# --- the shapes that used to crash -----------------------------------------------------------

MALFORMED = [
    pytest.param({"service": "gcm", "sample_logs": ["plain string line"]}, id="list-of-strings"),
    pytest.param({"service": "gcm", "sample_logs": "one\ntwo\n"}, id="bare-multiline-string"),
    pytest.param({"service": "gcm", "sample_logs": [None, 42]}, id="list-of-non-strings"),
    pytest.param({"service": "gcm", "sample_logs": {"a": 1}}, id="dict-not-list"),
    pytest.param({"service": "gcm", "sample_logs": [{"message": {"nested": 1}}]}, id="dict-message"),
    pytest.param({"service": "gcm", "recent_deploy": "v1"}, id="deploy-not-a-dict"),
    pytest.param({"service": "gcm", "alert": {"text": "x"}}, id="alert-not-a-string"),
]


@pytest.mark.parametrize("ctx", MALFORMED)
def test_fingerprint_survives_every_shape(ctx: dict) -> None:
    assert isinstance(fingerprint(ctx), str)


@pytest.mark.parametrize("ctx", MALFORMED)
def test_prompt_builders_survive_every_shape(ctx: dict) -> None:
    """Both run in the background task, where a raise means a silently `failed` incident."""
    assert isinstance(build_retrieval_query(ctx), str)
    assert isinstance(build_user_message(ctx), str)


# --- and the canonical shape must hash to exactly what it always did -------------------------


def test_canonical_fingerprint_is_unchanged() -> None:
    """Pinned literally, not recomputed.

    `fingerprint()` is the recurrence key: every open chain in the live database was hashed by the
    old implementation, so a changed value here does not fail a test — it silently stops collapsing
    repeat firings and every recurring alarm starts opening a fresh incident.
    """
    assert fingerprint(CANONICAL) == "2af8bd27b50c43de"


def test_absent_message_still_hashes_as_empty() -> None:
    """A dict entry with no `message` read as `""` before and must not now fall through to the
    alert text — that would change the key for contexts that already work."""
    no_message = {"service": "gcm", "sample_logs": [{"ts": "1"}], "alert": "ignored"}
    only_empty = {"service": "gcm", "sample_logs": [{"message": ""}], "alert": "different"}
    assert fingerprint(no_message) == fingerprint(only_empty)


def test_no_logs_falls_back_to_the_alert() -> None:
    assert first_message({"service": "gcm"}) is None
    assert first_message({"service": "gcm", "sample_logs": []}) is None
    assert fingerprint({"service": "gcm", "alert": "disk full"}) != fingerprint({"service": "gcm"})


# --- normalization ---------------------------------------------------------------------------


def test_strings_become_message_only_lines() -> None:
    assert log_lines({"sample_logs": ["a", "b"]}) == [{"message": "a"}, {"message": "b"}]


def test_a_pasted_blob_splits_into_lines_not_one() -> None:
    assert log_lines({"sample_logs": "first\n\nsecond\n"}) == [
        {"message": "first"},
        {"message": "second"},
    ]


def test_canonical_entries_pass_through_untouched() -> None:
    assert log_lines(CANONICAL) == CANONICAL["sample_logs"]


def test_unreadable_logs_degrade_to_empty_rather_than_raising() -> None:
    assert log_lines({"sample_logs": 7}) == []
    assert log_lines({}) == []


def test_rendered_string_line_carries_no_none_placeholders() -> None:
    rendered = build_user_message({"service": "gcm", "sample_logs": ["raw cloudwatch line"]})
    assert "raw cloudwatch line" in rendered
    assert "None" not in rendered


def test_repeat_counts_still_render() -> None:
    ctx = {"service": "gcm", "sample_logs": [{"message": "boom", "count": 4}]}
    assert "x4 times" in build_user_message(ctx)
