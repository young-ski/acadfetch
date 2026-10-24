"""Repository tokens, read from the environment.

Putting a token in ``--header`` leaks it into shell history and into every
``ps`` on the box. Each adapter instead declares the variable it reads, and the
token is attached per-source so it never travels to a host that has no business
seeing it.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

# source name -> variable that holds its token
TOKEN_ENV = {
    "zenodo": "ZENODO_TOKEN",
    "figshare": "FIGSHARE_TOKEN",
}


@dataclass(frozen=True)
class Token:
    source: str
    env: str
    value: str

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.value}"}

    @property
    def hint(self) -> str:
        """Enough to tell two tokens apart, not enough to use one."""
        return f"…{self.value[-4:]}" if len(self.value) > 8 else "…"

    def __str__(self) -> str:
        return f"${self.env} ({self.hint})"


def load(environ: Mapping[str, str] | None = None) -> dict[str, Token]:
    """Collect every token the environment provides, keyed by source."""
    env = os.environ if environ is None else environ
    found: dict[str, Token] = {}
    for source, name in TOKEN_ENV.items():
        value = (env.get(name) or "").strip()
        if value:
            found[source] = Token(source=source, env=name, value=value)
    return found


def headers(tokens: Mapping[str, Token], source: str | None) -> dict[str, str]:
    token = tokens.get(source or "")
    return token.headers if token else {}


def describe(tokens: Mapping[str, Token], source: str | None) -> str:
    token = tokens.get(source or "")
    if token is None:
        return ""
    return f"token from {token}"
