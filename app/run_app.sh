#!/bin/bash
# Launch the Tweeq window (dev): sync the Godot project to the C drive and open it.
#   ./run_app.sh            normal window
# Defaults to the vanilla SANDBOX (app/sandbox). Rebuild it any time with:  python3 make_sandbox.py
# Point it at a real install by editing %APPDATA%\Tweeq\config.json ("eq", "vault" as WSL paths).
set -e
cd "$(dirname "$0")"
[ -d sandbox/EverQuest ] || python3 make_sandbox.py
DST=/mnt/c/Users/brian/tweeq_ui
mkdir -p "$DST"
rsync -a --delete --exclude '.godot' --exclude 'sync_and_test.sh' ui/ "$DST/"
# Compile-check the app's scripts first (~3 s): a script error would otherwise open a blank gray window.
CONSOLE=/mnt/c/Users/brian/Downloads/Godot_v4.7.2-stable_win64.exe/Godot_v4.7.2-stable_win64_console.exe
ERR=""
for s in main viewer_pane backend; do
	# --check-only does not register autoloads, so "Identifier not found: Backend" is a false alarm; drop only that
	OUT=$("$CONSOLE" --headless --path 'C:\Users\brian\tweeq_ui' --check-only --script res://scripts/$s.gd 2>&1 \
		| awk '/SCRIPT ERROR/{keep = ($0 !~ /Identifier not found: Backend/)} keep && /SCRIPT ERROR|   at:/' || true)
	[ -n "$OUT" ] && ERR="$ERR\n== $s.gd\n$OUT"
done
if [ -n "$ERR" ]; then printf "NOT LAUNCHED - script errors:$ERR\n"; exit 1; fi
GODOT=/mnt/c/Users/brian/Downloads/Godot_v4.7.2-stable_win64.exe/Godot_v4.7.2-stable_win64.exe
nohup "$GODOT" --path 'C:\Users\brian\tweeq_ui' >/dev/null 2>&1 &
echo "launched: pick a zone on the left (e.g. gfaydark)"
