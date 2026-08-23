"""Loads the bundled starter runbooks from `app/seed_docs/*.md`.

Each file starts with a small frontmatter block (`title`/`source_type`/`service`/`tags`, one per
line, between two `---` markers) followed by the actual markdown content passed to chunking/
embedding unchanged. No YAML dependency — the format is deliberately this simple since it only
ever needs a handful of flat string fields.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

SEED_DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "seed_docs"

_FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@dataclass(frozen=True)
class SeedDocument:
    title: str
    source_type: str
    service: str | None
    tags: list[str]
    content: str


def _parse(text: str, *, source: str) -> SeedDocument:
    match = _FRONTMATTER_RE.match(text)
    if not match:
        raise ValueError(f"{source}: missing a --- frontmatter block")
    raw_fields, content = match.groups()
    fields: dict[str, str] = {}
    for line in raw_fields.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    if "title" not in fields:
        raise ValueError(f"{source}: frontmatter is missing 'title'")
    tags = [t.strip() for t in fields.get("tags", "").split(",") if t.strip()]
    return SeedDocument(
        title=fields["title"],
        source_type=fields.get("source_type", "runbook"),
        service=fields.get("service") or None,
        tags=tags,
        content=content.strip(),
    )


def load_seed_documents() -> list[SeedDocument]:
    """One entry per `*.md` file in `app/seed_docs/`, sorted by filename for a stable order."""
    return [
        _parse(path.read_text(), source=path.name)
        for path in sorted(SEED_DOCS_DIR.glob("*.md"))
    ]
