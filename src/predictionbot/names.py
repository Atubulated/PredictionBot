from __future__ import annotations

import re


CLUB_SUFFIXES = {
    "afc",
    "cf",
    "city",
    "fc",
    "football club",
    "sc",
    "town",
    "united",
}

ALIASES = {
    "man utd": "manchester utd",
    "man united": "manchester utd",
    "manchester united": "manchester utd",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "wolves": "wolverhampton wanderers",
}


def normalize_team_name(value: str) -> str:
    name = value.casefold()
    name = name.replace("&", " and ")
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = ALIASES.get(name, name)

    parts = name.split()
    while parts and parts[-1] in CLUB_SUFFIXES:
        parts.pop()
    normalized = " ".join(parts)
    return ALIASES.get(normalized, normalized)
