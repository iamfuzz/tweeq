"""Character vs object: the swapper deals in characters only (for now).

Everything else in the client's model archives (boats, ships, banners, chests, traps, destructible
props, item models...) is hidden. The rules live in data/model_kinds.json (shipped, editable); a user can
add their own in <vault>/model_kinds.json. Rules: a tag is an OBJECT if its format is listed, its tag
matches a pattern, or it is listed explicitly; "characters" in the user's file rescue a tag.
"""
from __future__ import annotations

import json
import os
import re

DEFAULT_RULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "model_kinds.json")


class Kinds:
    def __init__(self, *rule_files: str):
        self.patterns: list[re.Pattern] = []
        self.formats: set[str] = set()
        self.objects: set[str] = set()
        self.characters: set[str] = set()
        for p in rule_files:
            if not p or not os.path.exists(p):
                continue
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.patterns += [re.compile(x, re.I) for x in d.get("object_patterns", [])]
            self.formats |= set(d.get("object_formats", []))
            self.objects |= {t.lower() for t in d.get("object_tags", []) + d.get("objects", [])}
            self.characters |= {t.lower() for t in d.get("characters", [])}

    def is_object(self, tag: str, fmt: str | None = None) -> bool:
        t = tag.lower()
        if t in self.characters:
            return False
        return (t in self.objects or (fmt is not None and fmt in self.formats)
                or any(p.search(t) for p in self.patterns))

    def is_character(self, tag: str, fmt: str | None = None) -> bool:
        return not self.is_object(tag, fmt)
