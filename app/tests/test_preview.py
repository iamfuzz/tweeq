import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest import mock

from eqswap import preview
from eqswap.models import ModelIndex
from eqswap.peq import npc_groups_in_zone
from eqswap.racedata import RaceData
from tests.test_eqswap import make_index, make_install, row, wr_

LIVE = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"
REPO_INDEX = os.path.join(os.path.dirname(__file__), "..", "..", "data", "installed_model_index.json")


class TestPreviewLogic(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EQ")
        make_install(self.eq)
        for n, size in (("cth.eqg", 10), ("orc_chr.s3d", 10), ("global_chr.s3d", 1000), ("globalhum_chr.s3d", 50)):
            wr_(os.path.join(self.eq, n), b"x" * size)
        self.idx_path = os.path.join(self.tmp, "idx.json")
        make_index(self.idx_path)
        self.idx = ModelIndex(self.idx_path, self.eq)
        self.out = os.path.join(self.tmp, "prev")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fake_run(self, cmd):
        wr_([a for a in cmd if a.endswith(".part.glb")][0], b"glTF")
        return True, ""

    def test_smallest_archive_is_chosen_and_result_cached(self):
        with mock.patch.object(preview, "_run", side_effect=self.fake_run) as run:
            r = preview.ensure_preview(self.eq, self.idx, "hum", self.out)
            self.assertEqual(r["status"], "ok")
            self.assertIn("globalhum_chr.s3d", " ".join(run.call_args[0][0]))   # not the huge global_chr
            r2 = preview.ensure_preview(self.eq, self.idx, "hum", self.out)
            self.assertEqual(r2["status"], "cached")
            self.assertEqual(run.call_count, 1)

    def test_cache_invalidates_when_the_archive_changes(self):
        with mock.patch.object(preview, "_run", side_effect=self.fake_run) as run:
            preview.ensure_preview(self.eq, self.idx, "orc", self.out)
            wr_(os.path.join(self.eq, "orc_chr.s3d"), b"changed by a patch!")
            self.assertEqual(preview.ensure_preview(self.eq, self.idx, "orc", self.out)["status"], "ok")
            self.assertEqual(run.call_count, 2)

    def test_failure_leaves_no_partial_file(self):
        with mock.patch.object(preview, "_run", return_value=(False, "boom")):
            r = preview.ensure_preview(self.eq, self.idx, "orc", self.out)
        self.assertEqual((r["status"], r["error"]), ("error", "boom"))
        self.assertEqual([f for f in os.listdir(self.out)], [])

    def test_unknown_and_unsupported(self):
        self.assertEqual(preview.ensure_preview(self.eq, self.idx, "nope", self.out)["status"], "error")
        self.idx.by_tag["prop"] = [{"tag": "prop", "format": "eqg_mod", "container": "cth.eqg"}]
        self.assertEqual(preview.ensure_preview(self.eq, self.idx, "prop", self.out)["status"], "unsupported")


class TestZoneNpcs(unittest.TestCase):
    def test_groups_and_racedata_fallback(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "p.sqlite")
            with closing(sqlite3.connect(p)) as db:
                db.executescript("""create table npc_types(id,name,race,gender,level,texture);
                    create table spawn2(id,spawngroupID,zone,version,x,y,z,heading);
                    create table spawnentry(spawngroupID,npcID,chance);
                    insert into npc_types values (1,'a_orc_pawn',54,2,5,0),(2,'#Orc_Chief',54,2,20,1),(3,'Bob',1,0,10,0);
                    insert into spawn2 values (1,10,'GFaydark',0,0,0,0,0),(2,11,'gfaydark',0,0,0,0,0),(3,12,'gfaydark',0,0,0,0,0),(4,13,'gfaydark',3,0,0,0,0);
                    insert into spawnentry values (10,1,100),(11,2,100),(12,3,100),(13,3,100);""")
                db.commit()
            g = npc_groups_in_zone(p, "gfaydark")
            self.assertEqual([(x["race"], x["npcs"], x["example"]) for x in g], [(54, 2, "Orc Chief"), (1, 1, "Bob")])
            rd = RaceData(b"\n".join([row(54, 2, "ORC"), row(1, 0, "HUM"), row(1, 1, "HUF"), b""]))
            self.assertEqual(rd.tag_for(54, 0), "ORC")     # asked for gender 0, only neutral row exists
            self.assertEqual(rd.tag_for(1, 1), "HUF")
            self.assertEqual(rd.tag_for(1, 2), "HUM")      # no neutral row -> first row of the race
            self.assertIsNone(rd.tag_for(999, 0))
        finally:
            shutil.rmtree(tmp)


def _quail():
    return os.path.exists(os.path.expanduser("~/go/bin/quail"))


@unittest.skipUnless(os.path.exists(LIVE) and os.path.exists(REPO_INDEX) and _quail(),
                     "real install / quail not available")
class TestRealConversion(unittest.TestCase):
    def test_real_wld_and_eqg_models_convert_to_valid_glb(self):
        out = tempfile.mkdtemp()
        try:
            idx = ModelIndex(REPO_INDEX, LIVE)
            for tag, fmt in (("orc", "wld_skinned"), ("ork", "eqg_mds")):
                r = preview.ensure_preview(LIVE, idx, tag, out)
                self.assertEqual(r["status"], "ok", r)
                self.assertEqual(r["format"], fmt)
                with open(r["path"], "rb") as f:
                    self.assertEqual(f.read(4), b"glTF")
                self.assertGreater(os.path.getsize(r["path"]), 10_000)
            self.assertEqual(preview.ensure_preview(LIVE, idx, "orc", out)["status"], "cached")
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
