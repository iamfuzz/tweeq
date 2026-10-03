import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing

from eqswap import names
from eqswap.racedata import RaceData
from tests.test_eqswap import row, wr_

LIVE = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"
PEQ = os.path.join(os.path.dirname(__file__), "..", "..", "data", "peq_slim.sqlite")


def mk(rows):
    return RaceData(b"\n".join(rows + [b""]))


class TestNames(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.peq = os.path.join(self.tmp, "p.sqlite")
        with closing(sqlite3.connect(self.peq)) as db:
            db.executescript("""create table npc_types(id,name,race,gender,level,texture);
                insert into npc_types values (1,'Quarm',304,2,80,0),
                  (2,'Cazic_Thule',95,2,70,0),(3,'         ',95,2,60,0),(4,'Avatar_of_Fear',95,2,60,0),
                  (5,'a_bandit',2,0,40,0),(6,'a_cutthroat',2,0,14,0),(7,'a_victim',2,0,60,0),(8,'a_citizen',2,0,41,0);""")
            db.commit()
        self.rn = {1: "Human", 2: "Barbarian", 304: "Dragon", 95: "Cazic-Thule", 54: "Orc", 458: "Orc"}

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_race_names_from_dbstr(self):
        d = os.path.join(self.tmp, "eq"); os.makedirs(d)
        wr_(os.path.join(d, "dbstr_us.txt"), b"2^11^Barbarian^0^\n2^12^Barbarians^0^\n95^11^Cazic-Thule^0^\n203^45^junk^0^\n")
        self.assertEqual(names.load_race_names(d), {2: "Barbarian", 95: "Cazic-Thule"})
        self.assertEqual(names.load_race_names(self.tmp), {})            # missing file is fine

    def test_race_name_without_peq(self):
        rd = mk([row(304, 2, "TMT"), row(2, 0, "BAM")])
        n = names.build_names(rd, ["tmt", "bam"], self.rn)
        self.assertEqual((n["tmt"], n["bam"]), ("Dragon", "Barbarian"))

    def test_boss_rule_names_unique_models_after_the_npc(self):
        rd = mk([row(304, 2, "TMT"), row(95, 2, "CAZ"), row(2, 0, "BAM")])
        n = names.build_names(rd, ["tmt", "caz", "bam"], self.rn, peq_db=self.peq)
        self.assertEqual(n["tmt"], "Quarm")                  # 1 NPC name
        self.assertEqual(n["caz"], "Cazic Thule")            # blank NPC ignored, 2 distinct names left
        self.assertEqual(n["bam"], "Barbarian")              # 4 distinct names: a generic race, keep race name

    def test_duplicates_get_gender_or_tag_suffix(self):
        rd = mk([row(1, 0, "HUM"), row(1, 1, "HUF"), row(54, 2, "ORC"), row(458, 2, "ORK")])
        n = names.build_names(rd, ["hum", "huf", "orc", "ork"], self.rn)
        self.assertEqual((n["hum"], n["huf"]), ("Human (male)", "Human (female)"))
        self.assertEqual((n["orc"], n["ork"]), ("Orc (ORC)", "Orc (ORK)"))

    def test_gender_first_then_tag_only_when_still_ambiguous(self):
        # two barbarian-named families, both with a male and a female tag
        rd = mk([row(2, 0, "BAM"), row(2, 1, "BAF"), row(90, 0, "HLM"), row(90, 1, "HLF"), row(1, 1, "HUF"), row(1, 0, "HUM")])
        rn = {2: "Barbarian", 90: "Barbarian", 1: "Human"}
        n = names.build_names(rd, ["bam", "baf", "hlm", "hlf", "hum", "huf"], rn)
        self.assertEqual((n["bam"], n["baf"]), ("Barbarian (male, BAM)", "Barbarian (female, BAF)"))
        self.assertEqual((n["hlm"], n["hlf"]), ("Barbarian (male, HLM)", "Barbarian (female, HLF)"))
        self.assertEqual((n["hum"], n["huf"]), ("Human (male)", "Human (female)"))

    def test_overrides_and_fallback(self):
        rd = mk([row(95, 2, "CAZ")])
        ov = os.path.join(self.tmp, "o.json")
        wr_(ov, json.dumps({"_comment": "x", "CAZ": "Cazic (old)"}).encode())
        self.assertEqual(names.load_overrides(ov, "/no/such/file"), {"caz": "Cazic (old)"})
        n = names.build_names(rd, ["caz", "zzz"], self.rn, overrides=names.load_overrides(ov))
        self.assertEqual((n["caz"], n["zzz"]), ("Cazic (old)", "ZZZ"))   # unknown tag -> upper-case tag

    def test_lod_variants_are_not_listable(self):
        self.assertFalse(names.is_listable("crh_lod1"))
        self.assertTrue(names.is_listable("cth"))


@unittest.skipUnless(os.path.exists(PEQ) and os.path.exists(os.path.join(LIVE, "dbstr_us.txt")),
                     "real install / PEQ import not available")
class TestRealNames(unittest.TestCase):
    def test_real_data(self):
        with open(os.path.join(LIVE, "racedata.txt"), "rb") as f:
            rd = RaceData(f.read())
        rn = names.load_race_names(LIVE)
        n = names.build_names(rd, ["tmt", "cth", "caz", "hum", "huf", "gfm"], rn, peq_db=PEQ,
                              overrides=names.load_overrides(os.path.join(os.path.dirname(__file__), "..", "..", "data", "model_names.json")))
        self.assertEqual(n["tmt"], "Quarm")
        self.assertEqual((n["cth"], n["caz"]), ("Cazic-Thule (new)", "Cazic-Thule (classic)"))
        self.assertEqual((n["hum"], n["huf"]), ("Human (male)", "Human (female)"))
        self.assertTrue(n["gfm"])


if __name__ == "__main__":
    unittest.main()
