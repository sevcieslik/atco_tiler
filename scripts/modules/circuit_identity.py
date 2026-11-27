"""Resolve circuit IDs without rewriting authoritative identifiers in central SQLite.

Only spaces and underscores differ: PADDLE PRAIRIE == PADDLE_PRAIRIE.
Ambiguous normalized names fail closed; no arbitrary match is selected.
"""
from __future__ import annotations

import re


def normalized_entity_id(value: str) -> str:
    return re.sub(r"[ _]+", "_", str(value).strip()).casefold()


def resolve_circuit_id(con, requested: str) -> tuple[str, int]:
    """Return (authoritative entity_id, current_revision) from read-only DB."""
    exact = con.execute(
        "SELECT entity_id, current_revision FROM entities "
        "WHERE entity_kind='circuit' AND entity_id=?",
        (requested,),
    ).fetchone()
    if exact:
        return str(exact[0]), int(exact[1])

    key = normalized_entity_id(requested)
    candidates = con.execute(
        "SELECT entity_id, current_revision FROM entities WHERE entity_kind='circuit'"
    ).fetchall()
    matched = [(str(entity_id), int(revision)) for entity_id, revision in candidates
               if normalized_entity_id(entity_id) == key]
    if not matched:
        raise ValueError(f"{requested} is not registered in the central AOI database")
    if len(matched) > 1:
        names = ", ".join(row[0] for row in matched)
        raise ValueError(f"Ambiguous circuit name {requested!r}; matching database IDs: {names}")
    return matched[0]
