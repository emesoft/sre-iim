"""Unit tests for triage lanes — no DB.

Lanes exist because `status` answers "where is this in the pipeline" and a person needs "is this
mine right now". The mapping is small, but three places read it (the list filter, the rollup counts,
the tabs), so it lives in the domain and is pinned here rather than being re-decided in each.
"""

import pytest

from app.domain.incidents.lanes import LANES, statuses_for


def test_new_and_failed_are_the_same_thing_to_a_person():
    """Opposite ends of the pipeline, identical meaning on call: nobody has looked at this yet.
    Splitting them would leave failed analyses invisible in a lane nobody opens."""
    assert set(statuses_for("triage")) == {"new", "failed"}


def test_no_lane_means_no_filter_not_an_empty_one():
    """The ProjectScope trap in another costume: an empty tuple is "match nothing", None is "don't
    filter". Returning () here would make the unfiltered list silently render empty."""
    assert statuses_for(None) is None


def test_an_unknown_lane_is_refused_rather_than_matching_nothing():
    """A typo in a query param must 422, not quietly show zero incidents as if all were handled."""
    with pytest.raises(ValueError, match="unknown lane"):
        statuses_for("trage")


def test_every_status_belongs_to_exactly_one_lane():
    """An incident missing from every lane can't be reached through the tabs at all, and one in two
    lanes gets worked twice. Both are silent."""
    seen: list[str] = []
    for statuses in LANES.values():
        seen.extend(statuses)
    assert len(seen) == len(set(seen)), "a status appears in more than one lane"
    assert set(seen) == {"new", "failed", "analyzing", "analyzed", "ticketed", "resolved"}


def test_the_lanes_are_ordered_the_way_the_work_flows():
    """The tab order is this dict's order; triage first is the whole point of the page."""
    assert list(LANES) == ["triage", "working", "ticketed", "done"]
