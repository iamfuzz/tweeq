import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from eqswap import cli
from eqswap.engine import RACEDATA, Swapper, SwapError, sha
from eqswap.models import ModelIndex
from eqswap.racedata import RaceData
from eqswap.zonelist import ZoneList
from tests.test_eqswap import (LIVE, VANILLA, REPO_INDEX, make_index, make_install, rd_, wr_)


class AdoptBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EQ")
        self.van = os.path.join(self.tmp, "vanilla")
        make_install(self.eq)
        shutil.copytree(self.eq, os.path.join(self.tmp, "vcopy"))
        os.makedirs(self.van)
        for f in (RACEDATA, "potimeb_chr.txt", "gfaydark_chr.txt"):
            shutil.copy(os.path.join(self.eq, f), os.path.join(self.van, f))
        self.idx = os.path.join(self.tmp, "idx.json")
        make_index(self.idx)
        self.vault = os.path.join(self.tmp, "vault")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def swapper(self, vault=None):
        return Swapper(self.eq, vault or self.vault, ModelIndex(self.idx, self.eq))

    def hand_edit(self, race=95, tag="CTH", height="6", zone="potimeb", archive="cth"):
        rd = RaceData(rd_(os.path.join(self.eq, RACEDATA)))
        rd.set_tag(race, 2, tag)
        if height:
            rd.set_size(race, 2, height)
        wr_(os.path.join(self.eq, RACEDATA), rd.tobytes())
        p = os.path.join(self.eq, f"{zone}_chr.txt")
        z = ZoneList(rd_(p)); z.ensure(tag.lower(), archive)
        wr_(p, z.tobytes())


