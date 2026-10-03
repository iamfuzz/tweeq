#!/usr/bin/env python3
"""Build a throwaway EQ-shaped folder for developing/testing the UI safely.

Takes VANILLA racedata/GlobalLoad/zone lists (from the launcher's vanilla backups where we
ever edited a file, otherwise from the live install) so the sandbox never contains our
hand-applied test edits.  Output: app/sandbox/EverQuest  (never touches the real install).

    python3 make_sandbox.py [--live EQ_DIR] [--vanilla VANILLA_DIR]
"""
import argparse
import glob
import os
import shutil

LIVE = "/mnt/c/Users/Public/Daybreak Game Company/Installed Games/EverQuest"
VANILLA = "/mnt/c/Users/brian/EQ_Launcher/backups/vanilla"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sandbox", "EverQuest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", default=LIVE)
    ap.add_argument("--vanilla", default=VANILLA)
    a = ap.parse_args()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "Resources"))
    n = 0
    for p in glob.glob(os.path.join(a.live, "*_chr.txt")) + [os.path.join(a.live, "racedata.txt")]:
        name = os.path.basename(p)
        van = os.path.join(a.vanilla, name)
        shutil.copy(van if os.path.exists(van) else p, os.path.join(OUT, name))
        n += 1
    van = os.path.join(a.vanilla, "GlobalLoad.txt")
    shutil.copy(van if os.path.exists(van) else os.path.join(a.live, "Resources", "GlobalLoad.txt"),
                os.path.join(OUT, "Resources", "GlobalLoad.txt"))
    print(f"sandbox: {OUT} ({n} files + GlobalLoad.txt)")


if __name__ == "__main__":
    main()
