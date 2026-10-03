import json
import os
import shutil
import tempfile
import unittest

from eqswap.engine import RACEDATA, Swapper, SwapError, sha
from eqswap.models import ModelIndex
from eqswap.racedata import RaceData
from eqswap.zonelist import ZoneList

LIVE = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"
VANILLA = "/mnt/c/Users/brian/EQ_Launcher/backups/vanilla"
REPO_INDEX = os.path.join(os.path.dirname(__file__), "..", "..", "data", "installed_model_index.json")


def rd_(path):
    with open(path, "rb") as f:
        return f.read()


def wr_(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)


def row(race, gender, tag, size="6"):
    f = ["0"] * 58
    f[0], f[1], f[46], f[47], f[50] = str(race), str(gender), size, size, tag
    f[57] = "\r"
    return "^".join(f).encode()


def make_install(root):
    os.makedirs(os.path.join(root, "Resources"), exist_ok=True)
    rd = b"\n".join([b"junk header line\r", row(95, 2, "CAZ", "11.5"), row(54, 2, "ORC"),
                     row(11, 0, "HOM"), row(11, 1, "HOF"), b"short\r", b""])
    wr_(os.path.join(root, RACEDATA), rd)
    wr_(os.path.join(root, "potimeb_chr.txt"), b"2\r\nwrb,wrb_chr\r\ndzr,dzr\r\n")
    wr_(os.path.join(root, "gfaydark_chr.txt"), b"1\r\norc,orc_chr\r\n")
    wr_(os.path.join(root, "Resources", "GlobalLoad.txt"),
        b"4,0,TFFFC,orc_chr,Loading Orcs\r\n1,1,TFFFC,global_chr,Loading Global\r\n")


def make_index(path):
    models = [
        {"tag": "cth", "format": "eqg_mds", "container": "cth.eqg"},
        {"tag": "ork", "format": "eqg_mds", "container": "ork.eqg"},
        {"tag": "wrb", "format": "wld_skinned", "container": "wrb_chr.s3d"},
        {"tag": "orc", "format": "wld_skinned", "container": "orc_chr.s3d"},
        {"tag": "caz", "format": "wld_skinned", "container": "caz_chr.s3d"},
        {"tag": "hum", "format": "wld_skinned", "container": "global_chr.s3d"},
        {"tag": "hum", "format": "wld_skinned", "container": "globalhum_chr.s3d"},
    ]
    wr_(path, json.dumps({"models": models}).encode())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EverQuest")
        self.vault = os.path.join(self.tmp, "vault")
        make_install(self.eq)
        self.idx_path = os.path.join(self.tmp, "idx.json")
        make_index(self.idx_path)
        self.sw = Swapper(self.eq, self.vault, ModelIndex(self.idx_path, self.eq))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def live(self, rel):
        return rd_(os.path.join(self.eq, rel))


class TestFormats(unittest.TestCase):
    def test_racedata_roundtrip_and_edit(self):
        raw = b"\n".join([b"hdr\r", row(95, 2, "CAZ", "11.5"), b"x\r", b""])
        rd = RaceData(raw)
        self.assertEqual(rd.tobytes(), raw)
        self.assertEqual(rd.get_tag(95, 2), "CAZ")
        rd.set_tag(95, 2, "cth")
        rd.set_size(95, 2, "6")
        out = RaceData(rd.tobytes())
        self.assertEqual((out.get_tag(95, 2), out.get_size(95, 2)), ("CTH", ("6", "6")))
        # only the three fields differ
        a, b = raw.split(b"\n")[1].split(b"^"), rd.tobytes().split(b"\n")[1].split(b"^")
        self.assertEqual([i for i in range(58) if a[i] != b[i]], [46, 47, 50])

    def test_racedata_rejects_bad_input(self):
        rd = RaceData(row(1, 0, "HUM"))
        with self.assertRaises(KeyError):
            rd.set_tag(2, 0, "ORC")
        with self.assertRaises(ValueError):
            rd.set_tag(1, 0, "bad tag!")
        with self.assertRaises(ValueError):
            rd.set_size(1, 0, "tall")

    def test_zonelist(self):
        raw = b"2\r\nwrb,wrb_chr\r\ndzr,dzr\r\n"
        z = ZoneList(raw)
        self.assertEqual(z.tobytes(), raw)
        self.assertFalse(z.ensure("dzr", "dzr"))
        self.assertTrue(z.ensure("CTH", "cth"))
        self.assertTrue(z.ensure("wrb", "wrb"))           # replace existing entry
        self.assertEqual(z.tobytes(), b"3\r\nwrb,wrb\r\ndzr,dzr\r\ncth,cth\r\n")


