# Tweeq — engine + CLI

Tweeq swaps which model an EverQuest race uses and can **enhance** EQG models (more polygons, bigger textures).
This directory is the headless engine and JSON CLI that the Godot UI drives (plan: ../PLAN_tweeq.md).

## How a swap works (proven in-game 2026-10-01)
1. `racedata.txt`: set field 50 (model tag) of the race/gender row; optionally fields 46/47 (native height).
2. `<zone>_chr.txt`: ensure the zone loads the model (`tag,tag` EQG / `tag,tag_chr` WLD; none if the
   archive is already loaded globally per `Resources/GlobalLoad.txt`).

No game files are copied or shipped. Decisions live in a manifest and are **replayed onto the current
files**, so `apply` after an official patch re-creates every swap. Originals are vaulted on first touch.

## Layout
- `tweeq/racedata.py`, `zonelist.py` — byte-exact format layers (only edited fields change)
- `tweeq/models.py` — resolves a tag via `data/installed_model_index.json` (tools/scan_install.py) + GlobalLoad.txt
- `tweeq/engine.py` — `Swapper`: add/remove/enable swaps, `status`, `apply`, `restore_all`, drift handling
- `tweeq/peq.py` — zone/NPC discovery from a PEQ database: the installer ships a slim GPL extract of the EQEmu/ProjectEQ data (`data/peq_slim.sqlite`, listed in THIRD_PARTY_LICENSES.md); `--peq` can point at your own import instead, and everything works without it
- `tweeq/cli.py` — `python -m tweeq.cli --eq EQ --vault VAULT --index IDX {swap,plan,apply,status,list,restore,...}`
- `tests/` — `python3 -W error::ResourceWarning -m unittest discover -s tests -t .`
  (includes an integration test that rebuilds the live PoTime Cazic edit byte-for-byte from the vanilla backups)

## Enhance (EQG models)
`tweeq/enhance.py`: `build_enhanced(vanilla archive bytes, Params)` = `quail unzip` -> midpoint-subdivide every `.mds`
(`tools/mds_subdivide.py`, refuses > 65,535 vertices per model) -> upscale textures (`tools/enhance_textures.py`) ->
`quail zip` -> re-read and verify. It is a second decision type (`kind: enhance`) in the same manifest: the vanilla
archive is vaulted on first touch, built archives are cached in `<vault>/enhanced/` (key = sha of the original +
settings), `apply` builds anything missing BEFORE writing a single file (a failed or cancelled build leaves the
install untouched), and a game patch that replaces the archive reads as `drifted` like any other file.
- Params: `passes` 0/1/2 (x4 faces per pass), `tex_size` 0/512/1024, `engine` auto|esrgan|lanczos.
  Textures already at/above the target, under 64 px, or in an unreadable format are left alone; a request that
  would change nothing is refused. DDS layout is kept (RGBA32 stays RGBA32, DXT is re-encoded, mips only if the source had them).
- Textures: the built-in Lanczos resize always works. Real-ESRGAN (`realesrgan-ncnn-vulkan`) is optional and is NEVER
  shipped in this repo (its weights were trained on academic-only data sets): `upscaler-install` downloads the official
  release into `%APPDATA%\Tweeq\tools\realesrgan` after a sha256 check and a GPU self-test; `upscaler-remove` deletes it.
- Only EQG (`eqg_mds`) models. Classic WLD models are refused with a clear message.
- CLI: `enhance-plan TAG --passes N --tex-size S` (read-only estimate), `enhance-preview TAG ... --out DIR`
  (builds in a temp folder and returns a .glb; never touches the install), `enhance TAG ... [--take-over]` (records
  the decision; `apply` builds and writes it), `upscaler-status|install [--from-zip PATH]|remove`.
  Long commands take `--progress-file F` (`{stage,pct}` JSON to poll) and `--cancel-file F` (create it to stop cleanly).
- `--take-over`: the live archive differs from the vanilla backup (`--vanilla`), e.g. a hand-made enhanced build.
  Tweeq then rebuilds from the backup copy instead of enhancing the already-modified file.

## Status values
`clean` / `applied` / `needs-apply` (patch restored vanilla, or decisions changed) / `needs-build` (an enhanced
archive is wanted but not built yet; `apply` builds it) / `drifted` (file changed by something else;
`apply --accept-drift` adopts it as the new original) / `missing`.

