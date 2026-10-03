import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from eqswap import cli
from tests.test_eqswap import make_index, make_install


class TestCliJson(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq, self.vault = os.path.join(self.tmp, "EQ"), os.path.join(self.tmp, "vault")
        make_install(self.eq)
        self.idx = os.path.join(self.tmp, "idx.json")
        make_index(self.idx)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def call(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--eq", self.eq, "--vault", self.vault, "--index", self.idx, "--json", *args])
        return code, json.loads(out.getvalue())

    def test_full_flow(self):
        code, r = self.call("info")
        self.assertTrue(r["ok"]); self.assertEqual(r["data"]["swaps"], 0)
        _, r = self.call("races", "--filter", "caz")
        self.assertEqual([x["race"] for x in r["data"]], [95])
        _, r = self.call("models", "--filter", "cth")
        self.assertEqual(r["data"][0]["list_archive"], "cth")
        _, r = self.call("zones")
        self.assertEqual(r["data"]["all"], ["gfaydark", "potimeb"])
        _, r = self.call("swap", "95", "cth", "--zones", "potimeb", "--height", "6")
        self.assertEqual(r["data"]["original_tag"], "CAZ")
        _, r = self.call("plan"); self.assertIn("needs-apply", {x["state"] for x in r["data"]})
        _, r = self.call("apply"); self.assertEqual({x["path"]: x["state"] for x in r["data"]}, {"racedata.txt": "applied", "potimeb_chr.txt": "applied"})
        _, r = self.call("races", "--filter", "cth")
        self.assertTrue(r["data"][0]["swapped"]); self.assertEqual(r["data"][0]["tags"]["2"], "CTH")
        _, r = self.call("list"); did = r["data"][0]["id"]
        self.call("disable", did); self.call("apply")
        _, r = self.call("races", "--filter", "caz"); self.assertFalse(r["data"][0]["swapped"])
        self.call("remove", did[:8]); _, r = self.call("list"); self.assertEqual(r["data"], [])

    def test_check_command(self):
        compat = os.path.join(self.tmp, "model_compat.json")   # next to the index, as in production
        models = {"cth": {"format": "eqg_mds", "container": "cth.eqg", "bones": 1, "clips": ["stnd"], "extra_arms": False,
                          "weapon": {"primary": True, "secondary": True, "shield": True, "primary_parent": "ARMR_FARM",
                                     "secondary_parent": "ARML_HAND", "primary_under_hand": False,
                                     "secondary_under_hand": True, "tracks": {}}},
                  "orc": {"format": "wld_skinned", "container": "orc_chr.s3d", "bones": 1, "clips": ["P01"],
                          "extra_arms": False, "weapon": None}}
        with open(compat, "w") as f:
            json.dump({"version": 1, "common_clips": {"eqg_mds": ["stnd"], "wld_skinned": ["P01"]}, "models": models}, f)
        code, r = self.call("check", "54", "cth", "--zones", "gfaydark")
        self.assertEqual(code, 0)
        self.assertEqual(r["data"]["worst"], "warn")
        self.assertIn("weapon-bone-topology", {f["code"] for f in r["data"]["findings"]})
        code, r = self.call("check", "54", "orc")
        self.assertEqual(r["data"]["worst"], "error")          # same model
        code, r = self.call("check", "999", "cth")
        self.assertEqual(code, 2)

    def test_npcs_command(self):
        import sqlite3
        from contextlib import closing
        peq = os.path.join(self.tmp, "p.sqlite")
        with closing(sqlite3.connect(peq)) as db:
            db.executescript("""create table npc_types(id,name,race,gender,level,texture);
                create table spawn2(id,spawngroupID,zone,version,x,y,z,heading);
                create table spawnentry(spawngroupID,npcID,chance);
                insert into npc_types values (1,'an_orc',54,2,5,0),(2,'Cazic_Thule',95,2,70,0);
                insert into spawn2 values (1,10,'gfaydark',0,0,0,0,0),(2,11,'gfaydark',0,0,0,0,0);
                insert into spawnentry values (10,1,100),(11,2,100);""")
            db.commit()
        with open(os.path.join(self.tmp, "model_compat.json"), "w") as f:
            json.dump({"version": 1, "common_clips": {}, "models": {
                "orc": {"format": "wld_skinned", "container": "orc_chr.s3d", "bones": 1,
                        "clips": ["a", "b", "c", "d"], "extra_arms": False, "weapon": None}}}, f)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--eq", self.eq, "--vault", self.vault, "--index", self.idx, "--peq", peq,
                             "--json", "npcs", "gfaydark"])
        self.assertEqual(code, 0)
        by = {g["race"]: g for g in json.loads(out.getvalue())["data"]}
        self.assertEqual((by[54]["tag"], by[54]["has_model"], by[54]["clips"]), ("ORC", True, 4))
        self.assertEqual((by[95]["tag"], by[95]["has_model"], by[95]["clips"]), ("CAZ", True, None))
        # without a PEQ import the command explains why instead of crashing
        with redirect_stdout(out), redirect_stderr(err):
            out.truncate(0); out.seek(0)
            code = cli.main(["--eq", self.eq, "--vault", self.vault, "--index", self.idx, "--json", "npcs", "gfaydark"])
        self.assertEqual(code, 2)

    def test_disable_all_command(self):
        self.call("swap", "95", "cth", "--zones", "potimeb")
        self.call("apply")
        code, r = self.call("disable-all")
        self.assertEqual((code, r["data"]), (0, {"disabled": 1}))
        self.call("apply")
        _, r = self.call("races", "--filter", "caz")
        self.assertFalse(r["data"][0]["swapped"])
        _, r = self.call("list")
        self.assertEqual((len(r["data"]), r["data"][0]["enabled"]), (1, False))

    def test_errors_are_json_with_nonzero_exit(self):
        code, r = self.call("swap", "95", "nope", "--zones", "potimeb")
        self.assertEqual(code, 2); self.assertFalse(r["ok"]); self.assertIn("nope", r["error"])
        code, r = self.call("swap", "999", "cth", "--zones", "potimeb")
        self.assertEqual(code, 2); self.assertFalse(r["ok"])


if __name__ == "__main__":
    unittest.main()
