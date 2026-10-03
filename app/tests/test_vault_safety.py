"""Vault locking, restore-after-patch, ambiguous ids and failed writes."""
import os
from unittest import mock

from tweeq.engine import RACEDATA, SwapError, Swapper, VaultBusy, sha
from tests.test_tweeq import Base, rd_, wr_


class TestVaultLock(Base):
    def test_a_second_process_cannot_change_the_vault_while_one_is_working(self):
        other = Swapper(self.eq, self.vault, self.sw.models)
        other.LOCK_WAIT = 0
        with self.sw._exclusive():                       # e.g. a minutes-long enhance build
            with self.assertRaises(VaultBusy):
                other.add_swap(54, "cth", ["potimeb"])
            with self.assertRaises(VaultBusy):
                other.set_all_enabled(False)
            with self.assertRaises(VaultBusy):
                other.apply()
            self.assertEqual(len(other.decisions()), 0)  # reads still work
        other.add_swap(54, "cth", ["potimeb"])           # free again afterwards

    def test_the_lock_is_re_entrant_and_released_after_an_error(self):
        with self.assertRaises(SwapError):
            self.sw.add_swap(999, "cth", ["potimeb"])    # fails inside the lock
        self.sw.add_swap(54, "cth", ["potimeb"])
        self.sw.apply()                                  # apply -> status -> ... must not self-deadlock

    def test_a_stale_view_is_reloaded_so_nothing_is_overwritten(self):
        a = Swapper(self.eq, self.vault, self.sw.models)
        b = Swapper(self.eq, self.vault, self.sw.models)
        a.add_swap(54, "cth", ["potimeb"])
        d = b.add_swap(95, "ork", ["potimeb"])           # b was created before a's swap
        self.assertEqual(len(Swapper(self.eq, self.vault, None).decisions()), 2)
        self.assertEqual(d["race"], 95)


class TestRestoreAfterPatch(Base):
    def test_restore_does_not_put_an_old_file_over_a_patched_one(self):
        self.sw.add_swap(54, "cth", ["potimeb"])
        self.sw.apply()
        patched = rd_(os.path.join(self.eq, RACEDATA)) + b"new line from a patch\r\n"
        wr_(os.path.join(self.eq, RACEDATA), patched)
        r = self.sw.restore_all()
        self.assertEqual(r["drifted"], [RACEDATA])
        self.assertEqual(rd_(os.path.join(self.eq, RACEDATA)), patched)
        r = self.sw.restore_all(force=True)
        self.assertEqual(r["drifted"], [])
        self.assertNotEqual(rd_(os.path.join(self.eq, RACEDATA)), patched)

    def test_plain_restore_still_restores(self):
        self.sw.add_swap(54, "cth", ["potimeb"])
        orig = rd_(os.path.join(self.eq, RACEDATA))
        self.sw.apply()
        self.assertIn(RACEDATA, self.sw.restore_all()["restored"])
        self.assertEqual(rd_(os.path.join(self.eq, RACEDATA)), orig)


class TestIdPrefixes(Base):
    def test_empty_and_ambiguous_prefixes_touch_nothing(self):
        d1 = self.sw.add_swap(54, "cth", ["potimeb"])
        d2 = self.sw.add_swap(95, "ork", ["potimeb"])
        for bad in ("", "  "):
            with self.assertRaises(SwapError):
                self.sw.remove_swap(bad)
            with self.assertRaises(SwapError):
                self.sw.set_enabled(bad, False)
        self.assertEqual(len(self.sw.decisions()), 2)
        common = os.path.commonprefix([d1["id"], d2["id"]])
        if common:                                       # (uuids rarely share a first character)
            with self.assertRaises(SwapError):
                self.sw.remove_swap(common)
        self.sw.remove_swap(d1["id"][:12])
        self.assertEqual([d["id"] for d in self.sw.decisions()], [d2["id"]])


class TestWriteFailure(Base):
    def test_a_refused_write_is_a_clear_error_and_the_manifest_stays_truthful(self):
        self.sw.add_swap(54, "cth", ["potimeb"])
        real = __import__("tweeq.engine", fromlist=["_atomic_write"])._atomic_write
        calls = []

        def flaky(path, data):
            calls.append(path)
            if path.endswith("potimeb_chr.txt") and "originals" not in path:
                raise PermissionError(13, "Access is denied")
            return real(path, data)
        with mock.patch("tweeq.engine._atomic_write", flaky):
            with self.assertRaises(SwapError) as c:
                self.sw.apply()
        self.assertIn("EverQuest", str(c.exception))
        m = Swapper(self.eq, self.vault, None).m
        for rel, rec in m["files"].items():              # every recorded file really is what we wrote
            self.assertEqual(sha(rd_(os.path.join(self.eq, rel))), rec["applied_sha"])
        self.sw.apply()                                  # a retry completes
