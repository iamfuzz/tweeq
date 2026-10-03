#!/bin/bash
# Build the one-file Windows installer:  release/build_release.sh
#
# Runs in WSL2. Downloads and verifies the pinned inputs (release/pins.env), cross-builds quail.exe, assembles the install
# layout, exports Tweeq.exe with Godot, and compiles the installer with Inno Setup. Output goes to $OUT_WIN
# (default C:\Users\brian\eq_releases). It never touches an EverQuest folder or %APPDATA%\Tweeq.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/pins.env"

OUT_WIN="${OUT_WIN:-/mnt/c/Users/brian/eq_releases}"
CACHE="${TWEEQ_CACHE:-$HOME/.cache/tweeq-release}"
GODOT_CONSOLE="${GODOT_CONSOLE:-/mnt/c/Users/brian/Downloads/Godot_v4.7.2-stable_win64.exe/Godot_v4.7.2-stable_win64_console.exe}"
VER="$(cd "$REPO/app" && python3 -c 'import tweeq; print(tweeq.__version__)')"
VER4="$VER.0"
B="$OUT_WIN/build/$VER"          # build tree (on C: so Windows programs can read it)
STAGE="$B/stage"
TOOLS_WIN="$OUT_WIN/_tools"
WINP() { wslpath -w "$1"; }
say() { printf '\n== %s\n' "$*"; }
die() { printf 'BUILD FAILED: %s\n' "$*" >&2; exit 1; }
trap 'printf "BUILD FAILED at line %s: %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

fetch() {   # fetch URL OUT ALGO SUM : download once, verify every time
  local url=$1 out=$2 algo=$3 sum=$4
  mkdir -p "$(dirname "$out")"
  if [ ! -f "$out" ]; then
    echo "downloading $(basename "$out")"
    curl -fL --retry 3 -o "$out.part" "$url" && mv "$out.part" "$out"
  fi
  echo "$sum  $out" | "${algo}sum" -c --status - || { rm -f "$out"; die "checksum mismatch for $(basename "$out")"; }
}

say "0. preflight (version $VER)"
mkdir -p "$CACHE" "$OUT_WIN"
[ -x "$GODOT_CONSOLE" ] || die "Godot console editor not found at $GODOT_CONSOLE (set GODOT_CONSOLE)"
for f in app/ui/icon.ico app/ui/icon.png app/ui/export_presets.cfg release/tweeq.iss; do [ -f "$REPO/$f" ] || die "missing $f (run release/make_icon.py?)"; done
(cd "$REPO/app" && python3 -W error::ResourceWarning -m unittest discover -s tests -t . >/tmp/tweeq-prebuild-tests.log 2>&1) \
  || { tail -20 /tmp/tweeq-prebuild-tests.log; die "unit tests fail; not building"; }
rm -rf "$B"; mkdir -p "$STAGE"/{bin,data,licenses,app,tools}

say "1. bundled Python $PY_VER + wheels"
fetch "$PY_URL" "$CACHE/python-$PY_VER-embed-amd64.zip" sha256 "$PY_SHA256"
python3 -m zipfile -e "$CACHE/python-$PY_VER-embed-amd64.zip" "$STAGE/python"
cat > "$STAGE/python/python${PY_TAG}._pth" <<PTH
python${PY_TAG}.zip
.
..\\app
..\\tools
Lib\\site-packages
import site
PTH
mkdir -p "$STAGE/python/Lib/site-packages"
python3 -m pip install --quiet --target "$STAGE/python/Lib/site-packages" --platform win_amd64 --python-version 3.14 \
  --implementation cp --abi cp314 --only-binary=:all: --no-deps --require-hashes --disable-pip-version-check \
  -r "$HERE/requirements-win.txt" 2>&1 | tail -3 || true
[ -d "$STAGE/python/Lib/site-packages/numpy" ] && [ -d "$STAGE/python/Lib/site-packages/PIL" ] || \
  python3 -m pip install --quiet --break-system-packages --target "$STAGE/python/Lib/site-packages" --platform win_amd64 \
    --python-version 3.14 --implementation cp --abi cp314 --only-binary=:all: --no-deps --require-hashes \
    --disable-pip-version-check -r "$HERE/requirements-win.txt"
[ -d "$STAGE/python/Lib/site-packages/numpy" ] && [ -d "$STAGE/python/Lib/site-packages/PIL" ] || die "numpy/Pillow did not install"
rm -rf "$STAGE/python/Lib/site-packages/bin"      # numpy's f2py/numpy-config launcher stubs: not needed
find "$STAGE/python/Lib/site-packages" \( -path '*/numpy/*/tests' -o -path '*/numpy/tests' -o -path '*/PIL/../pillow-*/tests' \) -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$STAGE/python" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

