"""Unit tests that the factories actually attach what they declare — no DB, no AWS.

The evidence feature shipped dead. `IngestIncident.enricher` had a default of `None`, and not one
of the five real construction sites passed it: the HTTP route, the background-analysis resolver,
the poll job and both scheduler paths all built an `IngestIncident` that silently skipped
enrichment. Every test passed throughout, because tests build the class directly and pass an
enricher; only the factories in between were wrong.

`enricher` is now a required argument, so omitting it is a TypeError rather than a quiet no-op.
These pin the other half: that the factories pass a real one, not `None`.
"""

import inspect

from app.application.incidents.ingest import IngestIncident
from app.interface.http import deps


class _FakeSession:
    """Never used — the factories only stash it in repositories, which aren't called here."""


def _ingest_from(factory, **kwargs) -> IngestIncident:
    return factory(session=_FakeSession(), **kwargs)


def test_the_enricher_has_no_default():
    """The whole reason the bug was invisible. With a default, forgetting it costs nothing at
    construction and everything at runtime — no error, just an analysis with no evidence."""
    field = inspect.signature(IngestIncident).parameters["enricher"]
    assert field.default is inspect.Parameter.empty


def test_the_request_path_attaches_an_enricher():
    ingest = _ingest_from(deps.get_ingest_incident, analyzers=object(), enricher="the-enricher")
    assert ingest.enricher == "the-enricher"


def test_the_background_and_scheduler_paths_build_one_themselves():
    """They have no request to inject through, so they call `get_context_enricher` directly — the
    detail that was missing, and the reason unattended auto-analysis ran without evidence."""
    for factory in (deps.resolve_background_incident_deps, deps.get_poll_alarms_job):
        source = inspect.getsource(factory)
        assert "enricher=" in source, factory.__name__


def test_every_ingest_construction_in_the_app_passes_an_enricher():
    """A file-level sweep rather than a per-site test: the failure mode is a *new* call site added
    without it, which no test of the existing ones would notice."""
    import pathlib

    root = pathlib.Path(deps.__file__).resolve().parents[2]
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text()
        for index, line in enumerate(text.splitlines()):
            if "IngestIncident(" not in line or "import" in line:
                continue
            # The call spans several lines; look at the block that follows it.
            block = "\n".join(text.splitlines()[index : index + 12])
            if "enricher=" not in block:
                offenders.append(f"{path.name}:{index + 1}")
    assert offenders == [], f"IngestIncident built without an enricher at {offenders}"
