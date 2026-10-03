#!/usr/bin/env python3
"""Dev wrapper: rebuild the model index + compatibility data for an EverQuest install.

Tweeq does this by itself on first launch (`tweeq scan`); this script just runs the same code by hand
and writes into data/ (or another folder), which is handy when comparing against a known-good dump:

    python3 tools/build_compat_index.py "$EQ" [OUT_DIR]      # OUT_DIR defaults to ./data
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "app"))
from tweeq import scan  # noqa: E402


def main(eq_dir, out_dir):
    r = scan.run(eq_dir, out_dir, progress=lambda stage, pct: print("  %3d%% %s" % (pct * 100, stage), flush=True))
    print(r)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "..", "data"))