say "2. Tweeq sources"
rsync -a --exclude '__pycache__' --exclude '*.pyc' "$REPO/app/tweeq/" "$STAGE/app/tweeq/"
for t in ani build_compat_index enhance_textures eqg_model_swap mds mds_subdivide mds_to_gltf peq_load rig_survey s3d scan_install wce_chr_to_gltf; do
  cp "$REPO/tools/$t.py" "$STAGE/tools/"
done
cp "$REPO/data/model_kinds.json" "$REPO/data/model_names.json" "$REPO/data/peq_slim.sqlite" "$STAGE/data/"
sed -i "s/^__version__ = .*/__version__ = \"$VER\"/" "$STAGE/app/tweeq/__init__.py"

say "3. quail.exe (built from source: $QUAIL_MODULE)"
( cd "$(mktemp -d)" && env -u GOBIN CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go install -trimpath -ldflags="-s -w" "$QUAIL_MODULE" )
cp "$(go env GOPATH)/bin/windows_amd64/quail.exe" "$STAGE/bin/quail.exe"
[ -s "$STAGE/bin/quail.exe" ] || die "quail.exe was not built"

say "4. Tweeq.exe (Godot $GODOT_VER export)"
TEMPLATE="$CACHE/godot-$GODOT_VER/windows_release_x86_64.exe"
if [ ! -f "$TEMPLATE" ]; then
  fetch "$GODOT_TPZ_URL" "$CACHE/godot-$GODOT_VER.tpz" sha512 "$GODOT_TPZ_SHA512"
  mkdir -p "$(dirname "$TEMPLATE")"
  python3 - "$CACHE/godot-$GODOT_VER.tpz" "$GODOT_TEMPLATE_MEMBER" "$TEMPLATE" <<'PY'
import sys, zipfile, shutil
tpz, member, out = sys.argv[1:4]
with zipfile.ZipFile(tpz) as z, z.open(member) as src, open(out, "wb") as dst:
    shutil.copyfileobj(src, dst)
PY
fi
[ -s "$TEMPLATE" ] || die "Godot template was not extracted"
# the Godot project is exported from a COPY on C: with the version and template path filled in
mkdir -p "$B/ui" "$B/test"
rsync -a --delete --exclude '.godot' --exclude 'previews' --exclude 'sync_and_test.sh' "$REPO/app/ui/" "$B/ui/"
TEMPLATE_WIN="$(WINP "$TEMPLATE" | sed 's#\\#/#g')"
sed -i "s#@GODOT_TEMPLATE@#$TEMPLATE_WIN#g; s#@VERSION4@#$VER4#g" "$B/ui/export_presets.cfg"
sed -i "s#^config/version=.*#config/version=\"$VER\"#" "$B/ui/project.godot"
"$GODOT_CONSOLE" --headless --path "$(WINP "$B/ui")" --import >/dev/null 2>&1 || true
"$GODOT_CONSOLE" --headless --path "$(WINP "$B/ui")" --export-release "Windows Desktop" "$(WINP "$STAGE/Tweeq.exe")" 2>&1 | tail -5
[ -s "$STAGE/Tweeq.exe" ] || die "Godot did not produce Tweeq.exe"
# The test build is the same project whose main loop IS the smoke test (an exported build ignores --script).
mkdir -p "$B/uitest"; rsync -a --delete "$B/ui/" "$B/uitest/"
sed -i 's#^\[application\]#[application]\n\nrun/main_loop_type="SmokeTestLoop"#' "$B/uitest/project.godot"
sed -i '1i class_name SmokeTestLoop' "$B/uitest/scripts/smoke_test.gd"
"$GODOT_CONSOLE" --headless --path "$(WINP "$B/uitest")" --import >/dev/null 2>&1 || true
"$GODOT_CONSOLE" --headless --path "$(WINP "$B/uitest")" --export-release "Windows Desktop (test)" "$(WINP "$B/test/Tweeq-test.exe")" 2>&1 | tail -3
[ -s "$B/test/Tweeq-test.exe" ] || die "Godot did not produce the test build"

