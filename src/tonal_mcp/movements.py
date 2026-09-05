"""Movement name -> Tonal movementId lookup.

Backed by data/curated.json, copied (as data, not a live dependency) from
tonal-garmin-sync's own curated movement catalog -- see SPEC.md and the plan
this was built from for why that file is shared but the two services' Tonal
API clients are not. Packaged as module data (not read from a repo-relative
config/ path) so it resolves the same way under an editable dev install and
a real `pip install .` inside Docker, where the package tree gets copied
into site-packages -- `Path(__file__).resolve().parent.parent.parent` broke
under the latter (confirmed live: FileNotFoundError against
/usr/local/lib/python3.12/config/curated.json).
"""

from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import TypedDict

_CURATED_PATH = Path(__file__).resolve().parent / "data" / "curated.json"


class MovementMatch(TypedDict):
    id: str
    name: str
    on_machine: bool


def _load_catalog() -> list[dict]:
    with _CURATED_PATH.open(encoding="utf-8") as f:
        return json.load(f)


_CATALOG = _load_catalog()


def find_movement(name: str, limit: int = 5) -> list[MovementMatch]:
    """Ranked matches for a free-text movement name. Exact (case-insensitive)
    match always sorts first; everything else is ranked by substring hit
    then string similarity, so a caller can eyeball whether the top result
    is actually right rather than trusting a single guess.
    """
    query = name.strip().lower()
    if not query:
        return []

    scored: list[tuple[float, dict]] = []
    for entry in _CATALOG:
        candidate = entry["name"].lower()
        if candidate == query:
            score = 1.0
        elif query in candidate:
            score = 0.9
        else:
            score = difflib.SequenceMatcher(None, query, candidate).ratio()
        if score > 0.4:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        MovementMatch(id=entry["movementId"], name=entry["name"], on_machine=entry["onMachine"])
        for _, entry in scored[:limit]
    ]
