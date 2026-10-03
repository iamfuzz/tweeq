#!/bin/bash
# Automated checks of a built installer (V1-V9 of the release plan). Run after build_release.sh:  release/verify_release.sh
#
# SAFETY: everything happens in a throw-away folder (C:\Users\brian\eq_releases\test\<ver>) and the sandbox copy of
# EverQuest. The REAL EverQuest folder is only READ (the first-run scan, V6); a checksum snapshot of it and of the real
# %APPDATA%\Tweeq vault is taken before and after and the script fails if anything changed.
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
OUT_WIN="${OUT_WIN:-/mnt/c/Users/brian/eq_releases}"
VER="$(cd "$REPO/app" && python3 -c 'import tweeq; print(tweeq.__version__)')"
SETUP="$OUT_WIN/Tweeq-Setup-$VER.exe"
B="$OUT_WIN/build/$VER"
T="$OUT_WIN/test/$VER"
REAL_EQ="${REAL_EQ:-/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest}"
REAL_APPDATA="${REAL_APPDATA:-/mnt/c/Users/brian/AppData/Roaming/Tweeq}"
W() { wslpath -w "$1"; }
PASS=0; FAIL=0
ok()   { PASS=$((PASS+1)); printf 'PASS  %s\n' "$*"; }
bad()  { FAIL=$((FAIL+1)); printf 'FAIL  %s\n' "$*"; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
json_ok() { python3 -c 'import sys,json; d=json.loads(sys.stdin.read().strip().splitlines()[-1]); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null; }
json_get() { python3 -c 'import sys,json; d=json.loads(sys.stdin.read().strip().splitlines()[-1]); v=d
for k in sys.argv[1].split("."):
    v=v[int(k)] if k.isdigit() else v[k]
print(v)' "$1" 2>/dev/null; }

snapshot() {   # sha256 of everything verification must never change
  { find "$REAL_EQ" -maxdepth 1 -type f \( -name 'racedata.txt' -o -name '*_chr.txt' -o -name 'cth.eqg' -o -name 'tmt_chr.s3d' \) -print0 | sort -z | xargs -0 sha256sum
    sha256sum "$REAL_EQ/Resources/GlobalLoad.txt"
    [ -d "$REAL_APPDATA/vault" ] && find "$REAL_APPDATA/vault" -type f -print0 | sort -z | xargs -0 sha256sum; } 2>/dev/null | sha256sum | cut -d' ' -f1
}

[ -f "$SETUP" ] || { echo "no installer at $SETUP: run build_release.sh first"; exit 2; }
DESKTOP="$(powershell.exe -NoProfile -Command "[Environment]::GetFolderPath('Desktop')" | tr -d '\r')"
STARTMENU="$(powershell.exe -NoProfile -Command "[Environment]::GetFolderPath('Programs')" | tr -d '\r')"
LNK_D="$(wslpath "$DESKTOP")/Tweeq.lnk"; LNK_S="$(wslpath "$STARTMENU")/Tweeq.lnk"
UNKEY='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6C1B7B52-3E0F-4E57-9A53-7D2E61F4A8B1}_is1'

cleanup() { [ -f "$T/inst/unins000.exe" ] && "$T/inst/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/TWEEQDATA=$(W "$T/appdata")" /KEEPMODS=1 >/dev/null 2>&1; true; }
trap cleanup EXIT

say() { printf '\n== %s\n' "$*"; }
BEFORE="$(snapshot)"
rm -rf "$T"; mkdir -p "$T/appdata"
IDX_DIR="$T/appdata/installs/sandbox"

say "V1  silent install"
"$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$(W "$T/inst")" "/LOG=$(W "$T/install.log")"; rc=$?
check "installer exits 0 (got $rc)" "[ $rc -eq 0 ]"
for f in Tweeq.exe python/python.exe python/python314.dll python/python314._pth bin/quail.exe data/peq_slim.sqlite data/model_names.json \
         app/tweeq/cli.py tools/s3d.py licenses/LICENSE-GPL-3.0.txt licenses/THIRD_PARTY_LICENSES.md licenses/SOURCE.txt README.txt unins000.exe; do
  check "installed: $f" "[ -s '$T/inst/$f' ]"
done
check "no tests or dev data shipped" "[ ! -e '$T/inst/app/tests' ] && [ ! -e '$T/inst/data/installed_model_index.json' ] && [ ! -e '$T/inst/data/model_compat.json' ]"
check "no EverQuest game files shipped" "! find '$T/inst' \( -name '*.s3d' -o -name '*.eqg' -o -name 'racedata.txt' \) | grep -q ."
ls -la "$SETUP" | awk '{printf "      installer size: %.1f MB\n", $5/1048576}'

say "V2  shortcuts and uninstall entry"
LT="$(powershell.exe -NoProfile -Command "(New-Object -ComObject WScript.Shell).CreateShortcut('$(W "$LNK_D")').TargetPath" | tr -d '\r')"
check "desktop shortcut targets Tweeq.exe ($LT)" "[ \"\$(wslpath '$LT' 2>/dev/null)\" = '$T/inst/Tweeq.exe' ]"
LT2="$(powershell.exe -NoProfile -Command "(New-Object -ComObject WScript.Shell).CreateShortcut('$(W "$LNK_S")').TargetPath" | tr -d '\r')"
check "Start Menu shortcut targets Tweeq.exe" "[ \"\$(wslpath '$LT2' 2>/dev/null)\" = '$T/inst/Tweeq.exe' ]"
DN="$(powershell.exe -NoProfile -Command "(Get-ItemProperty '$UNKEY').DisplayName" | tr -d '\r')"
check "Add/Remove Programs entry: '$DN'" "[ '$DN' = 'Tweeq' ]"
PUB="$(powershell.exe -NoProfile -Command "(Get-ItemProperty '$UNKEY').Publisher" | tr -d '\r')"
check "publisher is '$PUB'" "[ '$PUB' = 'Brian Thomason' ]"
FV="$(powershell.exe -NoProfile -Command "(Get-Item '$(W "$T/inst/Tweeq.exe")').VersionInfo | ForEach-Object { \$_.ProductName + '|' + \$_.FileVersion + '|' + \$_.CompanyName }" | tr -d '\r')"
check "Tweeq.exe version resource: $FV" "[ '$FV' = 'Tweeq|$VER.0|Brian Thomason' ]"

PY="$T/inst/python/python.exe"
say "V3  bundled engine"
check "bundled python imports numpy, PIL, sqlite3, tweeq" "'$PY' -X utf8 -B -c 'import numpy, PIL, sqlite3, tweeq; print(tweeq.__version__)' | grep -q '$VER'"
check "quail.exe runs" "'$T/inst/bin/quail.exe' --help >/dev/null 2>&1"
check "engine reports its version" "'$PY' -X utf8 -B -m tweeq.cli --json discover --check '$(W "$REAL_EQ")' | json_ok"

say "V4  bundled engine against the sandbox (never the real install)"
SBX="$T/sbx4/EverQuest"; mkdir -p "$T/sbx4"; cp -r "$REPO/app/sandbox/EverQuest" "$SBX"
cp "$SBX/racedata.txt" "$T/racedata.vanilla"
eng() { "$PY" -X utf8 -B -m tweeq.cli --json --eq "$(W "$SBX")" --vault "$(W "$T/appdata/vault")" --index "$(W "$IDX_DIR/installed_model_index.json")" --peq "$(W "$T/inst/data/peq_slim.sqlite")" "$@"; }
out="$(eng scan)";                   check "scan sandbox ($(echo "$out" | json_get data.models) model)" "echo '$out' | json_ok"
out="$(eng info)";                   check "info" "echo '$out' | json_ok"
out="$(eng models --filter cth)";    check "models lists cth" "[ \"\$(echo '$out' | json_get data.0.tag)\" = cth ]"
out="$(eng preview cth --out "$(W "$T/prev")")"; check "preview cth makes a .glb" "echo '$out' | json_ok && [ \"\$(echo '$out' | json_get data.status)\" = ok ] && [ -s '$T/prev/cth.glb' ]"
out="$(eng enhance-plan cth --passes 1)"; check "enhance-plan cth: $(echo "$out" | json_get data.models.0.faces_after) faces" "[ \"\$(echo '$out' | json_get data.models.0.faces_after)\" = 12540 ]"
out="$(eng enhance-preview cth --passes 1 --out "$(W "$T/prev")")"; check "enhance-preview cth makes a .glb" "[ \"\$(echo '$out' | json_get data.status)\" = ok ]"
out="$(eng swap 95 cth --zones all)"; check "swap" "echo '$out' | json_ok"
out="$(eng apply)";                  check "apply" "echo '$out' | json_ok"
check "the sandbox racedata really changed" "! cmp -s '$SBX/racedata.txt' '$T/racedata.vanilla'"

say "V5  unit tests under the bundled python"
mkdir -p "$T/testrun"; rm -rf "$T/testrun/tests"; cp -r "$REPO/app/tests" "$T/testrun/tests"
( cd "$T/testrun" && "$PY" -X utf8 -B -W error::ResourceWarning -m unittest discover -s tests -t . > "$T/unittests.log" 2>&1 ); rc=$?
tail -4 "$T/unittests.log" | sed 's/^/      /'
check "unit tests pass under the bundled python" "[ $rc -eq 0 ]"

say "V6  first-run scan of the REAL install (read-only) - timing"
RV="$T/appdata/installs/real"
t0=$(date +%s)
out="$("$PY" -X utf8 -B -m tweeq.cli --json --eq "$(W "$REAL_EQ")" --vault "$(W "$T/appdata/vault_real_test")" --index "$(W "$RV/installed_model_index.json")" scan)"
t1=$(date +%s)
check "real-install scan ok: $(echo "$out" | json_get data.models) models in $((t1-t0)) s (wall, from WSL: slower than native)" "echo '$out' | json_ok"
check "model count matches the dev index" "[ \"\$(echo '$out' | json_get data.entries)\" = \"\$(python3 -c \"import json;print(len(json.load(open('$REPO/data/installed_model_index.json'))['models']))\")\" ]"
check "compat data equals the dev file" "python3 -c \"import json,sys; a=json.load(open('$RV/model_compat.json')); b=json.load(open('$REPO/data/model_compat.json')); sys.exit(0 if a==b else 1)\""

say "V7  headless UI smoke test of the exported TEST build (sandbox + real model files, read-only)"
SBX7="$T/sbx7/EverQuest"; mkdir -p "$T/sbx7"; cp -r "$REPO/app/sandbox/EverQuest" "$SBX7"
taskkill.exe /F /IM Tweeq-test.exe >/dev/null 2>&1      # a hung earlier run would leave the exe locked (timeout only kills the WSL side)
cp "$B/test/Tweeq-test.exe" "$T/inst/Tweeq-test.exe" || bad "could not copy the test build (is an old one still running?)"
# A saved settings.json means "not first run": otherwise the app's own first-run discovery would find the REAL install
# in the background and switch the test over to it mid-run.
printf '{"eq": "%s"}\n' "$(W "$SBX7" | sed 's#\\#/#g')" > "$T/appdata/settings.json"
export TWEEQ_DATA_ROOT="$(W "$T/appdata")" TWEEQ_SANDBOX_EQ="$(W "$SBX7")" TWEEQ_SANDBOX_VAULT="$(W "$T/appdata/vault_smoke")" \
       TWEEQ_MODELS="$(W "$REAL_EQ")" TWEEQ_INDEX="$(W "$RV/installed_model_index.json")" TWEEQ_SKIP_SCAN=1
export WSLENV="TWEEQ_DATA_ROOT:TWEEQ_SANDBOX_EQ:TWEEQ_SANDBOX_VAULT:TWEEQ_MODELS:TWEEQ_INDEX:TWEEQ_SKIP_SCAN"
timeout 900 "$T/inst/Tweeq-test.exe" --headless > "$T/smoke.log" 2>&1; rc=$?
unset TWEEQ_DATA_ROOT TWEEQ_SANDBOX_EQ TWEEQ_SANDBOX_VAULT TWEEQ_MODELS TWEEQ_INDEX TWEEQ_SKIP_SCAN WSLENV
echo "      smoke: $(grep -c '^PASS' "$T/smoke.log") PASS, $(grep -c '^FAIL' "$T/smoke.log") FAIL, exit $rc"
grep -E '^FAIL|SCRIPT ERROR' "$T/smoke.log" | head -5 | sed 's/^/      /'
check "installed-build smoke test passes (GLB loading in an export, bundled python/quail)" "[ $rc -eq 0 ] && grep -q 'DONE failures=0' '$T/smoke.log'"
taskkill.exe /F /IM Tweeq-test.exe >/dev/null 2>&1
rm -f "$T/inst/Tweeq-test.exe"

say "V8  uninstall puts the files back, keeps the vault, removes the app"
"$T/inst/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/TWEEQDATA=$(W "$T/appdata")" "/LOG=$(W "$T/uninstall.log")"; rc=$?
check "uninstaller exits 0 (got $rc)" "[ $rc -eq 0 ]"
check "sandbox racedata is byte-identical to vanilla again" "cmp -s '$SBX/racedata.txt' '$T/racedata.vanilla'"
check "the vault was kept" "[ -f '$T/appdata/vault/manifest.json' ]"
check "install folder is gone" "[ ! -e '$T/inst/Tweeq.exe' ] && [ ! -e '$T/inst/python' ]"
check "desktop and Start Menu shortcuts are gone" "[ ! -e '$LNK_D' ] && [ ! -e '$LNK_S' ]"
check "uninstall entry is gone" "! powershell.exe -NoProfile -Command \"Test-Path '$UNKEY'\" | tr -d '\r' | grep -q True"

say "V8b /KEEPMODS=1 leaves the game files alone"
"$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$(W "$T/inst")" >/dev/null 2>&1
SBXB="$T/sbx8b/EverQuest"; mkdir -p "$T/sbx8b"; cp -r "$REPO/app/sandbox/EverQuest" "$SBXB"; cp "$SBXB/racedata.txt" "$T/racedata8b.vanilla"
"$PY" -X utf8 -B -m tweeq.cli --json --eq "$(W "$SBXB")" --vault "$(W "$T/appdata8b/vault")" --index "$(W "$IDX_DIR/installed_model_index.json")" swap 95 cth --zones all >/dev/null
"$PY" -X utf8 -B -m tweeq.cli --json --eq "$(W "$SBXB")" --vault "$(W "$T/appdata8b/vault")" --index "$(W "$IDX_DIR/installed_model_index.json")" apply >/dev/null
"$T/inst/unins000.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/TWEEQDATA=$(W "$T/appdata8b")" /KEEPMODS=1; rc=$?
check "uninstaller exits 0 (got $rc)" "[ $rc -eq 0 ]"
check "files NOT restored with /KEEPMODS=1" "! cmp -s '$SBXB/racedata.txt' '$T/racedata8b.vanilla'"
check "app data kept with /KEEPMODS=1" "[ -f '$T/appdata8b/vault/manifest.json' ]"

say "V9  upgrade over an existing install"
"$SETUP" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$(W "$T/inst")" >/dev/null 2>&1
mkdir -p "$T/inst/app"; echo stale > "$T/inst/app/stale_file.txt"
mkdir -p "$T/up"; ISCC="$OUT_WIN/_tools/InnoSetup6/ISCC.exe"
( cd "$B" && "$ISCC" /Qp "/DAppVersion=$VER-upgrade" "/DAppVersion4=$VER.1" "/DStage=$(W "$B/stage")" "/DOutDir=$(W "$T/up")" /DFastCompression "$(W "$B/tweeq.iss")" ) >/dev/null 2>&1
"$T/up/Tweeq-Setup-$VER-upgrade.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART "/DIR=$(W "$T/inst")"; rc=$?
check "upgrade installer exits 0 (got $rc)" "[ $rc -eq 0 ]"
check "stale file from the old install was removed" "[ ! -e '$T/inst/app/stale_file.txt' ] && [ -s '$T/inst/app/tweeq/cli.py' ]"
N="$(powershell.exe -NoProfile -Command "(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall' | Where-Object { \$_.PSChildName -like '{6C1B7B52*' }).Count" | tr -d '\r')"
check "exactly one Add/Remove entry after upgrading (found $N)" "[ '$N' = 1 ]"
cleanup

say "safety: the real EverQuest folder and vault were not changed"
AFTER="$(snapshot)"
check "real install + real vault checksums unchanged" "[ '$BEFORE' = '$AFTER' ]"

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
