# Tweeq

Swap EverQuest character models. Pick a zone and NPC (or a race), preview the current model (A) next to any
installed replacement (B) in 3D, check that the swap is compatible, and apply it. Every change is reversible:
originals are vaulted on first touch and `apply` replays your swaps onto the current files, so patches don't lose them.

> **Not affiliated with or endorsed by Daybreak Game Company.** EverQuest is a trademark of Daybreak Game Company LLC.
> Tweeq contains no game files. It reads and edits the copy of EverQuest you already have installed.

## Status
Early. The engine (`app/tweeq`, Python, JSON CLI) and the Godot 4 UI (`app/ui`) work, but some paths in the scripts
are still hardcoded to the author's machine (WSL + Windows) and there is no packaged release yet.

## Enhance
Pick any EQG model (Luclin and later, e.g. Cazic-Thule) and make it look better: **more polygons** (every triangle split
once or twice, 4x or 16x the triangles) and/or **bigger textures** (512 or 1024 pixels). Tweeq shows what the result
will be first, lets you preview it next to the original, and writes it like a swap: the original file is vaulted and
comes straight back when you disable or remove the enhancement. Classic (pre-Luclin) models are not supported yet.

Textures use a built-in resize by default. For sharper results Tweeq can use the free
[Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) upscaler, which you install from inside the app ("Get AI upscaler",
checksum-verified, needs a Vulkan-capable GPU). It is never bundled in this repository: its model weights were trained on
third-party image sets (DIV2K is academic-research-only), so Tweeq downloads it only on your request.

## How it works
A swap edits the client's `racedata.txt` model tag (and height) and makes sure each zone's `<zone>_chr.txt` lists the
new model. Compatibility checks cover weapon bones, animation clips, height and borrowed animations.
See `app/README.md` for the engine, CLI and tests.

## Requirements
- Python 3 with Pillow and NumPy, Godot 4.x (UI)
- [quail](https://github.com/xackery/quail) v1.6.0 (MIT) for WLD/S3D conversion — not bundled in this repo
- An EverQuest install

## Data
`data/peq_slim.sqlite` is a slimmed extract (zones, NPC types, spawns) of the ProjectEQ/EQEmu database, distributed under
the GPL along with the rest of this project. It powers the zone -> NPC lists. Rebuild or refresh it from a PEQ dump
with `tools/peq_load.py`.

## License
GPL-3.0-or-later — see `LICENSE`. Third-party components: `THIRD_PARTY_LICENSES.md`.
