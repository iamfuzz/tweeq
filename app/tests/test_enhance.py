"""Enhance: polygon subdivision, texture upscaling, and the engine/CLI lifecycle around them."""
import hashlib
import io
import json
import os
import shutil
import struct
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout

import numpy as np
from PIL import Image

from tweeq import enhance as E  # noqa: F401  (puts tools/ on sys.path)
from tweeq.cli import build_parser, main as cli_main
from tweeq.engine import Swapper, SwapError, sha
from tweeq.models import ModelIndex
import enhance_textures as et  # noqa: E402
import mds as mdsmod  # noqa: E402
import mds_subdivide as sd  # noqa: E402
from tests.test_tweeq import VANILLA, make_index, make_install, rd_, wr_  # noqa: E402

CTH = os.path.join(VANILLA, "cth.eqg")
HAVE_CTH = os.path.exists(CTH)
try:
    QUAIL = E.find_quail()
except E.EnhanceError:
    QUAIL = None
NEED = unittest.skipUnless(HAVE_CTH and QUAIL, "needs the vanilla cth.eqg backup and the quail tool")


def tri_model(nbones=2):
    m = mdsmod.Model(name="T")
    for i, (x, y) in enumerate(((0, 0), (4, 0), (0, 4))):
        v = mdsmod.Vertex(position=(x, y, 0.0), normal=(0, 0, 1), uv=(x / 4, y / 4), tint=(10 * i, 20, 30, 255))
        v.set_weights([(i % nbones, 1.0)])
        m.vertices.append(v)
    m.faces.append(mdsmod.Face((0, 1, 2), 0, 7))
    m.sync_counts()
    return m


def longest_edge(m):
    best = 0.0
    for f in m.faces:
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = m.vertices[f.indices[i]].position, m.vertices[f.indices[j]].position
            best = max(best, sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5)
    return best


class Subdivide(unittest.TestCase):
    def test_counts_and_attributes(self):
        m = tri_model()
        before = longest_edge(m)
        sd.subdivide_once(m)
        self.assertEqual((len(m.vertices), len(m.faces)), (6, 4))
        self.assertAlmostEqual(longest_edge(m), before / 2)
        self.assertEqual({f.flags for f in m.faces}, {7})                  # flags survive
        mid = m.vertices[3]                                                # midpoint of verts 0 and 1
        self.assertEqual(mid.position, (2.0, 0.0, 0.0))
        self.assertEqual(mid.tint, (5, 20, 30, 255))                       # tint interpolated
        w = mid.active_weights()
        self.assertAlmostEqual(sum(x for _, x in w), 1.0)
        self.assertEqual({b for b, _ in w}, {0, 1})                        # inherits both parents' bones
        sd.check_model(m, 2)

    def test_predict_matches_reality_and_limit_is_enforced(self):
        m = tri_model()
        for passes in (1, 2, 3):
            want = sd.predict(m, passes)
            mm = tri_model()
            for _ in range(passes):
                sd.subdivide_once(mm)
            self.assertEqual((len(mm.vertices), len(mm.faces)), (want["verts"], want["faces"]))
        big = tri_model()
        n = 0                       # a strip big enough that one pass would pass the vertex limit
        for k in range(20000):
            a = len(big.vertices)
            for p in ((k, 1), (k + 1, 1), (k, 2)):
                v = mdsmod.Vertex(position=(p[0], p[1], 0), normal=(0, 0, 1), uv=(0, 0))
                v.set_weights([(0, 1.0)])
                big.vertices.append(v)
            big.faces.append(mdsmod.Face((a, a + 1, a + 2), 0, 0))
        big.sync_counts()
        mds = mdsmod.Mds(models=[big], bones=[mdsmod.Bone("b", -1, 0, -1)])
        with self.assertRaises(sd.SubdivideError):
            sd.subdivide_mds(mds, 1)
        self.assertEqual(len(big.faces), 20001)                           # refused BEFORE changing anything

    def test_authored_normals_are_kept_so_seams_do_not_crease(self):
        # two vertices at the same position (a UV seam) with deliberately different authored normals
        m = tri_model()
        m.vertices[0].normal = (1.0, 0.0, 0.0)
        mds = mdsmod.Mds(models=[m], bones=[mdsmod.Bone("b", -1, 0, -1), mdsmod.Bone("c", 0, 0, -1)])
        sd.subdivide_mds(mds, 1)
        self.assertEqual(m.vertices[0].normal, (1.0, 0.0, 0.0))             # original vertex untouched
        self.assertEqual(m.vertices[3].normal[0] > 0.5, True)                # midpoint blends parents (0 and 1)

    def test_check_model_catches_damage(self):
        m = tri_model()
        m.faces[0].indices = (0, 1, 9)
        with self.assertRaises(sd.SubdivideError):
            sd.check_model(m, 2)
        m = tri_model()
        m.vertices[0].set_weights([(5, 1.0)])
        with self.assertRaises(sd.SubdivideError):
            sd.check_model(m, 2)


