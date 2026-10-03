"""Pre-swap compatibility checks: will model B behave when it replaces model A?

Every check is offline and read-only, driven by data/model_compat.json (tools/build_compat_index.py).
Findings never block a swap except `error` (nothing sensible to apply); warnings explain what
will look wrong in-game. Evidence: Opus weapon investigation (data/debug_weapon_attach.md) and
the in-game swap tests of 2026-10-01 (see PLAN_mob_model_manager.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .peq import race_summary, zones_for_race
from .racedata import RaceData

ORDER = {"error": 0, "warn": 1, "info": 2}
GAP = 0.25  # weapon-track translation gap (units) that counts as "off the hand"


@dataclass
class Finding:
    level: str            # error | warn | info
    code: str
    message: str
    data: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "data": self.data}


class CompatDB:
    def __init__(self, path: str):
        with open(path) as f:
            d = json.load(f)
        self.models: dict[str, dict] = d["models"]
        self.common: dict[str, list[str]] = d["common_clips"]

    def get(self, tag: str) -> dict | None:
        return self.models.get(tag.lower())


def _family(fmt: str) -> str:
    return "eqg" if fmt.startswith("eqg") else "wld"


def _weapon_findings(tag: str, new: dict) -> list[Finding]:
    out: list[Finding] = []
    w = new.get("weapon")
    if w is None:
        return [Finding("info", "weapon-unchecked",
                        f"{tag.upper()} is a static/.mod model: weapon attach points were not analysed")]
    if new["format"] == "wld_skinned":
        if not w["primary"]:
            out.append(Finding("warn", "weapon-no-primary",
                               f"{tag.upper()} has no R_POINT weapon point: NPCs carrying a weapon will show it "
                               "in an unknown place (client behaviour with a missing point is untested)"))
        elif not w["secondary"]:
            out.append(Finding("info", "weapon-no-secondary",
                               f"{tag.upper()} has no L_POINT: no off-hand weapon or shield slot"))
        return out
    if not w["primary"]:
        out.append(Finding("warn", "weapon-no-primary",
                           f"{tag.upper()} has no ARMR_WEAP bone: a primary-hand weapon has no valid attach point"))
    elif w["primary_under_hand"] is False:
        out.append(Finding("warn", "weapon-bone-topology",
                           f"{tag.upper()}'s ARMR_WEAP hangs off '{w['primary_parent']}', not the hand, so a "
                           "weapon will not follow the hand's rotation (the CTH defect)",
                           {"bone": "ARMR_WEAP", "parent": w["primary_parent"]}))
    if not w["secondary"]:
        out.append(Finding("info", "weapon-no-secondary", f"{tag.upper()} has no ARML_WEAP: no off-hand weapon"))
    elif w["secondary_under_hand"] is False:
        out.append(Finding("warn", "weapon-bone-topology",
                           f"{tag.upper()}'s ARML_WEAP hangs off '{w['secondary_parent']}', not the hand",
                           {"bone": "ARML_WEAP", "parent": w["secondary_parent"]}))
    if not w["shield"]:
        out.append(Finding("info", "weapon-no-shield", f"{tag.upper()} has no ARML_SHLD: no shield slot"))
    for bone, t in sorted(w["tracks"].items()):
        if t["clips"] and t["over"] / t["clips"] >= 0.5:
            if t["exact"]:
                out.append(Finding("warn", "weapon-track-off-hand",
                                   f"{tag.upper()}'s {bone} animation track strays up to {t['max_gap']:.2f} units from its "
                                   f"rest position in {t['over']} of {t['clips']} clips: the item will sit off the hand "
                                   "in those animations", {"bone": bone, **t}))
            else:
                out.append(Finding("info", "weapon-track-unmatched",
                                   f"{tag.upper()}'s {bone} track ({t['max_gap']:.2f} u off) has a name quirk the client "
                                   "probably ignores: effect unknown", {"bone": bone, **t}))
    return out


def check_swap(db: CompatDB, old_tag: str, new_tag: str, *, rd: RaceData | None = None,
               race: int | None = None, zones: list[str] | None = None,
               peq_db: str | None = None, needs_list: bool = True) -> list[Finding]:
    f: list[Finding] = []
    if old_tag.lower() == new_tag.lower():
        return [Finding("error", "same-model", f"race already uses {new_tag.upper()}: nothing to swap")]
    new = db.get(new_tag)
    if new is None:
        return [Finding("error", "unknown-model", f"{new_tag.upper()} is not in this install's model index")]
    old = db.get(old_tag)

    # animation coverage. A row can borrow another race's animations (racedata field 53), e.g. GUL
    # has no clips of its own and the player races carry only 3; then the model's own file says nothing.
    donor = 0
    if rd is not None:
        rows = rd.rows_with_tag(new_tag)
        donor = rd.anim_donor(*rows[0]) if rows else 0
    if donor:
        dt = rd.tag_for(donor, 2) or rd.tag_for(donor, 0)
        f.append(Finding("info", "borrowed-animations",
                         f"{new_tag.upper()} borrows its animations from race {donor}"
                         f"{' (' + dt + ')' if dt else ''}; clip coverage of its own file is not meaningful",
                         {"donor_race": donor}))
    elif new["format"].startswith("wld") and new["container"].lower().startswith("global"):
        f.append(Finding("info", "shared-animations",
                         f"{new_tag.upper()}'s animations live in the shared global archives (player races), which "
                         "the clip survey does not read: coverage unknown, but these models animate in-game"))
    elif not new["clips"]:
        f.append(Finding("warn", "no-animations",
                         f"{new_tag.upper()} has no animation clips: NPCs using it will hold the rest pose"))
    elif old is not None and _family(old["format"]) == _family(new["format"]):
        key = "eqg_mds" if _family(new["format"]) == "eqg" else "wld_skinned"
        missing = [c for c in db.common.get(key, []) if c not in set(new["clips"])]
        if missing:
            f.append(Finding("warn", "missing-common-clips",
                             f"{new_tag.upper()} lacks clips most {_family(new['format']).upper()} models have: "
                             f"{', '.join(missing)} (those states will not animate)", {"missing": missing}))
        if old["clips"] and len(new["clips"]) < 0.5 * len(old["clips"]):
            f.append(Finding("warn", "fewer-clips",
                             f"{new_tag.upper()} has {len(new['clips'])} clips vs {len(old['clips'])} for "
                             f"{old_tag.upper()}: some animations the old model had will be missing"))
    elif old is not None:
        f.append(Finding("info", "clip-vocabularies",
                         "WLD and EQG models name their clips differently; coverage cannot be compared across "
                         "formats (cross-format swaps animated correctly in testing)"))

    f.extend(_weapon_findings(new_tag, new))
    if new.get("extra_arms"):
        f.append(Finding("info", "extra-arms", f"{new_tag.upper()} has extra arms; only the main pair can hold weapons"))

    # size: the client divides the server's NPC size by the row's native height
    if rd is not None and race is not None:
        rows = rd.rows_with_tag(new_tag)
        if rows:
            nat = rd.get_size(*rows[0])[0]
            cur = rd.get_size(race, rd.genders(race)[0])[0]
            if nat != cur:
                f.append(Finding("info", "height", f"{new_tag.upper()}'s native height is {nat} but this race row says "
                                 f"{cur}: set height {nat} or the model renders at the wrong scale",
                                 {"suggest_height": nat, "current": cur}))

    # who is affected
    if peq_db and race is not None:
        s = race_summary(peq_db).get(race)
        sp = zones_for_race(peq_db, race)
        if s:
            f.append(Finding("info", "impact", f"affects every NPC of race {race}: {s['npcs']} NPC types "
                             f"(e.g. {s['example']}) spawning in {len(sp)} zones", {"npcs": s["npcs"], "zones": sp}))
        if zones is not None and needs_list:
            missed = [z for z in sp if z not in {x.lower() for x in zones}]
            if missed:
                f.append(Finding("warn", "zones-not-covered",
                                 f"race {race} also spawns in {len(missed)} zone(s) you did not select "
                                 f"({', '.join(missed[:6])}{'...' if len(missed) > 6 else ''}). The race change is global, "
                                 "but those zones' lists would not load the new model, so the client cannot find it there "
                                 "and will most likely show a default model instead (untested). Select those zones too "
                                 "(button: 'Select all spawn zones') unless you don't care about them.",
                                 {"zones": missed}))
    f.sort(key=lambda x: ORDER[x.level])
    return f
