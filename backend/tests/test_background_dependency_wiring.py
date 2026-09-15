"""Unit tests for calling dependency factories outside a request — no DB, no network.

Background analysis (`_run_analysis`) has no request to resolve `Depends(...)` for it, so it
invokes the factories by hand. That hand-rolled call is easy to get wrong in a way nothing notices:
passing no session to a factory that wants one doesn't raise, it hands over the `Depends` sentinel,
which then fails deep inside a repository with "'Depends' object has no attribute 'get'".

The whole test suite missed it once, because every test overrides the analyzer factory with a
no-arg lambda and therefore only ever exercised the branch that was already fine. These cover the
other one.
"""

import inspect

from app.interface.http.deps import call_provider, get_base_analyzer


class _Sentinel:
    """Stands in for the session; identity is all that matters here."""


async def test_a_factory_that_wants_a_session_is_given_one():
    received = []

    async def provider(session):
        received.append(session)
        return "analyzer"

    session = _Sentinel()
    assert await call_provider(provider, session) == "analyzer"
    assert received == [session]


async def test_a_test_override_with_no_parameters_is_called_bare():
    """What every HTTP test installs: `lambda: FakeAnalyzer()`. Passing a session would TypeError."""
    assert await call_provider(lambda: "fake", _Sentinel()) == "fake"


async def test_a_synchronous_factory_is_not_awaited_twice():
    assert await call_provider(lambda: "fake", _Sentinel()) == "fake"


async def test_the_real_analyzer_factory_still_takes_a_session():
    """Pins the reason the branch exists. If this ever stops being true the dispatch above is dead
    code — and if it silently gained a session without this test, background analysis would break
    again in a way only a running container shows."""
    assert "session" in inspect.signature(get_base_analyzer).parameters