## Run it (MVP)
`./run_app.sh` opens the window. Pick a zone on the left (type to filter, e.g. `gfaydark`), then one of its NPC
models: pane A shows that NPC's model, pane B a second character (default: the next different model in the zone;
pick any installed model from the list to replace it). Drag to orbit, wheel to zoom; each pane is independent.
Previews are generated on first view (WLD ~0.5-7 s, EQG ~2 s, on a background thread) and cached under
%APPDATA%\Tweeq\previews (cache key = archive size/mtime + converter version). It defaults to the vanilla
sandbox, so Apply/Restore never touch your real EverQuest folder.

## Godot UI shell (app/ui, Godot 4.7.2)
Zone or race -> the model to replace (pane A) -> any installed model as the replacement (pane B) -> Swap A -> B
(applied at once). The "Enhance model B" box on the right (`scripts/enhance_panel.gd`) shows the plan, previews
the enhanced model in pane B, and enhances. Two isolated 3D panes (A current /
B candidate) load any .glb at runtime (`scripts/viewer_pane.gd`); previews come from `ui/previews/<tag>.glb`
(generated on demand by `tweeq/preview.py` -> tools/mds_to_gltf.py / wce_chr_to_gltf.py). `scripts/backend.gd` is the only bridge: one
process per call, one JSON object back. Dev launcher = `wsl.exe -d Ubuntu ... python3 -m tweeq.cli`
(default WSL distro here is docker-desktop, so `-d Ubuntu` matters); a packaged build swaps in tweeq.exe.
- Engine commands the UI uses: `npcs ZONE` (PEQ NPC groups + current tag + clip count), `preview TAG --out DIR` (model archives are read from `--models`, default `--eq`), plus races/models/zones/check/swap/apply/restore.
- `python3 make_sandbox.py` builds `app/sandbox/EverQuest` from VANILLA files; the UI defaults to it.
  Do NOT point the app at an install carrying hand-applied edits: the first apply vaults whatever is live as the "original".
- `ui/sync_and_test.sh` syncs to C:\Users\brian\tweeq_ui and runs the headless smoke test
  (`scripts/smoke_test.gd`, 98 checks: drives the real Main scene through the real engine + sandbox, including the
  whole Enhance flow: plan, preview, enhance, disable, enable, remove).

## Compatibility checks (`tweeq/compat.py`, CLI `check RACE TAG [--zones a,b]`)
Offline, read-only findings (error / warn / info) before a swap, from `data/model_compat.json`
(built by `tools/build_compat_index.py`). The UI shows them under the model picker, prefills the recommended
native height, and disables Add swap on an error. Checks: same model; unknown model; no animation clips;
clips most models of that format have but the new one lacks (same-format only; WLD/EQG vocabularies differ);
weapon/shield slot bones by exact name (EQG ARMR_WEAP/ARML_WEAP/ARML_SHLD, WLD R_POINT/L_POINT);
weapon bone parented under the HAND (not forearm); weapon animation tracks far (>0.25u) from bind in most
clips (the CTH defect; also flags bur, bal, mbr, glm, myg); extra arms; native-height mismatch with a
suggested value; impact (NPC types, spawn zones from PEQ) and zones the race spawns in that you did not select.
Not covered yet: simulated-grip pose check, texture-variant counts, WLD weapon-track check, client behaviour
when a slot bone is missing (untested in-game). The index is built from the Opus weapon survey outputs in
builds/cth_weapfix/ — fold those survey scripts into tweeq before shipping.

## Not built yet
- Settings dialog (paths are in %APPDATA%\Tweeq\config.json), `.mod`-format character previews (135 models), static WLD previews, async for the non-preview calls, an equipment/texture-variant view
- WLD (classic model) Enhance: needs per-family verification (tools/caz_upgrade.py + wld_mesh_splice.py are the dev starting point)
- Fix tooling for flagged models (neutralise bad tracks / relink the weapon bone: builds/cth_weapfix, untested in-game)
- Retag + GlobalLoad.txt fallback (needed only when a tag can't be changed; tools/eqg_model_swap.py)
- Texture-variant choice, size helper, per-NPC (not per-race) targeting is impossible client-side
- License gate, PyInstaller freeze, Windows packaging
