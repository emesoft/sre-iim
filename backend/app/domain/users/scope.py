"""ProjectScope: which projects a request may touch.

Authorization here has two axes. `role` answers *what* someone may do (that's `require_role`);
this answers *whose data* they may do it to. Keeping them apart is what lets an SRE be an SRE
everywhere while only seeing the projects they were added to.

The value object exists so the answer is computed once per request and then passed down, rather
than each query re-deriving it — and so the two special cases (an admin sees everything; a user
with no memberships sees nothing) are written once instead of at every call site.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProjectScope:
    """`unrestricted` is an admin. Otherwise `projects` is the exact allow-list — and an empty one
    means exactly that: nothing. It is never read as "no filter", which is the mistake that would
    turn a brand-new account into a superuser."""

    projects: frozenset[str]
    unrestricted: bool = False

    @classmethod
    def all(cls) -> "ProjectScope":
        return cls(projects=frozenset(), unrestricted=True)

    @classmethod
    def of(cls, projects: object) -> "ProjectScope":
        return cls(projects=frozenset(projects or ()))

    @property
    def is_empty(self) -> bool:
        """True when this scope can see nothing — the state a newly-created account starts in."""
        return not self.unrestricted and not self.projects

    def allows(self, project: str | None) -> bool:
        if self.unrestricted:
            return True
        return project is not None and project in self.projects

    @property
    def names(self) -> tuple[str, ...] | None:
        """The allow-list for a SQL filter, or None for "don't filter at all" (admins only).

        Callers must treat None and `()` as opposites: None means no WHERE clause, an empty tuple
        means a clause that matches nothing.
        """
        if self.unrestricted:
            return None
        return tuple(sorted(self.projects))