def gradient(size=128, alpha=255):
    a = np.zeros((size, size, 4), np.uint8)
    a[..., 0] = np.linspace(0, 255, size, dtype=np.uint8)[None, :]
    a[..., 1] = np.linspace(255, 0, size, dtype=np.uint8)[:, None]
    a[..., 2] = 90
    a[..., 3] = alpha
    return Image.fromarray(a, "RGBA")


class Textures(unittest.TestCase):
    def test_rgba32_roundtrip_is_exact_and_mips_follow_the_source(self):
        im = gradient()
        for mips in (False, True):
            d = et.write_dds(im, "RGBA32", mips)
            self.assertEqual(et.dds_kind(d), "RGBA32")
            self.assertEqual(et.dds_has_mips(d), mips)
            self.assertEqual(list(et.read_dds(d).getdata()), list(im.getdata()))
        self.assertEqual(len(et.write_dds(im, "RGBA32", False)), 128 + 128 * 128 * 4)

    def test_bleed_only_fills_fully_transparent_texels(self):
        rgb = np.zeros((8, 8, 3), np.uint8)
        rgb[...] = (200, 40, 40)
        rgb[4, 4] = (10, 250, 10)                  # a semi-transparent texel with its own real colour
        rgb[0, 0] = (0, 0, 0)                      # a fully transparent one (garbage colour)
        alpha = np.full((8, 8), 255, np.uint8)
        alpha[4, 4], alpha[0, 0] = 120, 0
        im = Image.fromarray(np.dstack([rgb, alpha]), "RGBA")
        out = et.bleed(np.asarray(im)[..., :3], np.asarray(im)[..., 3] > 0)
        self.assertEqual(tuple(out[4, 4]), (10, 250, 10))                   # colour kept
        self.assertEqual(tuple(out[0, 0]), (200, 40, 40))                   # hole filled from neighbours

    def test_dxt_is_reencoded_as_dxt(self):
        im = gradient()
        for kind in ("DXT1", "DXT5", "DXT3"):
            d = et.write_dds(im, kind, True)
            self.assertEqual(et.dds_kind(d), "DXT5" if kind == "DXT3" else kind)
            back = np.asarray(et.read_dds(d), float)[..., :3]
            self.assertLess(np.abs(back - np.asarray(im, float)[..., :3]).mean(), 6.0)

    def test_plan_skips_tiny_big_and_unreadable(self):
        files = {"a.dds": et.write_dds(gradient(128), "RGBA32", False),
                 "tiny.dds": et.write_dds(gradient(32), "RGBA32", False),
                 "big.dds": et.write_dds(gradient(128), "RGBA32", False),
                 "a_n.dds": et.write_dds(gradient(128), "RGBA32", False),
                 "weird.dds": b"DDS " + b"\0" * 200, "readme.txt": b"x"}
        acts = {r["name"]: r["action"] for r in et.plan_textures(files, 256)}
        self.assertEqual(acts, {"a.dds": "color", "a_n.dds": "normal", "big.dds": "color",
                                "tiny.dds": "keep", "weird.dds": "keep"})
        acts = {r["name"]: r["action"] for r in et.plan_textures(files, 128)}
        self.assertEqual(set(acts.values()), {"keep"})
        self.assertEqual({r["action"] for r in et.plan_textures(files, 0)}, {"keep"})

    def test_lanczos_enhance_size_alpha_and_normals(self):
        col = et.write_dds(gradient(128, alpha=0), "RGBA32", False)          # fully transparent colour map
        nrm = np.zeros((128, 128, 4), np.uint8)
        nrm[..., :3] = (200, 140, 220)                                       # arbitrary (not unit) vectors
        nrm[..., 3] = 0                                                      # alpha 0 everywhere, RGB must survive
        files = {"c.dds": col, "c_n.dds": et.write_dds(Image.fromarray(nrm, "RGBA"), "RGBA32", False)}
        out, warns = et.enhance_textures(files, 256, "lanczos")
        self.assertEqual(warns, [])
        c, n = et.read_dds(out["c.dds"]), et.read_dds(out["c_n.dds"])
        self.assertEqual((c.size, n.size), ((256, 256), (256, 256)))
        self.assertEqual(int(np.asarray(c)[..., 3].max()), 0)                # alpha preserved
        v = np.asarray(n, float)[..., :3] / 255 * 2 - 1
        self.assertLess(abs(np.linalg.norm(v, axis=2).mean() - 1.0), 0.02)   # renormalised
        self.assertGreater(np.asarray(n)[..., :3].max(), 0)                  # RGB not wiped by alpha 0

    def test_explicit_esrgan_without_it_installed_is_an_error(self):
        with self.assertRaises(et.TextureError):
            et.enhance_textures({"c.dds": et.write_dds(gradient(128), "RGBA32", False)}, 256, "esrgan", None)

    @unittest.skipIf(os.name == "nt", "uses a POSIX shell script as the fake upscaler")
    def test_auto_falls_back_with_a_warning_when_the_binary_fails(self):
        d = tempfile.mkdtemp()
        try:
            exe = os.path.join(d, "realesrgan-ncnn-vulkan")
            wr_(exe, b"#!/bin/sh\nexit 3\n")
            os.chmod(exe, 0o755)
            os.makedirs(os.path.join(d, "models"))
            out, warns = et.enhance_textures({"c.dds": et.write_dds(gradient(128), "RGBA32", False)}, 256, "auto", d)
            self.assertEqual(et.read_dds(out["c.dds"]).size, (256, 256))
            self.assertEqual(len(warns), 1)
            with self.assertRaises(et.TextureError):
                et.enhance_textures({"c.dds": et.write_dds(gradient(128), "RGBA32", False)}, 256, "esrgan", d)
        finally:
            shutil.rmtree(d)


