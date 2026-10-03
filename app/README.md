# eqswap — EQ model swapper (engine + CLI)

v1 of the Mob Model Manager is a **model-swapping** app (Enhance is deferred; see
../PLAN_mob_model_manager.md). This directory is the headless engine that the Godot UI will drive.

## How a swap works (proven in-game 2026-10-01)
1. `racedata.txt`: set field 50 (model tag) of the race/gender row; optionally fields 46/47 (native height).
2. `<zone>_chr.txt`: ensure the zone loads the model (`tag,tag` EQG / `tag,tag_chr` WLD; none if the
   archive is already loaded globally per `Resources/GlobalLoad.txt`).

No game files are copied or shipped. Decisions live in a manifest and are **replayed onto the current
files**, so `apply` after an official patch re-creates every swap. Originals are vaulted on first touch.

## Layout
- `eqswap/racedata.py`, `zonelist.py` — byte-exact format layers (only edited fields change)
- `eqswap/models.py` — resolves a tag via `data/installed_model_index.json` (tools/scan_install.py) + GlobalLoad.txt
- `eqswap/engine.py` — `Swapper`: add/remove/enable swaps, `status`, `apply`, `restore_all`, drift handling
- `eqswap/peq.py` — optional zone discovery from a user-imported PEQ db (GPL data, never bundled)
- `eqswap/cli.py` — `python -m eqswap.cli --eq EQ --vault VAULT --index IDX {swap,plan,apply,status,list,restore,...}`
- `tests/` — `python3 -W error::ResourceWarning -m unittest discover -s tests -t .`
  (includes an integration test that rebuilds the live PoTime Cazic edit byte-for-byte from the vanilla backups)

## Status values
`clean` / `applied` / `needs-apply` (patch restored vanilla, or decisions changed) / `drifted`
(file changed by something else; `apply --accept-drift` adopts it as the new original) / `missing`.

## Run it (MVP)
`./run_app.sh` opens the window. Pick a zone on the left (type to filter, e.g. `gfaydark`), then one of its NPC
models: pane A shows that NPC's model, pane B a second character (default: the next different model in the zone;
pick any installed model from the list to replace it). Drag to orbit, wheel to zoom; each pane is independent.
Previews are generated on first view (WLD ~0.5-7 s, EQG ~2 s, on a background thread) and cached under
%APPDATA%\EQSwapper\previews (cache key = archive size/mtime + converter version). It defaults to the vanilla
sandbox, so Apply/Restore never touch your real EverQuest folder.

## Godot UI shell (app/ui, Godot 4.7.2)
Races (filter by id / tag / NPC) -> pick a replacement model -> pick zones (PEQ spawn zones pre-selected
when a PEQ import exists) -> Add swap -> Apply all / Restore all. Two isolated 3D panes (A current /
B candidate) load any .glb at runtime (`scripts/viewer_pane.gd`); previews come from `ui/previews/<tag>.glb`
(generated on demand by `eqswap/preview.py` -> tools/mds_to_gltf.py / wce_chr_to_gltf.py). `scripts/backend.gd` is the only bridge: one
process per call, one JSON object back. Dev launcher = `wsl.exe -d Ubuntu ... python3 -m eqswap.cli`
(default WSL distro here is docker-desktop, so `-d Ubuntu` matters); a packaged build swaps in eq_toolkit.exe.
- Engine commands the UI uses: `npcs ZONE` (PEQ NPC groups + current tag + clip count), `preview TAG --out DIR` (model archives are read from `--models`, default `--eq`), plus races/models/zones/check/swap/apply/restore.
- `python3 make_sandbox.py` builds `app/sandbox/EverQuest` from VANILLA files; the UI defaults to it.
  Do NOT point the app at an install carrying hand-applied edits: the first apply vaults whatever is live as the "original".
- `ui/sync_and_test.sh` syncs to C:\Users\brian\eq_swapper_ui and runs the headless smoke test
  (`scripts/smoke_test.gd`, 21 checks: drives the real Main scene through the real engine + sandbox).

## Compatibility checks (`eqswap/compat.py`, CLI `check RACE TAG [--zones a,b]`)
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
builds/cth_weapfix/ — fold those survey scripts into eq_toolkit before shipping.

## Not built yet
- Settings dialog (paths are in %APPDATA%\EQSwapper\config.json), `.mod`-format character previews (135 models), static WLD previews, async for the non-preview calls, an equipment/texture-variant view
- Fix tooling for flagged models (neutralise bad tracks / relink the weapon bone: builds/cth_weapfix, untested in-game)
- Retag + GlobalLoad.txt fallback (needed only when a tag can't be changed; tools/eqg_model_swap.py)
- Texture-variant choice, size helper, per-NPC (not per-race) targeting is impossible client-side
- License gate, PyInstaller freeze, Windows packaging
