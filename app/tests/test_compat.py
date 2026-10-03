import json
import os
import shutil
import sqlite3
import tempfile
import unittest

from eqswap.compat import CompatDB, check_swap
from eqswap.racedata import RaceData
from tests.test_eqswap import row, wr_

REPO_COMPAT = os.path.join(os.path.dirname(__file__), "..", "..", "data", "model_compat.json")


def weapon(primary_parent="ARMR_HAND", secondary_parent="ARML_HAND", tracks=None, shield=True):
    uh = lambda p: None if p is None else any(w in p for w in ("HAND", "FING"))  # noqa: E731
    return {"primary": primary_parent is not None, "secondary": secondary_parent is not None, "shield": shield,
            "primary_parent": primary_parent, "secondary_parent": secondary_parent,
            "primary_under_hand": uh(primary_parent), "secondary_under_hand": uh(secondary_parent),
            "tracks": tracks or {}}


def model(fmt="eqg_mds", clips=("stnd", "walk", "idle"), w=None, **kw):
    return {"format": fmt, "container": "x.eqg", "bones": 50, "clips": list(clips),
            "extra_arms": kw.get("extra_arms", False), "weapon": w}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        models = {
            "old": model(w=weapon(), clips=("crmp", "gcst", "idle", "jmpa", "jmpu", "nrun", "slpr", "stnd", "stun", "swim", "turn", "walk")),
            "good": model(w=weapon(), clips=("crmp", "gcst", "idle", "jmpa", "jmpu", "nrun", "slpr", "stnd", "stun", "swim", "turn", "walk")),
            "forearm": model(w=weapon(primary_parent="ARMR_FARM"), clips=("idle", "stnd", "walk", "turn", "crmp", "gcst", "jmpa", "jmpu", "nrun", "slpr", "stun", "swim")),
            "badtrack": model(w=weapon(tracks={"ARMR_WEAP": {"clips": 10, "over": 10, "max_gap": 1.08, "exact": True}})),
            "quirk": model(w=weapon(tracks={"ARML_SHLD": {"clips": 10, "over": 10, "max_gap": 3.5, "exact": False}})),
            "noclips": model(fmt="wld_skinned", clips=(), w={"primary": True, "secondary": True, "shield": True,
                             "primary_under_hand": None, "secondary_under_hand": None, "tracks": {}}),
            "nopoint": model(fmt="wld_skinned", clips=("P01", "D05"), w={"primary": False, "secondary": False, "shield": False,
                             "primary_under_hand": None, "secondary_under_hand": None, "tracks": {}}),
            "wldold": model(fmt="wld_skinned", clips=("P01", "D05", "C05", "L01", "L02", "O01", "P03", "D01", "P06", "T05"),
                            w={"primary": True, "secondary": True, "shield": True, "primary_under_hand": None,
                               "secondary_under_hand": None, "tracks": {}}),
            "prop": model(fmt="eqg_mod", clips=(), w=None),
            "arms4": model(w=weapon(), extra_arms=True),
            "short": model(clips=("stnd",), w=weapon()),
        }
        self.dbp = os.path.join(self.tmp, "c.json")
        wr_(self.dbp, json.dumps({"version": 1, "common_clips": {
            "eqg_mds": ["crmp", "gcst", "idle", "jmpa", "jmpu", "nrun", "slpr", "stnd", "stun", "swim", "turn", "walk"],
            "wld_skinned": ["C05", "D05", "L01", "L02", "O01", "P01", "P03"]}, "models": models}).encode())
        self.db = CompatDB(self.dbp)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def codes(self, old, new, **kw):
        return {f.code: f for f in check_swap(self.db, old, new, **kw)}