class Params(unittest.TestCase):
    def test_validation_and_resolution(self):
        for bad in (E.Params(), E.Params(3, 0), E.Params(1, 777), E.Params(1, 512, "gpu")):
            with self.assertRaises(E.EnhanceError):
                bad.validate()
        self.assertEqual(E.Params(1, 512, "auto").resolved(None).engine, "lanczos")
        self.assertEqual(E.Params(1, 512, "auto").resolved("/x").engine, "esrgan")
        self.assertEqual(E.Params(2, 0, "auto").resolved("/x").engine, "lanczos")   # no textures: engine irrelevant
        self.assertEqual(E.Params.from_dict(E.Params(2, 1024, "lanczos").to_dict()), E.Params(2, 1024, "lanczos"))


@NEED
class RealCth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = rd_(CTH)

    def test_build_counts_members_and_untouched_bytes(self):
        data, rep = E.build_enhanced(self.base, E.Params(1, 512, "lanczos"), None, QUAIL)
        self.assertEqual(rep["models"][0]["verts_after"], 7754)
        self.assertEqual(rep["models"][0]["faces_after"], 12540)
        d = tempfile.mkdtemp()
        try:
            wr_(os.path.join(d, "o.eqg"), data)
            plan = E.plan(os.path.join(d, "o.eqg"), E.Params(1, 0), None)
            self.assertEqual(plan["models"][0]["verts_before"], 7754)       # re-read from the new archive
        finally:
            shutil.rmtree(d)

    def test_textures_only_leaves_the_mesh_byte_identical(self):
        data, rep = E.build_enhanced(self.base, E.Params(0, 1024, "lanczos"), None, QUAIL)
        self.assertEqual(len(rep["textures"]), 4)
        d = tempfile.mkdtemp()
        try:
            wr_(os.path.join(d, "a.eqg"), self.base)
            wr_(os.path.join(d, "b.eqg"), data)
            from s3d import load
            a, b = load(os.path.join(d, "a.eqg")), load(os.path.join(d, "b.eqg"))
            self.assertEqual(a["cth.mds"], b["cth.mds"])
            self.assertEqual(rep["models"], [])
        finally:
            shutil.rmtree(d)

    def test_plan_reports_limits_and_estimates(self):
        pl = E.plan(CTH, E.Params(2, 1024, "lanczos"), None)
        self.assertEqual((pl["models"][0]["verts_after"], pl["models"][0]["faces_after"]), (27946, 50160))
        self.assertIsNone(pl["blocked"])
        self.assertGreater(pl["bytes_after_estimate"], pl["bytes_before"])
        self.assertEqual(pl["engine"], "lanczos")
        self.assertTrue(any("not installed" in w for w in E.plan(CTH, E.Params(1, 512), None)["warnings"]))
        self.assertIn("not installed", E.plan(CTH, E.Params(1, 512, "esrgan"), None)["blocked"])
        pl3 = E.plan(CTH, E.Params(2, 0), None)
        self.assertEqual(pl3["textures"][0]["action"], "keep")

    def test_nothing_to_do_is_refused_not_silently_built(self):
        pl = E.plan(CTH, E.Params(0, 512, "lanczos"), None)          # CTH textures are already 512
        self.assertIn("nothing to do", pl["blocked"])
        with self.assertRaises(E.EnhanceError):
            E.build_enhanced(self.base, E.Params(0, 512, "lanczos"), None, QUAIL)

    def test_cancel_stops_before_repacking(self):
        with self.assertRaises(E.EnhanceError):
            E.build_enhanced(self.base, E.Params(1, 512, "lanczos"), None, QUAIL, cancel=lambda: True)


