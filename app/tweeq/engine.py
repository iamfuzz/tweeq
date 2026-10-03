"""Swap engine: decisions in a manifest, replayed onto the current game files.

Why replay instead of shipping/caching files: after an official patch the live files change
(possibly unrelated lines); replaying small edits onto whatever is there is robust and
distributes no Daybreak content. Originals are vaulted on first touch for restore.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass

from .models import ModelIndex
from .racedata import RaceData
from .zonelist import ZoneList

SCHEMA = 1
RACEDATA = "racedata.txt"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _read(path: str) -> bytes | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


ALL_ZONES = "all"


def zone_file(zone: str) -> str:
    return f"{zone.lower()}_chr.txt"


def _atomic_write(path: str, data: bytes) -> None:
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tweeq-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


@dataclass
class FileReport:
    path: str
    state: str          # clean | applied | needs-apply | drifted | missing
    live_sha: str | None = None


class SwapError(Exception):
    pass


def norm_dir(p: str) -> str:
    """Comparable form of an install path: Windows and WSL spellings of the same folder match."""
    p = p.strip().replace("\\", "/").rstrip("/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        p = "/mnt/%s/%s" % (m.group(1).lower(), m.group(2))
    return p.lower()


class Swapper:
    def __init__(self, eq_dir: str, vault_dir: str, model_index: ModelIndex | None = None):
        self.eq = eq_dir
        self.vault = vault_dir
        self.models = model_index
        os.makedirs(os.path.join(vault_dir, "originals"), exist_ok=True)
        self.manifest_path = os.path.join(vault_dir, "manifest.json")
        self.m = self._load()

    # ---------------------------------------------------------------- manifest
    def _load(self) -> dict:
        if os.path.exists(self.manifest_path):
            with open(self.manifest_path) as f:
                m = json.load(f)
            if m.get("schema_version") != SCHEMA:
                raise SwapError(f"unsupported manifest schema {m.get('schema_version')}")
            if m.get("eq_dir") and norm_dir(m["eq_dir"]) != norm_dir(self.eq):
                raise SwapError(f"this vault belongs to a different install ({m['eq_dir']}), not {self.eq}: "
                                "use that install, or a separate vault for this one")
            return m
        return {"schema_version": SCHEMA, "decisions": [], "files": {}}

    def _save(self) -> None:
        self.m.setdefault("eq_dir", self.eq)     # stamp the install this vault belongs to
        _atomic_write(self.manifest_path, json.dumps(self.m, indent=2).encode())

    # ---------------------------------------------------------------- file access
    def _live(self, rel: str) -> bytes | None:
        return _read(os.path.join(self.eq, rel))

    def _vaulted(self, rel: str) -> bytes | None:
        return _read(os.path.join(self.vault, "originals", rel))

    def _vault_put(self, rel: str, data: bytes) -> None:
        _atomic_write(os.path.join(self.vault, "originals", rel), data)

    def _base(self, rel: str) -> bytes:
        """The un-swapped content of a file: vaulted original, else the live file."""
        b = self._vaulted(rel)
        if b is None:
            b = self._live(rel)
        if b is None:
            raise SwapError(f"{rel} not found in the EQ install")
        return b

    # ---------------------------------------------------------------- zones
    def all_zone_names(self) -> list[str]:
        """Every zone that has a model list (<zone>_chr.txt) in the install right now."""
        try:
            names = os.listdir(self.eq)
        except OSError:
            return []
        return sorted(n[:-len("_chr.txt")] for n in names if n.lower().endswith("_chr.txt"))

    def zones_of(self, d: dict) -> list[str]:
        """The zones a decision applies to. An all-zones swap is expanded NOW, so zones that a game
        patch adds later are picked up the next time the swap is applied."""
        return self.all_zone_names() if d.get("all_zones") else list(d["zones"])

    # ---------------------------------------------------------------- decisions
    def decisions(self) -> list[dict]:
        return list(self.m["decisions"])

    def add_swap(self, race: int, model_tag: str, zones: list[str] | str,
                 genders: list[int] | None = None, height: str | None = None) -> dict:
        """`zones` is a list of zone names, or ALL_ZONES ("all") for every zone in the install."""
        every_zone = zones == ALL_ZONES or zones == [ALL_ZONES]
        if every_zone:
            zones = []
        rd = RaceData(self._base(RACEDATA))
        gs = genders if genders is not None else rd.genders(race)
        if not gs:
            raise SwapError(f"race {race} has no racedata rows")
        for g in gs:
            if not rd.has(race, g):
                raise SwapError(f"race {race} has no gender-{g} row")
        for d in self.m["decisions"]:
            if d["enabled"] and d["race"] == race and set(d["genders"]) & set(gs):
                raise SwapError(f"race {race} is already swapped by decision {d['id'][:8]}")
        archive, fmt, notes = None, None, []
        if self.models is not None:
            ref = self.models.resolve(model_tag)
            archive, fmt, notes = ref.list_archive, ref.format, ref.notes
        for z in zones:
            self._base(zone_file(z))  # must exist
        dec = {
            "id": str(uuid.uuid4()), "enabled": True,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "race": race, "genders": gs, "model_tag": model_tag.upper(),
            "list_archive": archive, "format": fmt, "zones": [z.lower() for z in zones],
            "all_zones": every_zone,
            "height": height, "notes": notes,
            "originals": {str(g): {"tag": rd.get_tag(race, g), "size": list(rd.get_size(race, g))}
                          for g in gs},
        }
        self.m["decisions"].append(dec)
        self._save()
        return dec

    def remove_swap(self, decision_id: str) -> None:
        n = [d for d in self.m["decisions"] if not d["id"].startswith(decision_id)]
        if len(n) == len(self.m["decisions"]):
            raise SwapError(f"no decision {decision_id}")
        self.m["decisions"] = n
        self._save()

    def set_all_enabled(self, enabled: bool) -> int:
        for d in self.m["decisions"]:
            d["enabled"] = enabled
        self._save()
        return len(self.m["decisions"])

    def set_enabled(self, decision_id: str, enabled: bool) -> None:
        for d in self.m["decisions"]:
            if d["id"].startswith(decision_id):
                d["enabled"] = enabled
                self._save()
                return
        raise SwapError(f"no decision {decision_id}")

    # ---------------------------------------------------------------- rendering
    def managed_files(self) -> list[str]:
        out = {RACEDATA}
        for d in self.m["decisions"]:
            out.update(zone_file(z) for z in self.zones_of(d))
        out.update(self.m["files"])
        return sorted(out)

    def render(self, bases: dict[str, bytes] | None = None) -> dict[str, bytes]:
        """Desired content of every managed file = base + enabled decisions replayed."""
        bases = bases or {}
        get = lambda rel: bases[rel] if rel in bases else self._base(rel)  # noqa: E731
        rd = RaceData(get(RACEDATA))
        zl: dict[str, ZoneList] = {}
        zl_changed: set[str] = set()
        for d in self.m["decisions"]:
            if not d["enabled"]:
                continue
            for g in d["genders"]:
                rd.set_tag(d["race"], g, d["model_tag"])
                if d.get("height"):
                    rd.set_size(d["race"], g, d["height"])
            if d["list_archive"]:
                for z in self.zones_of(d):
                    rel = zone_file(z)
                    if rel not in zl:
                        zl[rel] = ZoneList(get(rel))
                    if zl[rel].ensure(d["model_tag"], d["list_archive"]):
                        zl_changed.add(rel)
        out = {RACEDATA: rd.tobytes()}
        for rel in self.managed_files():
            if rel == RACEDATA:
                continue
            out[rel] = zl[rel].tobytes() if rel in zl_changed else get(rel)
        return out

    # ---------------------------------------------------------------- status / apply
    def status(self) -> list[FileReport]:
        """Per-file state vs. what the enabled decisions require.

        clean       live == original and no swap needs this file changed
        applied     live == desired content (swaps active)
        needs-apply live differs from desired but is a state we recognise
                    (original after a patch/restore, or our previous apply)
        drifted     live is neither original, nor our last apply: changed by something
                    else (e.g. a patch) and needs review / accept_drift
        missing     file not in the install
        """
        want = self.render()
        reps = []
        for rel in self.managed_files():
            live = self._live(rel)
            if live is None:
                reps.append(FileReport(rel, "missing"))
                continue
            s = sha(live)
            orig = self._vaulted(rel)
            rec = self.m["files"].get(rel)
            if s == sha(want[rel]):
                state = "clean" if (orig is None or want[rel] == orig) else "applied"
            elif rec is None:
                state = "needs-apply"
            elif s in (rec["original_sha"], rec["applied_sha"]):
                state = "needs-apply"
            else:
                state = "drifted"
            reps.append(FileReport(rel, state, s))
        return reps

    def apply(self, accept_drift: bool = False, dry_run: bool = False) -> list[FileReport]:
        """Write every managed file so it matches the enabled decisions."""
        # 1. re-baseline drifted files (a patch changed them): live becomes the new original
        bases: dict[str, bytes] = {}
        for rep in self.status():
            if rep.state == "drifted":
                if not accept_drift:
                    raise SwapError(f"{rep.path} was changed outside tweeq (likely a patch); "
                                    "re-run with accept_drift to adopt it as the new original")
                bases[rep.path] = self._live(rep.path)
        want = self.render(bases)
        if dry_run:
            return self.status()
        # 2. vault originals on first touch (or adopt drifted live files)
        for rel in want:
            if rel in bases:
                self._vault_put(rel, bases[rel])
            elif self._vaulted(rel) is None:
                live = self._live(rel)
                if live is None:
                    raise SwapError(f"{rel} not found in the EQ install")
                self._vault_put(rel, live)
        # 3. write
        for rel, data in want.items():
            orig = self._vaulted(rel)
            _atomic_write(os.path.join(self.eq, rel), data)
            self.m["files"][rel] = {"original_sha": sha(orig), "applied_sha": sha(data)}
        self._save()
        return self.status()

    # ---------------------------------------------------------------- adopting existing edits
    def unmanaged_edits(self, vanilla_dir: str) -> list[str]:
        """Managed-kind files (racedata + zone lists) whose live content differs from the vanilla
        copy in `vanilla_dir` AND that this manifest does not already account for."""
        out = []
        for rel in sorted(os.listdir(vanilla_dir)):
            if rel != RACEDATA and not rel.endswith("_chr.txt"):
                continue
            v, live = _read(os.path.join(vanilla_dir, rel)), self._live(rel)
            if live is None or v == live:
                continue
            rec = self.m["files"].get(rel)
            if rec and sha(live) == rec["applied_sha"]:
                continue  # one of ours
            out.append(rel)
        return out

    def adopt_from_vanilla(self, vanilla_dir: str) -> dict:
        """Turn edits already present in the live install into recorded swaps.

        Reads vanilla copies of the same files, derives one decision per changed race row (tag, and
        height if changed) plus the zone-list lines that were added for it, vaults the vanilla files as
        the originals, and refuses unless replaying the derived decisions on the vanilla files
        reproduces the live files BYTE FOR BYTE (so adoption can never silently lose an edit)."""
        if self.m["decisions"]:
            raise SwapError("this vault already has swaps; adopt only into a fresh vault")
        diffs: dict[str, tuple[bytes, bytes]] = {}
        for rel in sorted(os.listdir(vanilla_dir)):
            if rel != RACEDATA and not rel.endswith("_chr.txt"):
                continue
            v, live = _read(os.path.join(vanilla_dir, rel)), self._live(rel)
            if live is not None and v != live:
                diffs[rel] = (v, live)
        if not diffs:
            return {"adopted": [], "files": []}
        if RACEDATA not in diffs:
            raise SwapError("zone lists differ from vanilla but racedata.txt does not: nothing to derive a swap from")
        vr, lr = RaceData(diffs[RACEDATA][0]), RaceData(diffs[RACEDATA][1])
        by_race: dict[int, dict[int, tuple]] = {}
        for race, g in lr.keys():
            if not vr.has(race, g):
                continue
            ot, os_, nt, ns = vr.get_tag(race, g), vr.get_size(race, g), lr.get_tag(race, g), lr.get_size(race, g)
            if (ot, os_) != (nt, ns):
                by_race.setdefault(race, {})[g] = (ot, os_, nt, ns)
        decisions = []
        for race, rows in sorted(by_race.items()):
            new_tags = {r[2] for r in rows.values()}
            if len(new_tags) != 1:
                raise SwapError(f"race {race}: rows were changed to different models; cannot adopt")
            tag = new_tags.pop()
            sizes = {r[3] for r in rows.values()}
            height = None
            if any(r[1] != r[3] for r in rows.values()):
                one = next(iter(sizes)) if len(sizes) == 1 else None
                if one is None or one[0] != one[1]:
                    raise SwapError(f"race {race}: odd size edit; cannot adopt")
                height = one[0]
            zones, archive = [], None
            for rel, (v, live) in diffs.items():
                if rel == RACEDATA:
                    continue
                a_new, a_old = ZoneList(live).get(tag), ZoneList(v).get(tag)
                if a_new and a_new != a_old:
                    zones.append(rel[:-len("_chr.txt")])
                    if archive not in (None, a_new):
                        raise SwapError(f"race {race}: zone lists use different archives for {tag}")
                    archive = a_new
            decisions.append({
                "id": str(uuid.uuid4()), "enabled": True, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "race": race, "genders": sorted(rows), "model_tag": tag.upper(), "list_archive": archive,
                "format": None, "zones": zones, "height": height, "notes": ["adopted from existing edits"],
                "originals": {str(g): {"tag": r[0], "size": list(r[1])} for g, r in rows.items()}})
        # stage, then prove the replay reproduces the live files exactly
        written = []
        try:
            for rel, (v, _live) in diffs.items():
                self._vault_put(rel, v)
                written.append(rel)
            self.m["decisions"] = decisions
            want = self.render()
            bad = [rel for rel, (_v, live) in diffs.items() if want.get(rel) != live]
            if bad:
                raise SwapError("replaying the derived swaps does not reproduce these live files byte for byte "
                                f"(other edits present?): {', '.join(bad)}")
            for rel, (v, live) in diffs.items():
                self.m["files"][rel] = {"original_sha": sha(v), "applied_sha": sha(live)}
            self._save()
        except BaseException:
            self.m["decisions"] = []
            self.m["files"] = {}
            for rel in written:
                try:
                    os.remove(os.path.join(self.vault, "originals", rel))
                except OSError:
                    pass
            raise
        return {"adopted": [{"race": d["race"], "from": d["originals"][str(d["genders"][0])]["tag"],
                             "to": d["model_tag"], "zones": d["zones"], "height": d["height"]} for d in decisions],
                "files": sorted(diffs)}

    def restore_all(self) -> None:
        """Put every managed file back to its vaulted original."""
        for rel in list(self.m["files"]):
            orig = self._vaulted(rel)
            if orig is not None:
                _atomic_write(os.path.join(self.eq, rel), orig)
                self.m["files"][rel]["applied_sha"] = sha(orig)
        self._save()