class TestModels(Base):
    def test_list_archive_rules(self):
        m = self.sw.models
        self.assertEqual(m.resolve("cth").list_archive, "cth")        # EQG: tag,tag
        self.assertEqual(m.resolve("wrb").list_archive, "wrb_chr")    # WLD: tag,tag_chr
        self.assertIsNone(m.resolve("hum").list_archive)              # shared/global archive
        self.assertIsNone(m.resolve("orc").list_archive)              # orc_chr is in GlobalLoad.txt
        with self.assertRaises(KeyError):
            m.resolve("nope")


class TestSwap(Base):
    def test_apply_edits_only_what_is_needed(self):
        d = self.sw.add_swap(95, "cth", ["potimeb"], height="6")
        self.assertEqual(d["originals"]["2"]["tag"], "CAZ")
        self.sw.apply()
        rd = RaceData(self.live(RACEDATA))
        self.assertEqual((rd.get_tag(95, 2), rd.get_size(95, 2)), ("CTH", ("6", "6")))
        self.assertEqual(rd.get_tag(54, 2), "ORC")                     # others untouched
        self.assertEqual(self.live("potimeb_chr.txt"),
                         b"3\r\nwrb,wrb_chr\r\ndzr,dzr\r\ncth,cth\r\n")
        self.assertEqual(self.live("gfaydark_chr.txt"), b"1\r\norc,orc_chr\r\n")
        self.assertTrue(all(r.state in ("applied", "clean") for r in self.sw.status()))

    def test_apply_is_idempotent_and_restore_is_exact(self):
        orig = {r: self.live(r) for r in (RACEDATA, "potimeb_chr.txt")}
        self.sw.add_swap(95, "cth", ["potimeb"], height="6")
        self.sw.apply(); first = self.live(RACEDATA)
        self.sw.apply()
        self.assertEqual(self.live(RACEDATA), first)
        self.sw.restore_all()
        for r, b in orig.items():
            self.assertEqual(self.live(r), b, r)

    def test_removing_a_decision_restores_files_on_apply(self):
        orig = self.live(RACEDATA)
        d = self.sw.add_swap(95, "cth", ["potimeb"])
        self.sw.apply()
        self.sw.remove_swap(d["id"][:8])
        self.sw.apply()
        self.assertEqual(self.live(RACEDATA), orig)
        self.assertEqual(self.live("potimeb_chr.txt"), b"2\r\nwrb,wrb_chr\r\ndzr,dzr\r\n")

    def test_global_target_needs_no_list_line(self):
        self.sw.add_swap(11, "hum", ["potimeb"])
        self.sw.apply()
        self.assertEqual(self.live("potimeb_chr.txt"), b"2\r\nwrb,wrb_chr\r\ndzr,dzr\r\n")
        rd = RaceData(self.live(RACEDATA))
        self.assertEqual([rd.get_tag(11, 0), rd.get_tag(11, 1)], ["HUM", "HUM"])

    def test_conflicts_and_bad_input(self):
        self.sw.add_swap(95, "cth", ["potimeb"])
        with self.assertRaises(SwapError):
            self.sw.add_swap(95, "ork", ["potimeb"])                   # same race twice
        with self.assertRaises(SwapError):
            self.sw.add_swap(999, "cth", ["potimeb"])                  # no such race
        with self.assertRaises(KeyError):
            self.sw.add_swap(54, "nope", ["potimeb"])                  # unknown model
        with self.assertRaises(SwapError):
            self.sw.add_swap(54, "cth", ["nozone"])                    # no such zone list

    def test_survives_patch_that_restores_vanilla(self):
        self.sw.add_swap(95, "cth", ["potimeb"], height="6")
        self.sw.apply(); applied = self.live(RACEDATA)
        shutil.copy(os.path.join(self.vault, "originals", RACEDATA), os.path.join(self.eq, RACEDATA))
        self.assertIn("needs-apply", [r.state for r in self.sw.status()])
        self.sw.apply()                                                # "Apply All" after patch
        self.assertEqual(self.live(RACEDATA), applied)

    def test_patch_that_changes_other_lines_is_adopted_not_clobbered(self):
        self.sw.add_swap(95, "cth", ["potimeb"], height="6")
        self.sw.apply()
        # a patch ships a new racedata (unrelated row added) and wipes our edit
        patched = self.live(RACEDATA).replace(b"junk header line", b"junk header line v2")
        patched = RaceData(patched); patched.set_tag(95, 2, "CAZ"); patched.set_size(95, 2, "11.5")
        wr_(os.path.join(self.eq, RACEDATA), patched.tobytes())
        self.assertIn("drifted", [r.state for r in self.sw.status()])
        with self.assertRaises(SwapError):
            self.sw.apply()                                            # refuses silently overwriting
        self.sw.apply(accept_drift=True)
        out = self.live(RACEDATA)
        self.assertIn(b"junk header line v2", out)                     # patch content kept
        self.assertEqual(RaceData(out).get_tag(95, 2), "CTH")          # swap re-applied
        self.sw.restore_all()                                          # restore = NEW vanilla
        self.assertIn(b"junk header line v2", self.live(RACEDATA))
        self.assertEqual(RaceData(self.live(RACEDATA)).get_tag(95, 2), "CAZ")

    def test_set_all_enabled_then_apply_restores_originals(self):
        orig = {r: self.live(r) for r in (RACEDATA, "potimeb_chr.txt")}
        self.sw.add_swap(95, "cth", ["potimeb"], height="6")
        self.sw.add_swap(54, "ork", ["gfaydark"])
        self.sw.apply()
        self.assertEqual(self.sw.set_all_enabled(False), 2)
        self.assertTrue(all(not d["enabled"] for d in self.sw.decisions()))
        self.sw.apply()
        for r, b in orig.items():
            self.assertEqual(self.live(r), b, r)
        self.assertEqual(len(self.sw.decisions()), 2)          # still listed
        self.sw.set_all_enabled(True); self.sw.apply()
        self.assertEqual(RaceData(self.live(RACEDATA)).get_tag(95, 2), "CTH")

    def test_all_zones_swap_covers_every_zone_list_and_future_zones(self):
        d = self.sw.add_swap(95, "ork", "all")
        self.assertEqual((d["zones"], d["all_zones"], d["list_archive"]), ([], True, "ork"))
        self.sw.apply()
        for z in ("potimeb", "gfaydark"):
            self.assertEqual(ZoneList(self.live(f"{z}_chr.txt")).get("ork"), "ork", z)
        # a game patch adds a new zone: the swap picks it up on the next apply, and restore removes every line
        wr_(os.path.join(self.eq, "newzone_chr.txt"), b"1\r\nwrb,wrb_chr\r\n")
        self.assertIn(("newzone_chr.txt", "needs-apply"), [(r.path, r.state) for r in self.sw.status()])
        self.sw.apply()
        self.assertEqual(ZoneList(self.live("newzone_chr.txt")).get("ork"), "ork")
        self.sw.restore_all()
        self.assertIsNone(ZoneList(self.live("potimeb_chr.txt")).get("ork"))

    def test_manifest_persists(self):
        self.sw.add_swap(95, "cth", ["potimeb"])
        again = Swapper(self.eq, self.vault, ModelIndex(self.idx_path, self.eq))
        self.assertEqual(len(again.decisions()), 1)

    def test_disable_reverts_on_apply(self):
        orig = self.live(RACEDATA)
        d = self.sw.add_swap(95, "cth", ["potimeb"])
        self.sw.apply()
        self.sw.set_enabled(d["id"], False)
        self.sw.apply()
        self.assertEqual(self.live(RACEDATA), orig)