@NEED
class EngineLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EverQuest")
        self.vault = os.path.join(self.tmp, "Tweeq", "vault")
        self.van = os.path.join(self.tmp, "vanilla")
        os.makedirs(self.van)
        make_install(self.eq)
        shutil.copy(CTH, os.path.join(self.eq, "cth.eqg"))
        shutil.copy(CTH, os.path.join(self.van, "cth.eqg"))
        self.idx = os.path.join(self.tmp, "idx.json")
        make_index(self.idx)
        self.cth = os.path.join(self.eq, "cth.eqg")
        self.vanilla = rd_(CTH)
        os.environ["TWEEQ_QUAIL"] = QUAIL

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def sw(self):
        return Swapper(self.eq, self.vault, ModelIndex(self.idx, self.eq))

    def states(self, sw):
        return {r.path: r.state for r in sw.status() if r.path.endswith(".eqg")}

    P = E.Params(1, 512, "lanczos")

    def test_apply_restore_and_everything_in_between(self):
        sw = self.sw()
        dec = sw.add_enhance("cth", self.P)
        self.assertEqual(dec["kind"], "enhance")
        self.assertEqual(self.states(sw), {"cth.eqg": "needs-build"})
        self.assertEqual(rd_(self.cth), self.vanilla)                       # recording changes nothing
        self.assertEqual(self.states(sw.__class__(self.eq, self.vault, sw.models)), {"cth.eqg": "needs-build"})
        sw.apply(dry_run=True)
        self.assertEqual(rd_(self.cth), self.vanilla)                       # dry run builds nothing, writes nothing
        steps = []
        sw.apply(progress=lambda s, p: steps.append(s))
        self.assertTrue(steps and steps[-1] == "done")
        self.assertEqual(self.states(sw), {"cth.eqg": "applied"})
        self.assertEqual(sha(rd_(os.path.join(self.vault, "originals", "cth.eqg"))), sha(self.vanilla))
        self.assertNotEqual(rd_(self.cth), self.vanilla)
        self.assertEqual(E.plan(self.cth, E.Params(1, 0), None)["models"][0]["verts_before"], 7754)
        # a fresh Swapper (new process) agrees, and a second apply is a no-op that does not rebuild
        sw2 = self.sw()
        self.assertEqual(self.states(sw2), {"cth.eqg": "applied"})
        built = os.path.getmtime(os.path.join(self.vault, "enhanced", os.listdir(os.path.join(self.vault, "enhanced"))[0]))
        sw2.apply()
        self.assertEqual(built, os.path.getmtime(os.path.join(self.vault, "enhanced", os.listdir(os.path.join(self.vault, "enhanced"))[0])))
        # disable -> needs-apply -> apply puts the original back byte for byte
        sw2.set_enabled(dec["id"], False)
        self.assertEqual(self.states(sw2), {"cth.eqg": "needs-apply"})
        sw2.apply()
        self.assertEqual(rd_(self.cth), self.vanilla)
        self.assertEqual(self.states(sw2), {"cth.eqg": "clean"})
        # enable again re-uses the cached build (no rebuild), restore_all is byte-exact
        sw2.set_enabled(dec["id"], True)
        sw2.apply()
        self.assertEqual(self.states(sw2), {"cth.eqg": "applied"})
        sw2.restore_all()
        self.assertEqual(rd_(self.cth), self.vanilla)
        # remove the decision, apply -> original, status clean
        sw2.apply()
        sw2.remove_swap(dec["id"])
        sw2.apply()
        self.assertEqual(rd_(self.cth), self.vanilla)
        self.assertEqual(self.states(sw2), {"cth.eqg": "clean"})

    def test_patch_overwrite_is_drift_and_accept_rebuilds_from_the_new_original(self):
        sw = self.sw()
        sw.add_enhance("cth", self.P)
        sw.apply()
        patched, _ = E.build_enhanced(self.vanilla, E.Params(0, 1024, "lanczos"), None, QUAIL)   # "new" vanilla
        wr_(self.cth, patched)
        self.assertEqual(self.states(sw), {"cth.eqg": "drifted"})
        with self.assertRaises(SwapError):
            sw.apply()
        self.assertEqual(rd_(self.cth), patched)                             # refused, nothing touched
        sw.apply(accept_drift=True)
        self.assertEqual(self.states(sw), {"cth.eqg": "applied"})
        self.assertEqual(rd_(os.path.join(self.vault, "originals", "cth.eqg")), patched)
        self.assertEqual(E.plan(self.cth, E.Params(1, 0), None)["models"][0]["verts_before"], 7754)

    def test_failed_build_leaves_the_install_untouched(self):
        sw = self.sw()
        sw.add_enhance("cth", E.Params(1, 1024, "esrgan"))                   # explicit esrgan, not installed
        before = rd_(self.cth)
        with self.assertRaises(SwapError):
            sw.apply()
        self.assertEqual(rd_(self.cth), before)
        self.assertFalse(os.path.exists(os.path.join(self.vault, "originals", "cth.eqg")))

    def test_guards(self):
        sw = self.sw()
        with self.assertRaises(SwapError) as c:
            sw.add_enhance("caz", self.P)
        self.assertIn("EQG", str(c.exception))
        with self.assertRaises(SwapError):
            sw.add_enhance("nosuchtag", self.P)
        sw.add_enhance("cth", self.P)
        with self.assertRaises(SwapError):
            sw.add_enhance("cth", E.Params(2, 0))                           # already enhanced
        with self.assertRaises(E.EnhanceError):
            sw.add_enhance("ork", E.Params())

    def test_modified_live_file_needs_take_over_and_uses_the_vanilla_copy(self):
        modified, _ = E.build_enhanced(self.vanilla, E.Params(0, 1024, "lanczos"), None, QUAIL)
        wr_(self.cth, modified)
        sw = self.sw()
        with self.assertRaises(SwapError):
            sw.add_enhance("cth", self.P, vanilla_dir=self.van)
        self.assertEqual(sw.decisions(), [])
        sw.add_enhance("cth", self.P, vanilla_dir=self.van, take_over=True)
        self.assertEqual(sha(rd_(os.path.join(self.vault, "originals", "cth.eqg"))), sha(self.vanilla))
        sw.apply()
        self.assertEqual(E.plan(self.cth, E.Params(1, 0), None)["models"][0]["verts_before"], 7754)
        sw.restore_all()
        self.assertEqual(rd_(self.cth), self.vanilla)

    def test_cached_builds_from_an_older_pipeline_are_not_reused(self):
        p = E.Params(1, 512, "lanczos")
        old = E.PIPELINE_VERSION
        a = E.params_hash("abc", p)
        try:
            E.PIPELINE_VERSION = old + 1
            self.assertNotEqual(a, E.params_hash("abc", p))
        finally:
            E.PIPELINE_VERSION = old

    def test_take_over_keeps_a_copy_and_a_later_enhance_cannot_skip_the_question(self):
        modified, _ = E.build_enhanced(self.vanilla, E.Params(0, 1024, "lanczos"), None, QUAIL)
        wr_(self.cth, modified)
        sw = self.sw()
        dec = sw.add_enhance("cth", self.P, vanilla_dir=self.van, take_over=True)
        kept = os.path.join(self.vault, "unmanaged")
        saved = [os.path.join(r, f) for r, _d, fs in os.walk(kept) for f in fs]
        self.assertEqual([rd_(x) for x in saved], [modified])                # the hand-made file is not lost
        sw.remove_swap(dec["id"])                                            # build cancelled: decision gone, live untouched
        self.assertEqual(rd_(self.cth), modified)
        with self.assertRaises(SwapError):                                   # vanilla is vaulted now, but still asks
            self.sw().add_enhance("cth", self.P, vanilla_dir=self.van)

    def test_no_build_leaves_an_unbuilt_archive_alone(self):
        sw = self.sw()
        sw.add_enhance("cth", self.P)
        reps = sw.apply(no_build=True)
        self.assertEqual({r.path: r.state for r in reps}["cth.eqg"], "needs-build")
        self.assertEqual(rd_(self.cth), self.vanilla)
        self.assertEqual(os.listdir(os.path.join(self.vault, "enhanced")) if os.path.isdir(os.path.join(self.vault, "enhanced")) else [], [])

    def test_swap_and_enhance_coexist(self):
        sw = self.sw()
        sw.add_swap(95, "cth", ["potimeb"])
        sw.add_enhance("cth", self.P)
        sw.apply()
        st = {r.path: r.state for r in sw.status()}
        self.assertEqual(set(st.values()), {"applied"})
        self.assertIn("cth.eqg", st)
        self.assertIn("racedata.txt", st)
        kinds = sorted(d["kind"] for d in self.sw().decisions())
        self.assertEqual(kinds, ["enhance", "swap"])
        sw.set_all_enabled(False)
        sw.apply()
        self.assertEqual(rd_(self.cth), self.vanilla)

    def test_old_manifests_without_a_kind_still_work(self):
        sw = self.sw()
        sw.add_swap(95, "cth", ["potimeb"])
        for d in sw.m["decisions"]:
            d.pop("kind", None)
        sw._save()
        sw2 = self.sw()
        sw2.apply()
        self.assertEqual({r.state for r in sw2.status()}, {"applied"})


