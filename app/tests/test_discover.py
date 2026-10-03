import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from tweeq import cli
from tweeq.discover import discover, is_eq_dir, missing_files, parse_reg_output, to_display, to_native
from tweeq.engine import Swapper, SwapError, norm_dir
from tests.test_tweeq import LIVE, make_install, wr_


def fake_install(path):
    os.makedirs(path, exist_ok=True)
    wr_(os.path.join(path, "eqgame.exe"), b"MZ")
    wr_(os.path.join(path, "racedata.txt"), b"x")


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d)

    def find(self, **kw):
        return discover(drive_list=[self.d], use_registry=False, use_process=False, env={}, **kw)

    def test_is_install_needs_both_files_case_insensitively(self):
        p = os.path.join(self.d, "eq"); os.makedirs(p)
        self.assertEqual(sorted(missing_files(p)), ["eqgame.exe", "racedata.txt"])
        wr_(os.path.join(p, "EQGame.EXE"), b"x")
        self.assertEqual(missing_files(p), ["racedata.txt"])
        wr_(os.path.join(p, "RaceData.txt"), b"x")
        self.assertTrue(is_eq_dir(p))
        self.assertEqual(missing_files(os.path.join(self.d, "nope")), ["eqgame.exe", "racedata.txt"])

    def test_finds_the_standard_daybreak_location(self):
        fake_install(os.path.join(self.d, "Users/Public/Daybreak Game Company/Installed Games/EverQuest"))
        r = self.find()
        self.assertEqual([x["source"] for x in r], ["standard location"])
        self.assertTrue(r[0]["path"].endswith(os.path.join("Installed Games", "EverQuest")))

    def test_finds_installs_on_drive_root_program_files_and_test_server_copies(self):
        fake_install(os.path.join(self.d, "EverQuest"))
        fake_install(os.path.join(self.d, "Program Files (x86)", "EverQuest Test"))
        fake_install(os.path.join(self.d, "Games", "EverQuest"))
        got = sorted(os.path.relpath(x["path"], self.d) for x in self.find())
        self.assertEqual(got, ["EverQuest", os.path.join("Games", "EverQuest"), os.path.join("Program Files (x86)", "EverQuest Test")])

    def test_ignores_folders_that_only_look_like_installs(self):
        os.makedirs(os.path.join(self.d, "EverQuest-notes"))                       # empty
        p = os.path.join(self.d, "EverQuest"); os.makedirs(p); wr_(os.path.join(p, "eqgame.exe"), b"x")   # no racedata
        self.assertEqual(self.find(), [])

    def test_env_override_dedup_and_order(self):
        p = os.path.join(self.d, "EverQuest"); fake_install(p)
        r = discover(drive_list=[self.d], use_registry=False, use_process=False, env={"EQ_DIR": p})
        self.assertEqual(len(r), 1)                                                # same folder found twice -> once
        self.assertEqual(r[0]["source"], "EQ_DIR environment variable")             # best source wins

    def test_registry_parser(self):
        text = ('HKEY_LOCAL_MACHINE\\SOFTWARE\\X\\EverQuest\n'
                '    InstallLocation    REG_SZ    D:\\Games\\EverQuest\\\n'
                '    UninstallString    REG_SZ    "C:\\Games\\EQ2\\uninstall.exe" /S\n'
                '    NSIS:Language    REG_SZ    1033\n')
        self.assertEqual(parse_reg_output(text), ["D:\\Games\\EverQuest\\", "C:\\Games\\EQ2"])

    def test_path_conversions(self):
        if os.name == "nt":      # natively a Windows path stays as it is, and a WSL-style path becomes a Windows one
            self.assertEqual(to_native("C:\\Users\\Public\\Daybreak Game Company"), "C:\\Users\\Public\\Daybreak Game Company")
            self.assertEqual(to_native("/mnt/c/x"), "C:\\x")
            self.assertEqual(to_native("/mnt/d/Games/Everquest folder"), "D:\\Games\\Everquest folder")
        else:
            self.assertEqual(to_native("C:\\Users\\Public\\Daybreak Game Company"), "/mnt/c/Users/Public/Daybreak Game Company")
            self.assertEqual(to_native("/mnt/c/x"), "/mnt/c/x")
        self.assertEqual(to_display("/mnt/d/Games/EverQuest"), "D:\\Games\\EverQuest")
        self.assertEqual(norm_dir("C:\\Foo\\Bar\\"), norm_dir("/mnt/c/foo/bar"))

    def test_cli_discover_and_check(self):
        fake_install(os.path.join(self.d, "EverQuest"))
        o, e = io.StringIO(), io.StringIO()
        with redirect_stdout(o), redirect_stderr(e):
            code = cli.main(["--json", "discover", "--check", os.path.join(self.d, "EverQuest")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(o.getvalue())["data"]["valid"], True)
        o2 = io.StringIO()
        with redirect_stdout(o2), redirect_stderr(e):
            cli.main(["--json", "discover", "--check", self.d])
        d = json.loads(o2.getvalue())["data"]
        self.assertEqual((d["valid"], sorted(d["missing"])), (False, ["eqgame.exe", "racedata.txt"]))


class TestVaultBelongsToOneInstall(unittest.TestCase):
    def test_opening_a_vault_against_another_install_is_refused(self):
        tmp = tempfile.mkdtemp()
        try:
            a, b = os.path.join(tmp, "A"), os.path.join(tmp, "B")
            make_install(a); make_install(b)
            vault = os.path.join(tmp, "vault")
            sw = Swapper(a, vault); sw.add_swap(95, "cth", ["potimeb"])
            Swapper(a, vault)                                    # same install: fine
            with self.assertRaises(SwapError) as cm:
                Swapper(b, vault)
            self.assertIn("different install", str(cm.exception))
            # a legacy manifest without the stamp is accepted and stamped on the next save
            mp = os.path.join(vault, "manifest.json")
            with open(mp) as f:
                m = json.load(f)
            del m["eq_dir"]
            with open(mp, "w") as f:
                json.dump(m, f)
            sw2 = Swapper(b, vault); sw2.set_enabled(sw2.decisions()[0]["id"], False)
            with open(mp) as f:
                self.assertEqual(json.load(f)["eq_dir"], b)
        finally:
            shutil.rmtree(tmp)


@unittest.skipUnless(os.path.exists(LIVE), "real install not available")
class TestRealDiscovery(unittest.TestCase):
    def test_finds_this_machines_install(self):
        r = discover()
        self.assertTrue(any(os.path.normpath(x["path"]).lower() == os.path.normpath(LIVE).lower() for x in r),
                        [x["path"] for x in r])
        self.assertTrue(all(is_eq_dir(x["path"]) for x in r))


if __name__ == "__main__":
    unittest.main()
