import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from tweeq import cli
from tests.test_tweeq import make_index, make_install


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


class TestUninstallRestore(unittest.TestCase):
    """`uninstall-restore --data-root`: put the game files back from every vault, never touch a patched file."""

    def setUp(self):
        from unittest import mock
        from tweeq.engine import Swapper
        from tweeq.models import ModelIndex
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EQ")
        self.data = os.path.join(self.tmp, "AppData")
        self.vault = os.path.join(self.data, "vault")
        make_install(self.eq)
        self.idx = os.path.join(self.tmp, "idx.json")
        make_index(self.idx)
        self.orig = {f: self.read(f) for f in ("racedata.txt", "potimeb_chr.txt")}
        sw = Swapper(self.eq, self.vault, ModelIndex(self.idx, self.eq))
        sw.add_swap(95, "cth", ["potimeb"], height="6")
        sw.apply()
        self.assertNotEqual(self.read("racedata.txt"), self.orig["racedata.txt"])
        self.running = mock.patch("tweeq.uninstall._running_client", return_value=[])
        self.running.start()

    def tearDown(self):
        self.running.stop()
        shutil.rmtree(self.tmp)

    def read(self, f):
        with open(os.path.join(self.eq, f), "rb") as fh:
            return fh.read()

    def call(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["--json", "uninstall-restore", "--data-root", self.data])
        return code, json.loads(out.getvalue())

    def test_restores_everything_and_keeps_the_vault(self):
        code, r = self.call()
        self.assertEqual(code, 0, r)
        for f, b in self.orig.items():
            self.assertEqual(self.read(f), b, f)
        self.assertEqual(r["data"]["drifted"], [])
        self.assertEqual(sorted(r["data"]["restored"][0]["files"]), ["potimeb_chr.txt", "racedata.txt"])
        self.assertTrue(os.path.exists(os.path.join(self.vault, "manifest.json")))      # never deleted
        self.assertTrue(os.path.exists(os.path.join(self.vault, "originals", "racedata.txt")))
        code, r = self.call()                                                           # idempotent
        self.assertEqual(code, 0, r)

    def test_a_patched_file_is_left_alone_and_reported_but_the_rest_is_restored(self):
        patched = b"2\r\nwrb,wrb_chr\r\nbrand,new_patch_line\r\n"
        with open(os.path.join(self.eq, "potimeb_chr.txt"), "wb") as f:
            f.write(patched)
        code, r = self.call()
        self.assertEqual(code, 3, r)
        self.assertEqual(r["data"]["drifted"], ["potimeb_chr.txt"])
        self.assertEqual(self.read("potimeb_chr.txt"), patched)                          # not overwritten
        self.assertEqual(self.read("racedata.txt"), self.orig["racedata.txt"])          # the safe file was restored

    def test_refuses_while_everquest_runs(self):
        from unittest import mock
        with mock.patch("tweeq.uninstall._running_client", return_value=[self.eq]):
            code, r = self.call()
        self.assertEqual(code, 4)
        self.assertIn("close", r["error"].lower())
        self.assertNotEqual(self.read("racedata.txt"), self.orig["racedata.txt"])       # nothing changed

    def test_a_vanished_install_is_skipped_and_no_vaults_is_fine(self):
        shutil.rmtree(self.eq)
        code, r = self.call()
        self.assertEqual(code, 0, r)
        self.assertEqual(r["data"]["restored"], [])
        self.assertEqual(r["data"]["skipped"][0]["reason"], "the EverQuest folder is gone")
        shutil.rmtree(self.vault)
        code, r = self.call()
        self.assertEqual((code, r["data"]["restored"], r["data"]["skipped"]), (0, [], []))

    def test_skip_drifted_leaves_a_changed_file_even_in_a_normal_apply(self):
        from tweeq.engine import Swapper, SwapError
        with open(os.path.join(self.eq, "racedata.txt"), "ab") as f:
            f.write(b"extra\r\n")
        sw = Swapper(self.eq, self.vault, None)
        with self.assertRaises(SwapError):
            sw.apply()                                                                   # the default still refuses
        before = self.read("racedata.txt")
        sw.set_all_enabled(False)
        sw.apply(skip_drifted=True)
        self.assertEqual(self.read("racedata.txt"), before)
        self.assertEqual(self.read("potimeb_chr.txt"), self.orig["potimeb_chr.txt"])


class TestProgressWriter(unittest.TestCase):
    def test_a_locked_progress_file_never_fails_the_command(self):
        from unittest import mock
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "p.json")
            write = cli._progress_writer(path)
            real = os.replace
            calls = {"n": 0}

            def flaky(a, b):             # Windows refuses os.replace while the UI has the target open
                calls["n"] += 1
                if calls["n"] < 3:
                    raise PermissionError("in use")
                return real(a, b)
            with mock.patch("os.replace", side_effect=flaky):
                write("stage one", 0.5)                       # retried until it works
            with open(path) as f:
                self.assertEqual(json.load(f), {"stage": "stage one", "pct": 0.5})
            with mock.patch("os.replace", side_effect=PermissionError("locked for good")):
                write("stage two", 0.9)                       # gives up quietly instead of raising
            with open(path) as f:
                self.assertEqual(json.load(f)["stage"], "stage one")
            self.assertIsNone(cli._progress_writer(None))
        finally:
            shutil.rmtree(tmp)