class TestChecks(Base):
    def test_errors(self):
        self.assertEqual(list(self.codes("old", "old")), ["same-model"])
        self.assertEqual(list(self.codes("old", "nope")), ["unknown-model"])

    def test_clean_swap_has_no_warnings(self):
        self.assertEqual([f for f in check_swap(self.db, "old", "good") if f.level != "info"], [])

    def test_weapon_bone_under_forearm_is_flagged(self):
        c = self.codes("old", "forearm")
        self.assertEqual(c["weapon-bone-topology"].level, "warn")
        self.assertEqual(c["weapon-bone-topology"].data["parent"], "ARMR_FARM")

    def test_track_far_from_bind_flagged_only_when_exact(self):
        self.assertEqual(self.codes("old", "badtrack")["weapon-track-off-hand"].level, "warn")
        c = self.codes("old", "quirk")
        self.assertNotIn("weapon-track-off-hand", c)
        self.assertEqual(c["weapon-track-unmatched"].level, "info")

    def test_no_animations_and_missing_points(self):
        self.assertEqual(self.codes("wldold", "noclips")["no-animations"].level, "warn")
        c = self.codes("wldold", "nopoint")
        self.assertIn("weapon-no-primary", c)
        self.assertEqual(c["missing-common-clips"].data["missing"], ["C05", "L01", "L02", "O01", "P03"])

    def test_clip_gaps_within_same_format(self):
        c = self.codes("old", "short")
        self.assertIn("missing-common-clips", c); self.assertIn("fewer-clips", c)

    def test_cross_format_is_informational(self):
        c = self.codes("wldold", "good")
        self.assertEqual(c["clip-vocabularies"].level, "info")
        self.assertNotIn("missing-common-clips", c)

    def test_static_models_and_extra_arms(self):
        self.assertIn("weapon-unchecked", self.codes("old", "prop"))
        self.assertIn("extra-arms", self.codes("old", "arms4"))

    def test_height_suggestion_from_native_row(self):
        rd = RaceData(b"\n".join([row(95, 2, "CAZ", "11.5"), row(670, 2, "GOOD", "6"), b""]))
        f = self.codes("old", "good", rd=rd, race=95)["height"]
        self.assertEqual(f.data["suggest_height"], "6")
        rd2 = RaceData(b"\n".join([row(95, 2, "CAZ", "6"), row(670, 2, "GOOD", "6"), b""]))
        self.assertNotIn("height", self.codes("old", "good", rd=rd2, race=95))

    def test_zone_coverage_and_impact_use_peq(self):
        peq = os.path.join(self.tmp, "peq.sqlite")
        db = sqlite3.connect(peq)
        db.executescript("""create table npc_types(id,name,race,gender);create table spawn2(id,spawngroupID,zone,version,x,y,z,heading);
            create table spawnentry(spawngroupID,npcID,chance);
            insert into npc_types values(1,'Cazic_Thule',95,2),(2,'a_devotee',95,2);
            insert into spawn2 values(1,10,'cazicthule',0,0,0,0,0),(2,11,'fearplane',0,0,0,0,0);
            insert into spawnentry values(10,1,100),(11,2,100);""")
        db.commit(); db.close()
        c = self.codes("old", "good", race=95, zones=["fearplane"], peq_db=peq)
        self.assertEqual(c["impact"].data["npcs"], 2)
        self.assertEqual(c["zones-not-covered"].data["zones"], ["cazicthule"])
        c = self.codes("old", "good", race=95, zones=["fearplane"], peq_db=peq, needs_list=False)
        self.assertNotIn("zones-not-covered", c)
        c = self.codes("old", "good", race=95, zones=["cazicthule", "fearplane"], peq_db=peq)
        self.assertNotIn("zones-not-covered", c)

    def test_borrowed_animations_suppress_clip_warnings(self):
        # row for tag NOCLIPS borrows race 777's animations (field 53): its own file having no clips is fine
        def borrower(race, tag, donor):
            f = row(race, 2, tag).split(b"^")
            f[53] = str(donor).encode()
            return b"^".join(f)
        rd = RaceData(b"\n".join([row(95, 2, "OLD"), borrower(500, "NOCLIPS", 777), row(777, 2, "GOOD"), b""]))
        self.db.models["noclips"]["format"] = "eqg_mds"
        c = self.codes("old", "noclips", rd=rd, race=95)
        self.assertEqual(c["borrowed-animations"].data["donor_race"], 777)
        self.assertNotIn("no-animations", c)
        self.assertNotIn("missing-common-clips", c)
        # without a donor the same model is flagged
        rd2 = RaceData(b"\n".join([row(95, 2, "OLD"), row(500, 2, "NOCLIPS"), b""]))
        self.assertIn("no-animations", self.codes("old", "noclips", rd=rd2, race=95))

    def test_player_race_models_in_global_archives_are_not_flagged_for_clips(self):
        self.db.models["hum"] = {**model(fmt="wld_skinned", clips=("L01", "L02", "P01"), w=None),
                                 "container": "global_chr.s3d"}
        c = self.codes("wldold", "hum")
        self.assertEqual(c["shared-animations"].level, "info")
        self.assertNotIn("missing-common-clips", c)
        self.assertNotIn("fewer-clips", c)

    def test_findings_sorted_by_severity(self):
        lv = [f.level for f in check_swap(self.db, "old", "forearm")]
        self.assertEqual(lv, sorted(lv, key={"error": 0, "warn": 1, "info": 2}.get))


@unittest.skipUnless(os.path.exists(REPO_COMPAT), "model_compat.json not built")
class TestRealData(unittest.TestCase):
    def setUp(self):
        self.db = CompatDB(REPO_COMPAT)

    def test_cth_is_flagged_for_both_known_defects(self):
        codes = {f.code for f in check_swap(self.db, "caz", "cth")}
        self.assertIn("weapon-bone-topology", codes)      # ARMR_WEAP under the forearm
        self.assertIn("weapon-track-off-hand", codes)     # 10 clips with a bad constant track

    def test_ork_is_clean_for_weapons(self):
        codes = {f.code for f in check_swap(self.db, "orc", "ork")}
        self.assertNotIn("weapon-bone-topology", codes)
        self.assertNotIn("weapon-track-off-hand", codes)

    def test_install_wide_known_offenders(self):
        flagged = {t for t, v in self.db.models.items()
                   for b, x in ((v.get("weapon") or {}).get("tracks") or {}).items()
                   if x["exact"] and x["over"] / x["clips"] >= 0.5}
        self.assertTrue({"cth", "bur", "bal", "mbr"} <= flagged)

    def test_known_clipless_wld_models(self):
        self.assertIn("no-animations", {f.code for f in check_swap(self.db, "orc", "btp")})


if __name__ == "__main__":
    unittest.main()
