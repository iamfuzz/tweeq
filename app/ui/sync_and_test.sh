#!/bin/bash
# Sync the UI project to the C drive (Godot on Windows) and run the headless smoke test.
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/mnt/c/Users/brian/eq_swapper_ui
GODOT="/mnt/c/Users/brian/Downloads/Godot_v4.7.2_stable_win64.exe"
GODOT=$(ls /mnt/c/Users/brian/Downloads/Godot_v4.7.2-stable_win64.exe/Godot_v4.7.2-stable_win64_console.exe)
mkdir -p "$DST"
rsync -a --delete --exclude '.godot' --exclude 'sync_and_test.sh' "$SRC/" "$DST/"
"$GODOT" --headless --path 'C:\Users\brian\eq_swapper_ui' --import >/dev/null 2>&1 || true
"$GODOT" --headless --path 'C:\Users\brian\eq_swapper_ui' --script res://scripts/smoke_test.gd
