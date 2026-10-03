"""Optional zone discovery from a user-imported PEQ database (data/peq_slim.sqlite).

The PEQ data is GPL and user-supplied; this module only reads it if the user has
imported it (tools/peq_load.py). Everything else in tweeq works without it."""
from __future__ import annotations

import sqlite3
from contextlib import closing


def zones_for_race(db_path: str, race: int, gender: int | None = None) -> list[str]:
    q = ("select distinct s.zone from spawn2 s "
         "join spawnentry e on e.spawngroupID=s.spawngroupID "
         "join npc_types n on n.id=e.npcID where n.race=?")
    args: list = [race]
    if gender is not None:
        q += " and n.gender=?"
        args.append(gender)
    with closing(sqlite3.connect(db_path)) as db:
        return sorted(r[0].lower() for r in db.execute(q, args))


def race_summary(db_path: str) -> dict[int, dict]:
    """race id -> {"npcs": number of NPC types, "example": a representative NPC name}."""
    with closing(sqlite3.connect(db_path)) as db:
        rows = db.execute("select race, count(*), min(name) from npc_types group by race").fetchall()
    return {r: {"npcs": n, "example": (ex or "").replace("_", " ").lstrip("#")} for r, n, ex in rows}


def npc_groups_in_zone(db_path: str, zone: str) -> list[dict]:
    """Distinct (race, gender) groups of NPCs that spawn in a zone, biggest groups first.
    Each carries a representative NPC (highest level, then name) for display."""
    q = ("select n.race, n.gender, n.name, n.level, n.texture, n.id from spawn2 s "
         "join spawnentry e on e.spawngroupID=s.spawngroupID join npc_types n on n.id=e.npcID "
         "where lower(s.zone)=? and s.version=0")
    groups: dict[tuple[int, int], dict] = {}
    with closing(sqlite3.connect(db_path)) as db:
        for race, gender, name, level, texture, nid in db.execute(q, (zone.lower(),)):
            g = groups.setdefault((race, gender), {"race": race, "gender": gender, "npcs": set(),
                                                   "example": "", "level": -1, "texture": 0})
            g["npcs"].add(nid)
            if (level or 0, name) > (g["level"], g["example"]):
                g["level"], g["example"], g["texture"] = level or 0, name, texture or 0
    out = []
    for g in groups.values():
        g["npcs"] = len(g["npcs"])
        g["example"] = g["example"].replace("_", " ").lstrip("#")
        out.append(g)
    out.sort(key=lambda g: (-g["npcs"], g["race"], g["gender"]))
    return out