@unittest.skipUnless(os.path.exists(os.path.join(VANILLA, "racedata.txt")) and os.path.exists(REPO_INDEX),
                     "real EQ install / vanilla backups not available")
class TestAgainstRealFiles(unittest.TestCase):
    """Replay the PoTime Cazic swap from VANILLA backups and compare to what is live."""

    def test_reproduces_the_live_potimeb_cazic_edit_byte_for_byte(self):
        tmp = tempfile.mkdtemp()
        try:
            eq = os.path.join(tmp, "EQ")
            os.makedirs(os.path.join(eq, "Resources"))
            for f in ("racedata.txt", "potimeb_chr.txt"):
                shutil.copy(os.path.join(VANILLA, f), os.path.join(eq, f))
            shutil.copy(os.path.join(VANILLA, "GlobalLoad.txt"), os.path.join(eq, "Resources", "GlobalLoad.txt"))
            sw = Swapper(eq, os.path.join(tmp, "vault"), ModelIndex(REPO_INDEX, eq))
            d = sw.add_swap(95, "cth", ["potimeb"], height="6")
            self.assertEqual(d["list_archive"], "cth")
            sw.apply()
            for f in ("racedata.txt", "potimeb_chr.txt"):
                a, b = rd_(os.path.join(eq, f)), rd_(os.path.join(LIVE, f))
                self.assertEqual(sha(a), sha(b), f"{f} differs from the hand-applied live edit")
            sw.restore_all()
            for f in ("racedata.txt", "potimeb_chr.txt"):
                self.assertEqual(rd_(os.path.join(eq, f)), rd_(os.path.join(VANILLA, f)))
        finally:
            shutil.rmtree(tmp)

    def test_racedata_roundtrips_real_file(self):
        raw = rd_(os.path.join(VANILLA, "racedata.txt"))
        self.assertEqual(RaceData(raw).tobytes(), raw)
        self.assertEqual(RaceData(raw).get_tag(54, 2), "ORC")


if __name__ == "__main__":
    unittest.main(verbosity=2)
