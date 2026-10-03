"""Human-readable model names: tag `cth` -> "Cazic-Thule", `tmt` -> "Quarm".

Layers, best available wins (each is optional except the first):
  1. The client's own race names: dbstr_us.txt rows `<race id>^11^<name>`, joined to model tags
     through racedata.txt. Clean to ship (client data).
  2. Boss rule (needs the user's PEQ import): when every race using a tag has at most BOSS_MAX
     distinct NPC names, name the model after its most prominent NPC ("Quarm", not "Dragon").
  3. Duplicates get a suffix: "(male)"/"(female)" when the tags differ by gender, else "(TAG)".
  4. Overrides: JSON {"tag": "Name"} files, applied last (curated defaults + the user's own).
Anything unresolved falls back to the upper-case tag.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict
from contextlib import closing

from .racedata import RaceData

BOSS_MAX = 3
RACE_NAME_TYPE = "11"          # dbstr_us.txt type: singular race name
_LOD = re.compile(r"_lod\d+$")


def load_race_names(models_dir: str) -> dict[int, str]:
    p = os.path.join(models_dir, "dbstr_us.txt")
    out: dict[int, str] = {}
    if not os.path.exists(p):
        return out
    with open(p, encoding="latin1") as f:
        for ln in f:
            parts = ln.rstrip("\r\n").split("^")
            if len(parts) >= 3 and parts[1] == RACE_NAME_TYPE and parts[0].isdigit():
                out.setdefault(int(parts[0]), parts[2].strip())
    return out


def load_overrides(*paths: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for p in paths:
        if p and os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.update({k.lower(): v for k, v in json.load(f).items() if not k.startswith("_")})
    return out


def _clean_npc(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("_", " ").lstrip("#")).strip()


def _peq_by_race(peq_db: str) -> dict[int, dict[str, tuple[int, int]]]:
    """race -> {cleaned NPC name: (number of NPC types, highest level)}; one query for the whole db."""
    out: dict[int, dict[str, list[int]]] = defaultdict(dict)
    with closing(sqlite3.connect(peq_db)) as db:
        for race, name, n, lvl in db.execute(
                "select race, name, count(*), max(level) from npc_types group by race, name"):
            c = _clean_npc(name or "")
            if c:
                e = out[race].setdefault(c, [0, 0])
                e[0] += n
                e[1] = max(e[1], lvl or 0)
    return {r: {k: (v[0], v[1]) for k, v in d.items()} for r, d in out.items()}


def build_names(rd: RaceData, tags: list[str], race_names: dict[int, str], *,
                peq_db: str | None = None, overrides: dict[str, str] | None = None) -> dict[str, str]:
    overrides = overrides or {}
    peq = _peq_by_race(peq_db) if peq_db else {}
    base: dict[str, str] = {}
    genders: dict[str, set[int]] = {}
    for tag in tags:
        rows = rd.rows_with_tag(tag)
        genders[tag] = {g for _, g in rows}
        races = sorted({r for r, _ in rows})
        name = None
        if peq and races:
            npcs: dict[str, tuple[int, int]] = {}
            for r in races:
                for k, (n, lvl) in peq.get(r, {}).items():
                    cur = npcs.get(k, (0, 0))
                    npcs[k] = (cur[0] + n, max(cur[1], lvl))
            if 0 < len(npcs) <= BOSS_MAX:
                name = max(npcs.items(), key=lambda kv: kv[1])[0]   # most NPC types, then highest level
        if name is None:
            rn: list[str] = []
            for r in races:
                n = race_names.get(r)
                if n and n not in rn:
                    rn.append(n)
            name = " / ".join(rn[:2]) if rn else None
        base[tag] = name or tag.upper()

    out = dict(base)
    groups: dict[str, list[str]] = defaultdict(list)
    for tag, n in base.items():
        groups[n.lower()].append(tag)
    word = {frozenset({0}): "male", frozenset({1}): "female"}
    for members in groups.values():
        if len(members) < 2:
            continue
        # first try gender alone; only add the tag where that is still ambiguous
        label = {m: (f"{base[m]} ({word[frozenset(genders[m])]})" if frozenset(genders[m]) in word else base[m])
                 for m in members}
        first = dict(label)  # count duplicates against this snapshot, not labels already rewritten
        for m in members:
            if sum(1 for x in members if first[x] == first[m]) > 1:
                g = word.get(frozenset(genders[m]))
                label[m] = f"{base[m]} ({g + ', ' if g else ''}{m.upper()})"
        out.update(label)
    for tag in tags:
        if tag in overrides:
            out[tag] = overrides[tag]
    return out


def is_listable(tag: str) -> bool:
    """LOD variants are not distinct characters; keep them out of pickers."""
    return not _LOD.search(tag)
