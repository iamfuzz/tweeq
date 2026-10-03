"""Resolve a model tag to where it lives and what a zone list needs to load it.

Inputs: installed_model_index.json (tools/scan_install.py) and the client's own
Resources/GlobalLoad.txt (archives the client loads in every zone: no list line needed).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from .kinds import Kinds


@dataclass
class ModelRef:
    tag: str
    format: str            # wld_skinned | wld_static | eqg_mds | eqg_mod
    container: str         # e.g. cth.eqg, orc_chr.s3d
    list_archive: str | None  # archive name for a `tag,archive` line; None = already global
    notes: list[str] = field(default_factory=list)


def global_archives(eq_dir: str) -> set[str]:
    """Archive basenames (no extension, lower case) loaded unconditionally via GlobalLoad.txt."""
    p = os.path.join(eq_dir, "Resources", "GlobalLoad.txt")
    out: set[str] = set()
    if not os.path.exists(p):
        return out
    with open(p, encoding="latin1") as f:
        for ln in f:
            parts = ln.strip().split(",")
            if len(parts) >= 4:
                out.add(parts[3].strip().lower())
    return out


class ModelIndex:
    def __init__(self, index_path: str, eq_dir: str, kinds: Kinds | None = None):
        with open(index_path) as f:
            data = json.load(f)
        self.kinds = kinds or Kinds()
        self.by_tag: dict[str, list[dict]] = {}
        self.hidden: dict[str, list[dict]] = {}     # objects (boats, props, traps...): kept only for review
        grouped: dict[str, list[dict]] = {}
        for m in data["models"]:
            if m["format"] in ("wld_skinned", "wld_static", "eqg_mds", "eqg_mod"):
                grouped.setdefault(m["tag"].lower(), []).append(m)
        for tag, entries in grouped.items():
            # a TAG is an object if any of its entries is (ship exists as a static mesh in one archive and a
            # skinned one in another; both are the same boat)
            is_obj = any(self.kinds.is_object(tag, e["format"]) for e in entries)
            (self.hidden if is_obj else self.by_tag)[tag] = entries
        self.globals = global_archives(eq_dir)

    def candidates(self, tag: str) -> list[dict]:
        """Every index entry (container/format/member) that defines this tag."""
        return list(self.by_tag.get(tag.lower(), []))

    def tags(self) -> list[str]:
        return sorted(self.by_tag)

    def resolve(self, tag: str) -> ModelRef:
        cands = self.by_tag.get(tag.lower())
        if not cands:
            raise KeyError(f"model tag {tag!r} is not in this install's model index")
        t = tag.lower()

        def score(m):  # prefer the canonical per-model archive over shared/zone ones
            c = m["container"].lower()
            stem = c.rsplit(".", 1)[0]
            return (0 if stem in (t, f"{t}_chr") else 1, c)

        m = sorted(cands, key=score)[0]
        c = m["container"]
        stem = c.rsplit(".", 1)[0].lower()
        notes: list[str] = []
        if len(cands) > 1:
            notes.append(f"tag defined in {len(cands)} containers; using {c}")
        if m["format"].startswith("eqg"):
            if stem != t:
                notes.append(f"EQG container {c} differs from tag {t}: list line is unverified")
            arc = None if stem in self.globals else stem
        else:
            if stem.startswith("global") or stem.endswith("_chr2"):
                notes.append("shared archive: assumed loaded for every zone that needs it")
                arc = None if stem in self.globals else stem
            else:
                arc = None if stem in self.globals else stem
        return ModelRef(t, m["format"], c, arc, notes)
