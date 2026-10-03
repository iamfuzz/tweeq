"""Scan: PFS directory listing, the install fingerprint, and the one-pass index + compat build."""
import json
import os
import shutil
import struct
import tempfile
import unittest
import zlib

from tweeq import enhance as E  # noqa: F401  (puts tools/ on sys.path)
from tweeq import scan
import s3d  # noqa: E402
from tests.test_tweeq import VANILLA, rd_, wr_  # noqa: E402

CTH = os.path.join(VANILLA, "cth.eqg")
REPO = os.path.join(os.path.dirname(__file__), "..", "..")
LIVE = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"


def jl(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def jw(path, doc):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def make_pfs(files: dict[str, bytes], block: int = 64) -> bytes:
    """A small PFS archive: members in small zlib blocks, a filename list, and a directory."""
    body, entries = bytearray(b"\0" * 12), []

    def put(data: bytes) -> int:
        off = len(body)
        for i in range(0, max(len(data), 1), block):
            chunk = data[i:i + block]
            z = zlib.compress(chunk)
            body.extend(struct.pack("<II", len(z), len(chunk)) + z)
        return off
    for i, (name, data) in enumerate(files.items()):
        entries.append((0x1000 + i, put(data), len(data)))
    names = struct.pack("<I", len(files)) + b"".join(struct.pack("<I", len(n) + 1) + n.encode() + b"\0" for n in files)
    entries.append((s3d.FILENAME_LIST_ID, put(names), len(names)))
    dir_off = len(body)
    body.extend(struct.pack("<I", len(entries)) + b"".join(struct.pack("<III", *e) for e in entries))
    body[0:12] = struct.pack("<I4sI", dir_off, b"PFS ", 0x20000)
    return bytes(body)


class Pfs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.files = {"a.mds": b"x" * 300, "b.ani": bytes(range(256)) * 3, "c.dds": b"", "d.txt": b"hello"}
        self.p = os.path.join(self.tmp, "t.eqg")
        wr_(self.p, make_pfs(self.files))

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_list_members_gives_names_and_sizes_without_reading_members(self):
        self.assertEqual(s3d.list_members(self.p), {k: len(v) for k, v in self.files.items()})

    def test_load_with_a_filter_matches_load_everything(self):
        full = s3d.load(self.p)
        self.assertEqual(full, self.files)
        part = s3d.load(self.p, want=lambda n: n.endswith((".mds", ".ani")))
        self.assertEqual(part, {k: v for k, v in self.files.items() if k.endswith((".mds", ".ani"))})

    def test_bad_files_are_refused(self):
        wr_(os.path.join(self.tmp, "bad.eqg"), b"not a pfs archive at all")
        with self.assertRaises(ValueError):
            s3d.list_members(os.path.join(self.tmp, "bad.eqg"))


class Fingerprint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        for n in ("a.eqg", "b_chr.s3d", "zone.s3d", "racedata.txt"):
            wr_(os.path.join(self.tmp, n), b"1234")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_only_model_archives_count_and_any_change_is_noticed(self):
        names = [n for n, _ in scan.sources(self.tmp)]
        self.assertEqual(names, ["a.eqg", "b_chr.s3d"])                     # terrain archives and text files are ignored
        fp = scan.fingerprint(scan.sources(self.tmp))
        wr_(os.path.join(self.tmp, "racedata.txt"), b"changed")
        wr_(os.path.join(self.tmp, "zone.s3d"), b"changed")
        self.assertEqual(scan.fingerprint(scan.sources(self.tmp)), fp)
        wr_(os.path.join(self.tmp, "a.eqg"), b"12345")                      # size changed
        self.assertNotEqual(scan.fingerprint(scan.sources(self.tmp)), fp)

    def test_a_substituted_original_hides_the_enhanced_file(self):
        orig = os.path.join(self.tmp, "orig.bin")
        wr_(orig, b"1234")
        os.utime(orig, (1000, 1000))
        os.utime(os.path.join(self.tmp, "a.eqg"), (1000, 1000))
        base = lambda n: orig if n == "a.eqg" else None  # noqa: E731
        fp = scan.fingerprint(scan.sources(self.tmp, base))
        wr_(os.path.join(self.tmp, "a.eqg"), b"an enhanced, much bigger archive")
        self.assertEqual(scan.fingerprint(scan.sources(self.tmp, base)), fp)


@unittest.skipUnless(os.path.exists(CTH), "needs the vanilla cth.eqg backup")
class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EverQuest")
        os.makedirs(self.eq)
        shutil.copy(CTH, self.eq)
        self.out = os.path.join(self.tmp, "installs", "x")

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_scan_writes_index_compat_and_meta_and_status_follows(self):
        self.assertEqual(scan.status(self.eq, self.out)["reason"], "missing")
        seen = []
        r = scan.run(self.eq, self.out, progress=lambda s, p: seen.append((s, p)))
        self.assertEqual((r["models"], r["errors"]), (1, 0))
        self.assertEqual(seen[-1], ("done", 1.0))
        idx = jl(os.path.join(self.out, scan.INDEX_NAME))
        self.assertEqual([(m["tag"], m["format"], m["container"]) for m in idx["models"]], [("cth", "eqg_mds", "cth.eqg")])
        compat = jl(os.path.join(self.out, scan.COMPAT_NAME))["models"]["cth"]
        self.assertEqual(compat["bones"], 103)
        self.assertTrue(compat["extra_arms"])
        self.assertEqual(compat["weapon"]["primary_parent"], "ARMR_FARM")                      # the known CTH defect
        self.assertGreaterEqual(compat["weapon"]["tracks"]["ARMR_WEAP"]["over"], 9)
        self.assertEqual(scan.status(self.eq, self.out), {"fresh": True, "reason": "ok", "models": 1})
        shutil.copy(CTH, os.path.join(self.eq, "extra_chr.s3d"))
        self.assertEqual(scan.status(self.eq, self.out)["reason"], "changed")

    def test_a_new_scanner_version_means_rescan(self):
        scan.run(self.eq, self.out)
        meta_p = os.path.join(self.out, scan.META_NAME)
        meta = jl(meta_p)
        meta["scanner_version"] = 0
        jw(meta_p, meta)
        self.assertEqual(scan.status(self.eq, self.out)["reason"], "version")

    def test_cancel_writes_nothing(self):
        with self.assertRaises(scan.ScanCancelled):
            scan.run(self.eq, self.out, cancel=lambda: True)
        self.assertFalse(os.path.exists(self.out))

    def test_one_unreadable_archive_is_reported_not_fatal(self):
        wr_(os.path.join(self.eq, "broken.eqg"), b"garbage")
        r = scan.run(self.eq, self.out)
        self.assertEqual((r["models"], r["errors"]), (1, 1))


@unittest.skipUnless(os.environ.get("TWEEQ_SLOW_TESTS") and os.path.exists(os.path.join(LIVE, "racedata.txt")),
                     "slow: set TWEEQ_SLOW_TESTS=1 with a real install (read-only, ~2 minutes)")
class AgainstTheRealInstall(unittest.TestCase):
    def test_matches_the_dev_pipeline_exactly(self):
        out = tempfile.mkdtemp()
        try:
            scan.run(LIVE, out)
            new = jl(os.path.join(out, scan.INDEX_NAME))
            old = jl(os.path.join(REPO, "data", "installed_model_index.json"))
            key = lambda m: json.dumps(m, sort_keys=True)  # noqa: E731
            self.assertEqual(sorted(map(key, new["models"])), sorted(map(key, old["models"])))
            self.assertEqual(jl(os.path.join(out, scan.COMPAT_NAME)),
                             jl(os.path.join(REPO, "data", "model_compat.json")))
        finally:
            shutil.rmtree(out)


if __name__ == "__main__":
    unittest.main()