class TestAdopt(AdoptBase):
    def test_adopts_a_hand_applied_swap_and_restore_returns_vanilla(self):
        self.hand_edit()
        sw = self.swapper()
        self.assertEqual(sw.unmanaged_edits(self.van), ["potimeb_chr.txt", RACEDATA])
        r = sw.adopt_from_vanilla(self.van)
        self.assertEqual(r["adopted"], [{"race": 95, "from": "CAZ", "to": "CTH", "zones": ["potimeb"], "height": "6"}])
        self.assertEqual(sw.unmanaged_edits(self.van), [])
        self.assertTrue(all(x.state == "applied" for x in sw.status()))
        d = sw.decisions()[0]
        self.assertEqual((d["race"], d["model_tag"], d["list_archive"], d["height"]), (95, "CTH", "cth", "6"))
        sw.apply()                                           # idempotent: live files unchanged
        self.assertEqual(RaceData(rd_(os.path.join(self.eq, RACEDATA))).get_tag(95, 2), "CTH")
        sw.restore_all()
        for f in (RACEDATA, "potimeb_chr.txt"):
            self.assertEqual(rd_(os.path.join(self.eq, f)), rd_(os.path.join(self.van, f)), f)

    def test_adopted_swap_survives_a_patch(self):
        self.hand_edit()
        sw = self.swapper(); sw.adopt_from_vanilla(self.van)
        shutil.copy(os.path.join(self.van, RACEDATA), os.path.join(self.eq, RACEDATA))   # a patch restores vanilla
        self.assertIn("needs-apply", [x.state for x in sw.status()])
        sw.apply()
        self.assertEqual(RaceData(rd_(os.path.join(self.eq, RACEDATA))).get_tag(95, 2), "CTH")

    def test_swap_without_height_and_global_model(self):
        self.hand_edit(race=54, tag="HUM", height=None, zone="gfaydark", archive="hum")
        # make the zone list change not look like an edit for this swap (global archive): revert it
        shutil.copy(os.path.join(self.van, "gfaydark_chr.txt"), os.path.join(self.eq, "gfaydark_chr.txt"))
        r = self.swapper().adopt_from_vanilla(self.van)
        self.assertEqual((r["adopted"][0]["height"], r["adopted"][0]["zones"]), (None, []))

    def test_refuses_edits_it_cannot_reproduce_and_leaves_no_trace(self):
        self.hand_edit()
        rd = RaceData(rd_(os.path.join(self.eq, RACEDATA)))
        lines = rd.tobytes().split(b"\n")
        f = lines[1].split(b"^"); f[10] = b"999"; lines[1] = b"^".join(f)          # an unrelated field edit
        wr_(os.path.join(self.eq, RACEDATA), b"\n".join(lines))
        sw = self.swapper()
        with self.assertRaises(SwapError) as cm:
            sw.adopt_from_vanilla(self.van)
        self.assertIn("byte for byte", str(cm.exception))
        self.assertEqual(sw.decisions(), [])
        self.assertEqual(os.listdir(os.path.join(self.vault, "originals")), [])
        self.assertEqual(sw.m["files"], {})

    def test_nothing_to_adopt_and_not_into_a_used_vault(self):
        sw = self.swapper()
        self.assertEqual(sw.adopt_from_vanilla(self.van), {"adopted": [], "files": []})
        sw.add_swap(95, "cth", ["potimeb"])
        with self.assertRaises(SwapError):
            sw.adopt_from_vanilla(self.van)

    def test_cli_guard_blocks_apply_and_swap_until_adopted(self):
        self.hand_edit()
        def call(*args):
            o, e = io.StringIO(), io.StringIO()
            with redirect_stdout(o), redirect_stderr(e):
                code = cli.main(["--eq", self.eq, "--vault", self.vault, "--index", self.idx,
                                 "--vanilla", self.van, "--json", *args])
            return code, json.loads(o.getvalue())
        code, r = call("info")
        self.assertEqual(r["data"]["unmanaged_edits"], ["potimeb_chr.txt", RACEDATA])
        code, r = call("apply")
        self.assertEqual(code, 2); self.assertIn("adopt", r["error"])
        code, r = call("swap", "54", "cth", "--zones", "gfaydark")
        self.assertEqual(code, 2)
        code, r = call("adopt")
        self.assertEqual(code, 0); self.assertEqual(r["data"]["adopted"][0]["to"], "CTH")
        code, r = call("info"); self.assertEqual(r["data"]["unmanaged_edits"], [])
        code, r = call("apply"); self.assertEqual(code, 0)


@unittest.skipUnless(os.path.exists(os.path.join(VANILLA, "racedata.txt")) and os.path.exists(os.path.join(LIVE, "racedata.txt"))
                     and os.path.exists(REPO_INDEX), "real install / vanilla backups not available")
class TestAdoptRealInstall(unittest.TestCase):
    def test_adopts_the_real_potimeb_cazic_edit_into_a_scratch_copy(self):
        tmp = tempfile.mkdtemp()
        try:
            eq = os.path.join(tmp, "EQ"); os.makedirs(os.path.join(eq, "Resources"))
            for f in ("racedata.txt", "potimeb_chr.txt", "gfaydark_chr.txt"):
                shutil.copy(os.path.join(LIVE, f), os.path.join(eq, f))           # the LIVE (edited) files
            shutil.copy(os.path.join(VANILLA, "GlobalLoad.txt"), os.path.join(eq, "Resources", "GlobalLoad.txt"))
            sw = Swapper(eq, os.path.join(tmp, "vault"), ModelIndex(REPO_INDEX, eq))
            r = sw.adopt_from_vanilla(VANILLA)
            self.assertEqual(r["adopted"], [{"race": 95, "from": "CAZ", "to": "CTH", "zones": ["potimeb"], "height": "6"}])
            self.assertEqual(sorted(r["files"]), ["potimeb_chr.txt", "racedata.txt"])
            sw.restore_all()
            for f in ("racedata.txt", "potimeb_chr.txt"):
                self.assertEqual(rd_(os.path.join(eq, f)), rd_(os.path.join(VANILLA, f)), f)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