@NEED
class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.eq = os.path.join(self.tmp, "EverQuest")
        self.vault = os.path.join(self.tmp, "Tweeq", "vault")
        make_install(self.eq)
        shutil.copy(CTH, os.path.join(self.eq, "cth.eqg"))
        self.idx = os.path.join(self.tmp, "idx.json")
        make_index(self.idx)
        os.environ["TWEEQ_QUAIL"] = QUAIL

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def call(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(["--json", "--eq", self.eq, "--vault", self.vault, "--index", self.idx, *args])
        out = json.loads(buf.getvalue())
        return rc, out

    def test_plan_enhance_list_apply_progress(self):
        rc, out = self.call("enhance-plan", "cth", "--passes", "1", "--tex-size", "512", "--engine", "lanczos")
        self.assertEqual(rc, 0)
        self.assertEqual(out["data"]["models"][0]["faces_after"], 12540)
        rc, out = self.call("enhance", "cth", "--passes", "1", "--tex-size", "512", "--engine", "lanczos")
        self.assertEqual(out["data"]["kind"], "enhance")
        rc, out = self.call("list")
        self.assertEqual([d["kind"] for d in out["data"]], ["enhance"])
        prog = os.path.join(self.tmp, "p.json")
        rc, out = self.call("--progress-file", prog, "apply")
        self.assertEqual(rc, 0)
        self.assertEqual({r["path"]: r["state"] for r in out["data"]}.get("cth.eqg"), "applied")
        with open(prog) as f:
            self.assertEqual(json.load(f)["pct"], 1.0)
        rc, out = self.call("enhance", "caz", "--passes", "1")
        self.assertEqual(rc, 2)
        self.assertIn("classic", out["error"])
        rc, out = self.call("enhance", "cth", "--passes", "5")
        self.assertEqual(rc, 2)

    def test_preview_of_an_enhanced_model(self):
        rc, out = self.call("enhance-preview", "cth", "--passes", "1", "--tex-size", "512", "--engine", "lanczos",
                            "--out", os.path.join(self.tmp, "prev"))
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["data"]["status"], "ok", out)
        with open(out["data"]["path"], "rb") as f:
            self.assertEqual(f.read(4), b"glTF")
        rc, again = self.call("enhance-preview", "cth", "--passes", "1", "--tex-size", "512", "--engine", "lanczos",
                              "--out", os.path.join(self.tmp, "prev"))
        self.assertEqual(again["data"]["status"], "cached")
        self.assertEqual(rd_(os.path.join(self.eq, "cth.eqg")), rd_(CTH))   # previewing never touches the install


class UpscalerInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.vault = os.path.join(self.tmp, "Tweeq", "vault")
        os.makedirs(self.vault)
        self.orig = E.UPSCALER_ZIP_SHA256

    def tearDown(self):
        E.UPSCALER_ZIP_SHA256 = self.orig
        shutil.rmtree(self.tmp)

    def make_zip(self, names):
        p = os.path.join(self.tmp, "u.zip")
        with zipfile.ZipFile(p, "w") as z:
            for n in names:
                z.writestr(n, b"x")
        return p

    def test_wrong_checksum_installs_nothing(self):
        z = self.make_zip(["realesrgan-ncnn-vulkan.exe", "models/a.param"])
        with self.assertRaises(E.EnhanceError) as c:
            E.install_upscaler(self.vault, zip_path=z)
        self.assertIn("checksum", str(c.exception))
        self.assertFalse(os.path.exists(E.upscaler_dir(self.vault)))

    def test_path_traversal_is_refused(self):
        z = self.make_zip(["../evil.txt", "realesrgan-ncnn-vulkan.exe"])
        with open(z, "rb") as f:
            E.UPSCALER_ZIP_SHA256 = hashlib.sha256(f.read()).hexdigest()
        with self.assertRaises(E.EnhanceError) as c:
            E.install_upscaler(self.vault, zip_path=z)
        self.assertIn("unsafe", str(c.exception))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "Tweeq", "evil.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_discovery_order_and_removal(self):
        d = E.upscaler_dir(self.vault)
        self.assertIsNone(E.find_esrgan(self.vault))
        os.makedirs(os.path.join(d, "models"))
        wr_(os.path.join(d, "realesrgan-ncnn-vulkan.exe"), b"x")
        self.assertEqual(E.find_esrgan(self.vault), d)
        self.assertTrue(E.remove_upscaler(self.vault))
        self.assertIsNone(E.find_esrgan(self.vault))
        self.assertFalse(E.remove_upscaler(self.vault))


if __name__ == "__main__":
    unittest.main()


class QuailArguments(unittest.TestCase):
    """quail splits an archive argument at ':' (archive:member), which breaks Windows drive paths (C:\\x.eqg)."""

    def test_unzip_gets_a_bare_file_name_from_the_archives_own_folder(self):
        from unittest import mock
        seen = []
        with mock.patch("tweeq.enhance._run_quail", side_effect=lambda q, args, cwd=None: seen.append((args, cwd))), \
                mock.patch("tweeq.enhance._load_members", return_value={}), mock.patch("os.listdir", return_value=[]):
            with self.assertRaises(E.EnhanceError):          # no .mds in the (fake, empty) unpacked tree
                E.build_enhanced(b"x", E.Params(1, 0), None, "quail")
        (args, cwd), = seen
        self.assertEqual(args[0], "unzip")
        self.assertNotIn(os.sep, args[1])
        self.assertNotIn(":", args[1])
        self.assertEqual(args[1], "in.eqg")
        self.assertTrue(cwd and os.path.isabs(cwd))
