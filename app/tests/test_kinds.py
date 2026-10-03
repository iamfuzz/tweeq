import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout

from eqswap import cli
from eqswap.kinds import DEFAULT_RULES, Kinds
from eqswap.models import ModelIndex
from tests.test_eqswap import make_install, row, wr_


class TestKindsRules(unittest.TestCase):
    def setUp(self):
        self.k = Kinds(DEFAULT_RULES)

    def test_object_families_are_hidden(self):
        for tag in ("i11", "i35", "t00", "t13", "dest_cgg", "it12095", "g05", "bnx", "cak", "box", "brl", "chs", "cst",
                    "och", "vas", "rak", "tbl", "btp", "prt", "gsp", "ghostship", "shp", "b09", "b10", "flg"):
            self.assertTrue(self.k.is_object(tag), tag)
        for fmt_only in ("boat", "launch", "ship"):
            self.assertTrue(self.k.is_object(fmt_only, "wld_static"), fmt_only)

    def test_creatures_and_player_races_are_kept(self):
        for tag, fmt in (("cth", "eqg_mds"), ("tmt", "wld_skinned"), ("hum", "wld_skinned"), ("bat", "eqg_mod"),
                         ("dkm", "eqg_mod"), ("wlf", "eqg_mod"), ("kar", "wld_skinned"), ("gfm", "wld_skinned"),
                         ("bas", "eqg_mod"), ("b05", "eqg_mod"), ("zmm", "eqg_mod"), ("eye", "wld_skinned")):
            self.assertTrue(self.k.is_character(tag, fmt), tag)

    def test_patterns_do_not_catch_lookalikes(self):
        self.assertTrue(self.k.is_character("tmt"))      # t + letters is not the t## trap family
        self.assertTrue(self.k.is_character("ice"))
        self.assertTrue(self.k.is_character("itm"))      # 'it' + letters is not the it<digits> item family

    def test_user_file_can_add_and_rescue(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "model_kinds.json")
            wr_(p, json.dumps({"objects": ["hum"], "characters": ["i11", "BOX"]}).encode())
            k = Kinds(DEFAULT_RULES, p)
            self.assertTrue(k.is_object("hum"))           # user hid a character
            self.assertTrue(k.is_character("i11"))        # user rescued a pattern match
            self.assertTrue(k.is_character("box"))        # ...and an explicit one (case-insensitive)
        finally:
            shutil.rmtree(tmp)


class TestObjectsAreHiddenEverywhere(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq, self.vault = os.path.join(self.tmp, "EQ"), os.path.join(self.tmp, "vault")
        make_install(self.eq)
        # racedata: add a boat race (tag BOA) and an object race (tag I11) alongside the fixture's races
        rd = os.path.join(self.eq, "racedata.txt")
        with open(rd, "rb") as f:
            lines = f.read().split(b"\n")
        lines[-1:-1] = [row(72, 2, "BOAT"), row(500, 2, "I11")]
        wr_(rd, b"\n".join(lines))
        models = [{"tag": "orc", "format": "wld_skinned", "container": "orc_chr.s3d"},
                  {"tag": "boat", "format": "wld_static", "container": "boat_chr.s3d"},
                  {"tag": "i11", "format": "eqg_mod", "container": "i11.eqg"},
                  {"tag": "caz", "format": "wld_skinned", "container": "caz_chr.s3d"}]
        self.idx = os.path.join(self.tmp, "idx.json")
        wr_(self.idx, json.dumps({"models": models}).encode())
        self.peq = os.path.join(self.tmp, "p.sqlite")
        with closing(sqlite3.connect(self.peq)) as db:
            db.executescript("""create table npc_types(id,name,race,gender,level,texture);
                create table spawn2(id,spawngroupID,zone,version,x,y,z,heading);
                create table spawnentry(spawngroupID,npcID,chance);
                insert into npc_types values (1,'an_orc',54,2,5,0),(2,'a_boat',72,2,1,0),(3,'a_boulder',500,2,1,0);
                insert into spawn2 values (1,10,'gfaydark',0,0,0,0,0),(2,11,'gfaydark',0,0,0,0,0),(3,12,'gfaydark',0,0,0,0,0);
                insert into spawnentry values (10,1,100),(11,2,100),(12,3,100);""")
            db.commit()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def call(self, *args):
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            code = cli.main(["--eq", self.eq, "--vault", self.vault, "--index", self.idx, "--peq", self.peq, "--json", *args])
        return code, json.loads(o.getvalue())

    def test_model_list_race_list_and_npc_list_exclude_objects(self):
        _, r = self.call("models")
        self.assertEqual(sorted(m["tag"] for m in r["data"]), ["caz", "orc"])
        _, r = self.call("races")
        self.assertEqual(sorted(x["race"] for x in r["data"]), [54, 95])            # no boat race, no boulder race
        _, r = self.call("npcs", "gfaydark")
        self.assertEqual([g["race"] for g in r["data"]], [54])                      # boat + boulder NPCs not listed

    def test_swapping_to_an_object_is_refused(self):
        code, r = self.call("swap", "54", "boat", "--zones", "all")
        self.assertEqual(code, 2); self.assertFalse(r["ok"])
        code, r = self.call("swap", "54", "i11", "--zones", "all")
        self.assertEqual(code, 2)

    def test_a_tag_with_one_static_entry_is_hidden_entirely(self):
        models = [{"tag": "ship", "format": "wld_static", "container": "oot_chr.s3d"},
                  {"tag": "ship", "format": "wld_skinned", "container": "ship1_chr.s3d"},
                  {"tag": "orc", "format": "wld_skinned", "container": "orc_chr.s3d"}]
        p = os.path.join(self.tmp, "i2.json")
        wr_(p, json.dumps({"models": models}).encode())
        idx = ModelIndex(p, self.eq, Kinds(DEFAULT_RULES))
        self.assertNotIn("ship", idx.by_tag)
        self.assertIn("ship", idx.hidden)
        self.assertEqual(list(idx.by_tag), ["orc"])

    def test_engine_keeps_hidden_objects_for_review_only(self):
        idx = ModelIndex(self.idx, self.eq, Kinds(DEFAULT_RULES))
        self.assertEqual(sorted(idx.hidden), ["boat", "i11"])
        self.assertNotIn("boat", idx.by_tag)


if __name__ == "__main__":
    unittest.main()
