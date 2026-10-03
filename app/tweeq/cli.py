"""tweeq command line. `--json` makes every command print one JSON object, which is the
IPC contract the Godot UI uses:   {"ok": true, "data": ...}  or  {"ok": false, "error": "..."}

    python -m tweeq.cli --eq EQ --vault VAULT [--index IDX] [--peq DB] [--models DIR] [--json] <cmd>

  info                         counts + file states
  status | plan                per-file state (plan = dry run of apply)
  races [--filter S]           racedata rows (race, genders, current + original tag, NPC example)
  models [--filter S]          installed model tags with format / container / list archive
  zones [--race N]             zones to target (PEQ spawn zones if available, else all zone lists)
  list                         recorded swaps
  preview TAG --out DIR        build/cache a .glb preview of an installed model
  npcs ZONE                    NPC groups (race/gender) spawning in a zone, with their current model tag
  discover [--check PATH]      find the EverQuest install(s) / validate a folder
  disable-all                  switch every recorded swap off (then `apply` restores the originals)
  adopt                        record edits already in the install as swaps (needs --vanilla)
  check RACE TAG [--zones a,b]  pre-swap compatibility findings (+ suggested height)
  swap RACE TAG --zones a,b [--genders 0,1] [--height 6]
  remove|enable|disable ID
  apply [--accept-drift]       write all enabled swaps (re-run after patches)
  restore                      put every managed file back to its original
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import time
import traceback

from . import __version__, enhance, scan as scanmod
from .discover import discover, missing_files, to_native
from .engine import SwapError, Swapper, norm_dir
from .compat import CompatDB, check_swap
from .kinds import DEFAULT_RULES, Kinds
from .models import ModelIndex
from .names import build_names, is_listable, load_overrides, load_race_names
from .paths import DATA_DIR, bundled_quail
from .uninstall import ClientRunning, restore_all
from .peq import npc_groups_in_zone, race_summary, zones_for_race
from .preview import ensure_enhanced_preview, ensure_preview
from .racedata import RaceData

CONTRACT = 1


def model_names(a, sw, idx) -> tuple[dict[str, str], dict[int, str]]:
    """({tag: display name}, {race id: client race name}) for the whole install."""
    race_names = load_race_names(a.models or a.eq)
    if not idx:
        return {}, race_names
    ov = load_overrides(os.path.join(os.path.dirname(a.index), "model_names.json") if a.index else None,
                        os.path.join(DATA_DIR, "model_names.json"),
                        os.path.join(a.vault, "model_names.json"))     # the user's own file wins
    peq = a.peq if a.peq and os.path.exists(a.peq) else None
    names = build_names(RaceData(sw._base("racedata.txt")), [t for t in idx.tags() if is_listable(t)],
                        race_names, peq_db=peq, overrides=ov)
    return names, race_names


def _reports(reps):
    return [{"path": r.path, "state": r.state} for r in reps]


def _decision(d):
    if d.get("kind") == "enhance":
        return {"id": d["id"], "kind": "enhance", "enabled": d["enabled"], "model_tag": d["model_tag"],
                "container": d["container"], "params": d["params"], "notes": d["notes"]}
    o = d["originals"][str(d["genders"][0])]
    return {"id": d["id"], "kind": "swap", "enabled": d["enabled"], "race": d["race"], "genders": d["genders"],
            "original_tag": o["tag"], "model_tag": d["model_tag"], "zones": d["zones"],
            "all_zones": bool(d.get("all_zones")),
            "height": d["height"], "list_archive": d["list_archive"], "notes": d["notes"]}


def _no_index(a) -> str:
    """Say WHICH index was missing: 'no model index' alone is hard to act on."""
    if not a.index:
        return "no model index given (--index)"
    return f"no model index: {a.index!r} does not exist yet (exists={os.path.exists(a.index)}, cwd={os.getcwd()!r})"


def _upscaler_info(sw) -> dict:
    d = sw._esrgan()
    return {"installed": bool(d), "path": d, "install_dir": enhance.upscaler_dir(sw.vault),
            "version": enhance.UPSCALER_VERSION, "url": enhance.UPSCALER_ZIP_URL}


def _cancel_checker(path):
    """Stale cancel files from an earlier run are cleared first, so a new command is not cancelled at birth."""
    if not path:
        return None
    if os.path.exists(path):
        os.remove(path)
    return lambda: os.path.exists(path)


def _progress_writer(path):
    if not path:
        return None

    def write(stage, pct):
        """Progress is a courtesy to the UI and must never fail a command. On Windows os.replace is refused while the
        UI has the file open, so retry briefly and, if it still will not go, just skip this update."""
        tmp = path + ".tmp"
        for attempt in range(6):
            try:
                with open(tmp, "w") as f:
                    json.dump({"stage": stage, "pct": round(float(pct), 3)}, f)
                os.replace(tmp, path)
                return
            except OSError:
                time.sleep(0.02 * (attempt + 1))
    return write


def all_zone_lists(eq: str) -> list[str]:
    return sorted(os.path.basename(p)[:-8] for p in glob.glob(os.path.join(eq, "*_chr.txt")))


def build_parser():
    ap = argparse.ArgumentParser(prog="tweeq")
    ap.add_argument("--eq")
    ap.add_argument("--vault")
    ap.add_argument("--index")
    ap.add_argument("--peq")
    ap.add_argument("--vanilla", help="folder of vanilla copies of racedata/zone lists (enables the unmanaged-edit guard + adopt)")
    ap.add_argument("--models", help="folder holding the model archives (read-only); default: --eq")
    ap.add_argument("--compat", help="model_compat.json (default: next to --index)")
    ap.add_argument("--esrgan", help="folder with realesrgan-ncnn-vulkan (default: <app data>/tools/realesrgan)")
    ap.add_argument("--progress-file", help="long commands write {stage,pct} JSON here for the UI to poll")
    ap.add_argument("--cancel-file", help="a long command stops cleanly when this file appears")
    ap.add_argument("--json", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("adopt")
    sub.add_parser("disable-all")
    p = sub.add_parser("discover"); p.add_argument("--check")
    p = sub.add_parser("uninstall-restore"); p.add_argument("--data-root", required=True)
    for n in ("info", "status", "plan", "list", "restore"):
        sub.add_parser(n)
    p = sub.add_parser("apply"); p.add_argument("--accept-drift", action="store_true")
    for n in ("enhance-plan", "enhance-preview", "enhance"):
        p = sub.add_parser(n); p.add_argument("tag")
        p.add_argument("--passes", type=int, default=0); p.add_argument("--tex-size", type=int, default=0)
        p.add_argument("--engine", default="auto")
        if n == "enhance-preview":
            p.add_argument("--out", required=True)
        if n == "enhance":
            p.add_argument("--take-over", action="store_true")
    sub.add_parser("scan-status")
    p = sub.add_parser("scan"); p.add_argument("--force", action="store_true")
    sub.add_parser("upscaler-status")
    p = sub.add_parser("upscaler-install"); p.add_argument("--from-zip")
    sub.add_parser("upscaler-remove")
    p = sub.add_parser("races"); p.add_argument("--filter", default="")
    p = sub.add_parser("models"); p.add_argument("--filter", default="")
    p = sub.add_parser("zones"); p.add_argument("--race", type=int)
    p = sub.add_parser("preview"); p.add_argument("tag"); p.add_argument("--out", required=True)
    p = sub.add_parser("npcs"); p.add_argument("zone")
    p = sub.add_parser("check"); p.add_argument("race", type=int); p.add_argument("tag")
    p.add_argument("--zones", default="")
    p = sub.add_parser("swap")
    p.add_argument("race", type=int); p.add_argument("tag")
    p.add_argument("--zones", required=True); p.add_argument("--genders"); p.add_argument("--height")
    for n in ("remove", "enable", "disable"):
        sub.add_parser(n).add_argument("id")
    return ap


def run(a) -> object:
    if a.cmd == "discover":                      # needs no install: it is how we find one
        if a.check:
            miss = missing_files(to_native(a.check))
            return {"path": to_native(a.check), "valid": not miss, "missing": miss}
        return {"candidates": discover()}
    if a.cmd == "uninstall-restore":             # works from the per-user data folder alone
        return restore_all(a.data_root)
    if not a.eq or not a.vault:
        raise SwapError("--eq and --vault are required for this command")
    kinds = Kinds(DEFAULT_RULES, os.path.join(a.vault, "model_kinds.json"))
    idx = ModelIndex(a.index, a.eq, kinds) if a.index and os.path.exists(a.index) else None   # absent before the first scan
    sw = Swapper(a.eq, a.vault, idx, esrgan_dir=a.esrgan)
    progress = _progress_writer(a.progress_file)
    cancel = _cancel_checker(a.cancel_file)
    unmanaged = sw.unmanaged_edits(a.vanilla) if a.vanilla and os.path.isdir(a.vanilla) else []
    if a.cmd == "adopt":
        if not a.vanilla or not os.path.isdir(a.vanilla):
            raise SwapError("adopt needs --vanilla DIR (a folder with vanilla copies of the edited files)")
        return sw.adopt_from_vanilla(a.vanilla)
    if a.cmd in ("swap", "apply", "enhance") and unmanaged:
        raise SwapError("these files differ from your vanilla backups and are not tracked by this app yet: "
                        f"{', '.join(unmanaged)}. Run 'adopt' first, or the app would treat your edits as the originals.")
    if a.cmd == "info":
        return {"contract": CONTRACT, "version": __version__, "eq": a.eq, "unmanaged_edits": unmanaged, "models": len(idx.tags()) if idx else None,
                "peq": bool(a.peq and os.path.exists(a.peq)), "swaps": len(sw.decisions()),
                "upscaler": _upscaler_info(sw),
                "files": _reports(sw.status())}
    if a.cmd in ("status", "plan"):
        return _reports(sw.apply(dry_run=True) if a.cmd == "plan" else sw.status())
    if a.cmd == "list":
        return [_decision(d) for d in sw.decisions()]
    if a.cmd == "races":
        live, orig = RaceData(sw._live("racedata.txt")), RaceData(sw._base("racedata.txt"))
        info = race_summary(a.peq) if a.peq and os.path.exists(a.peq) else {}
        race_names = load_race_names(a.models or a.eq)
        f = a.filter.lower()
        out = []
        for race in sorted({r for r, _ in live.keys()}):
            gs = live.genders(race)
            tags = {g: live.get_tag(race, g) for g in gs}
            origs = {g: orig.get_tag(race, g) for g in gs}
            if idx is not None and not any(t.lower() in idx.by_tag for t in tags.values()):
                continue                     # object races (boats, props) and races with no installed model
            meta = info.get(race, {})
            row = {"race": race, "genders": gs, "tags": tags, "original_tags": origs,
                   "swapped": tags != origs, "npcs": meta.get("npcs"), "example": meta.get("example", ""),
                   "name": race_names.get(race, "")}
            if f and f not in f"{race} {' '.join(tags.values())} {row['example']} {row['name']}".lower():
                continue
            out.append(row)
        return out
    if a.cmd == "models":
        if not idx:
            raise SwapError(_no_index(a))
        names, _rn = model_names(a, sw, idx)
        f = a.filter.lower()
        out = []
        for t in idx.tags():
            if not is_listable(t):
                continue
            r = idx.resolve(t)
            name = names.get(t, t.upper())
            if f and f not in f"{name} {t} {r.container}".lower():
                continue
            out.append({"tag": t, "name": name, "format": r.format, "container": r.container,
                        "list_archive": r.list_archive, "notes": r.notes,
                        "enhanceable": r.format == "eqg_mds"})
        def sort_key(m):  # alphabetical ignoring "a/an/the"; names starting with a digit last
            n = re.sub(r"^(a|an|the) ", "", m["name"].lower())
            return (n[:1].isdigit(), n, m["tag"])
        out.sort(key=sort_key)
        return out
    if a.cmd == "zones":
        if a.race is not None and a.peq and os.path.exists(a.peq):
            have = set(all_zone_lists(a.eq))
            zs = [z for z in zones_for_race(a.peq, a.race) if z in have]
            return {"source": "peq", "zones": zs, "all": all_zone_lists(a.eq)}
        return {"source": "lists", "zones": [], "all": all_zone_lists(a.eq)}
    if a.cmd == "preview":
        if not idx:
            raise SwapError(_no_index(a))
        return ensure_preview(a.models or a.eq, idx, a.tag, a.out)
    if a.cmd == "npcs":
        if not a.peq or not os.path.exists(a.peq):
            raise SwapError("no PEQ import: NPC lists need the user-supplied PEQ database (--peq)")
        rd = RaceData(sw._live("racedata.txt"))
        cpath = a.compat or (os.path.join(os.path.dirname(a.index), "model_compat.json") if a.index else None)
        cdb = CompatDB(cpath) if cpath and os.path.exists(cpath) else None
        names, race_names = model_names(a, sw, idx)
        out = []
        for g in npc_groups_in_zone(a.peq, a.zone):
            tag = rd.tag_for(g["race"], g["gender"])
            ref = None
            if idx and tag:
                try:
                    ref = idx.resolve(tag)
                except KeyError:
                    ref = None
            cm = cdb.get(tag) if (cdb and tag) else None
            if idx is not None and tag and tag.lower() not in idx.by_tag:
                continue                     # an NPC whose model is an object (boat, trap...) or not installed
            out.append({**g, "tag": tag, "model_name": names.get((tag or "").lower(), (tag or "").upper()),
                        "race_name": race_names.get(g["race"], ""), "format": ref.format if ref else None,
                        "has_model": ref is not None,
                        # clip count lets the UI skip placeholder models (invisible traps etc.) as defaults
                        "clips": len(cm["clips"]) if cm else None})
        return out
    if a.cmd == "check":
        cpath = a.compat or (os.path.join(os.path.dirname(a.index), "model_compat.json") if a.index else None)
        if not cpath or not os.path.exists(cpath):
            raise SwapError("no model_compat.json (run tools/build_compat_index.py)")
        rd = RaceData(sw._base("racedata.txt"))
        gs = rd.genders(a.race)
        if not gs:
            raise SwapError(f"race {a.race} has no racedata rows")
        needs = True
        if idx:
            needs = idx.resolve(a.tag).list_archive is not None
        fs = check_swap(CompatDB(cpath), rd.get_tag(a.race, gs[0]), a.tag, rd=rd, race=a.race,
                        zones=[z for z in a.zones.split(",") if z] if a.zones else None,
                        peq_db=a.peq if a.peq and os.path.exists(a.peq) else None, needs_list=needs)
        sug = next((f.data["suggest_height"] for f in fs if f.code == "height"), None)
        return {"findings": [f.as_dict() for f in fs], "suggest_height": sug,
                "worst": fs[0].level if fs else "ok"}
    if a.cmd == "swap":
        gs = [int(x) for x in a.genders.split(",")] if a.genders else None
        zones = "all" if a.zones.strip().lower() == "all" else [z for z in a.zones.split(",") if z]
        return _decision(sw.add_swap(a.race, a.tag, zones, gs, a.height))
    if a.cmd == "apply":
        return _reports(sw.apply(accept_drift=a.accept_drift, progress=progress, cancel=cancel))
    if a.cmd in ("enhance-plan", "enhance-preview", "enhance"):
        if not idx:
            raise SwapError(_no_index(a))
        params = enhance.Params(a.passes, a.tex_size, a.engine).validate()
        ref = idx.resolve(a.tag)
        if ref.format != "eqg_mds":
            raise SwapError(f"{a.tag.upper()} is a classic model; Enhance supports EQG (Luclin and later) models for now")
        if a.cmd == "enhance":
            return _decision(sw.add_enhance(a.tag, params, vanilla_dir=a.vanilla, take_over=a.take_over))
        base = sw.base_path(ref.container)
        if a.cmd == "enhance-plan":
            return enhance.plan(base, params, sw._esrgan())
        return ensure_enhanced_preview(idx, a.tag, a.out, base, params, sw._esrgan(), progress=progress, cancel=cancel)
    if a.cmd in ("scan-status", "scan"):
        if not a.index:
            raise SwapError("--index is required (it names the per-install folder the scan is written to)")
        out_dir = os.path.dirname(os.path.abspath(a.index))
        src = a.models or a.eq                     # where the model archives are (same folder as previews read)
        managed = set(sw.enhance_files()) if norm_dir(src) == norm_dir(a.eq) else set()
        base = (lambda name: sw.base_path(name) if name in managed else None)   # enhanced archives: scan the vaulted original
        st = scanmod.status(src, out_dir, base)
        if a.cmd == "scan-status":
            return st
        if st["fresh"] and not a.force:
            return {**st, "models": st["models"], "seconds": 0.0, "skipped": True}
        try:
            return scanmod.run(src, out_dir, base, progress=progress, cancel=cancel)
        except scanmod.ScanCancelled:
            raise SwapError("the scan was cancelled; nothing was changed")
    if a.cmd == "upscaler-status":
        return _upscaler_info(sw)
    if a.cmd == "upscaler-install":
        try:
            return enhance.install_upscaler(a.vault, zip_path=a.from_zip, progress=progress)
        except enhance.EnhanceError as e:
            raise SwapError(str(e))
    if a.cmd == "upscaler-remove":
        return {"removed": enhance.remove_upscaler(a.vault)}
    if a.cmd == "restore":
        sw.restore_all()
        return {"restored": True}
    if a.cmd == "disable-all":
        return {"disabled": sw.set_all_enabled(False)}
    if a.cmd == "remove":
        sw.remove_swap(a.id)
    else:
        sw.set_enabled(a.id, a.cmd == "enable")
    return {"id": a.id}


def _list_line(d) -> str:
    on = "on " if d["enabled"] else "off"
    if d["kind"] == "enhance":
        p = d["params"]
        return (f"{d['id'][:8]} {on} enhance {d['model_tag']} passes={p['passes']} "
                f"textures={p['tex_size'] or 'off'} ({d['container']})")
    return (f"{d['id'][:8]} {on} race {d['race']} {d['original_tag']} -> {d['model_tag']} "
            f"zones={'all' if d['all_zones'] else ','.join(d['zones'])}" + (f" height={d['height']}" if d["height"] else ""))


def render_text(cmd: str, data) -> str:
    if cmd in ("status", "plan", "apply"):
        return "\n".join(f"{r['state']:12s} {r['path']}" for r in data)
    if cmd == "list":
        return "\n".join(_list_line(d) for d in data)
    if cmd == "races":
        return "\n".join(f"race {r['race']:4d} {','.join(r['tags'].values()):10s}"
                         + (f" (was {','.join(r['original_tags'].values())})" if r["swapped"] else "")
                         + f"  {r['example']}" for r in data)
    if cmd == "check":
        return "\n".join(f"{f['level']:5s} {f['code']}: {f['message']}" for f in data["findings"]) or "no findings"
    if cmd == "models":
        return "\n".join(f"{m['name']:34s} {m['tag']:6s} {m['format']:12s} {m['container']}" for m in data)
    return json.dumps(data, indent=1)


LOG_LIMIT = 1_000_000


def _log(text: str) -> None:
    """Append to the file named by TWEEQ_LOG (the installed app sets it to %APPDATA%\\Tweeq\\logs\\engine.log)."""
    path = os.environ.get("TWEEQ_LOG")
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > LOG_LIMIT:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    q = bundled_quail()
    if q:                                         # child tools (wce_chr_to_gltf...) find the same quail
        os.environ.setdefault("TWEEQ_QUAIL", q)
    try:
        data = run(a)
    except (SwapError, enhance.EnhanceError, KeyError, ValueError) as e:
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        _log(f"{a.cmd}: {msg}")
        code = 4 if isinstance(e, ClientRunning) else 2
        if a.json:
            print(json.dumps({"ok": False, "error": str(msg)}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return code
    except Exception as e:                        # a bug: say so plainly, keep the details in the log
        _log(f"{a.cmd}: INTERNAL ERROR\n{traceback.format_exc()}")
        msg = f"internal error: {type(e).__name__}: {e} (details are in the engine log)"
        if a.json:
            print(json.dumps({"ok": False, "error": msg}))
        else:
            print(f"error: {msg}", file=sys.stderr)
        return 3
    print(json.dumps({"ok": True, "data": data}) if a.json else render_text(a.cmd, data))
    if a.cmd == "uninstall-restore" and (data["drifted"] or data["errors"]):
        _log(f"uninstall-restore: left alone {data['drifted']}; errors {data['errors']}")
        return 3                                    # the installer tells the user some files were not restored
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