say "5. licenses and notices"
L="$STAGE/licenses"
cp "$REPO/LICENSE" "$L/LICENSE-GPL-3.0.txt" 2>/dev/null || cp /home/brian/tweeq/LICENSE "$L/LICENSE-GPL-3.0.txt"
cp "$REPO/THIRD_PARTY_LICENSES.md" "$L/" 2>/dev/null || cp /home/brian/tweeq/THIRD_PARTY_LICENSES.md "$L/"
# licence texts are pinned by sha256 like every other input (fetch verifies, and caches under $CACHE)
fetch_lic() { fetch "$1" "$CACHE/licenses/$3" sha256 "$2"; cp "$CACHE/licenses/$3" "$4"; }
fetch_lic "$GODOT_LICENSE_URL" "$GODOT_LICENSE_SHA256" godot-LICENSE.txt "$L/godot-LICENSE.txt"
fetch_lic "$GODOT_COPYRIGHT_URL" "$GODOT_COPYRIGHT_SHA256" godot-COPYRIGHT.txt "$L/godot-COPYRIGHT.txt"
cp "$STAGE/python/LICENSE.txt" "$L/python-LICENSE.txt"
for d in "$STAGE"/python/Lib/site-packages/numpy-*.dist-info "$STAGE"/python/Lib/site-packages/pillow-*.dist-info; do
  n="$(basename "$d" | sed 's/-.*//')"
  find "$d" -iname 'LICEN*' -type f | head -3 | while read -r f; do cp "$f" "$L/$n-$(basename "$f")"; done
done
M="$(go env GOMODCACHE)"
cp "$M/${QUAIL_MODULE%@*}@${QUAIL_MODULE#*@}/LICENSE" "$L/quail-LICENSE.txt"
# The module cache escapes upper-case letters in a path ("Foo" is stored as "!foo"); a dependency with no licence file
# is reported loudly rather than silently skipped (or killing the build through a failing last `&&`).
go version -m "$(go env GOPATH)/bin/windows_amd64/quail.exe" | awk '$1=="dep"{print $2, $3}' | while read -r mod ver; do
  esc="$(printf '%s' "$mod" | sed -E 's/([A-Z])/!\L\1/g')"
  f="$(ls "$M/$esc@$ver"/LICENSE* "$M/$esc@$ver"/COPYING* 2>/dev/null | head -1 || true)"
  if [ -n "$f" ]; then cp "$f" "$L/go-dep-$(basename "$mod")-LICENSE.txt"; else echo "WARNING: no licence file found for Go dependency $mod@$ver" >&2; fi
done
fetch_lic "$GO_LICENSE_URL" "$GO_LICENSE_SHA256" go-LICENSE.txt "$L/go-LICENSE.txt"
sed "s/@VERSION@/$VER/g" "$HERE/SOURCE.txt.in" > "$L/SOURCE.txt"
sed "s/@VERSION@/$VER/g" "$HERE/README.txt.in" > "$STAGE/README.txt"

say "6. installer (Inno Setup $INNO_VER)"
ISCC="$TOOLS_WIN/InnoSetup6/ISCC.exe"
if [ ! -f "$ISCC" ]; then
  fetch "$INNO_URL" "$CACHE/innosetup-$INNO_VER.exe" sha256 "$INNO_SHA256"
  mkdir -p "$TOOLS_WIN"; cp "$CACHE/innosetup-$INNO_VER.exe" "$TOOLS_WIN/innosetup.exe"
  ( cd "$TOOLS_WIN" && ./innosetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /CURRENTUSER /NOICONS "/DIR=$(WINP "$TOOLS_WIN/InnoSetup6")" )
  for _ in $(seq 1 60); do [ -f "$ISCC" ] && break; sleep 1; done
fi
[ -f "$ISCC" ] || die "Inno Setup is not installed at $ISCC"
cp "$HERE/tweeq.iss" "$REPO/app/ui/icon.ico" "$B/"
rm -f "$OUT_WIN/Tweeq-Setup-$VER.exe"
( cd "$B" && "$ISCC" /Qp "/DAppVersion=$VER" "/DAppVersion4=$VER4" "/DStage=$(WINP "$STAGE")" "/DOutDir=$(WINP "$OUT_WIN")" "$(WINP "$B/tweeq.iss")" )
SETUP="$OUT_WIN/Tweeq-Setup-$VER.exe"
[ -s "$SETUP" ] || die "the installer was not produced"

say "7. done"
( cd "$OUT_WIN" && sha256sum "Tweeq-Setup-$VER.exe" | tee "Tweeq-Setup-$VER.exe.sha256" )
ls -l "$SETUP"
echo "stage: $STAGE   test export: $B/test/Tweeq-test.exe"
