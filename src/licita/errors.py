"""One error type for everything an operator can fix: what went wrong, why, and what to do."""
from __future__ import annotations


class LicitaError(Exception):
    """An error with a problem, its cause and the fix, printed as three lines."""

    def __init__(self, problem: str, cause: str, fix: str):
        self.problem, self.cause, self.fix = problem, cause, fix
        super().__init__(f"{problem}\n  cause: {cause}\n  fix:   {fix}")
