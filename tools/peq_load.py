#!/usr/bin/env python3
"""Load the zone/NPC/spawn tables of a PEQ MySQL dump into a small SQLite file.

DEV/IMPORT TOOL. The PEQ database is GPL data: it is read from the user's own
download and never bundled. Only the column *shape* is hardcoded here.

    python3 tools/peq_load.py data/external/peq-dump/create_tables_content.sql \
        data/peq_slim.sqlite

Column order comes from each table's CREATE TABLE, so a schema that gains
columns keeps working. Missing wanted columns are reported, not guessed.
"""
import re
import sqlite3
import sys

KEEP = {
    "npc_types": ["id", "name", "level", "race", "gender", "texture",
                  "helmtexture", "size", "bodytype", "face"],
    "spawn2": ["id", "spawngroupID", "zone", "version", "x", "y", "z", "heading"],
    "spawngroup": ["id", "name"],
    "spawnentry": ["spawngroupID", "npcID", "chance"],
    "zone": ["zoneidnumber", "short_name", "long_name", "expansion"],
}


def table_columns(path):
    cols, cur = {}, None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"CREATE TABLE `(\w+)`", line)
            if m:
                cur = m.group(1) if m.group(1) in KEEP else None
                if cur:
                    cols[cur] = []
                continue
            if cur:
                m = re.match(r"\s+`(\w+)` ", line)
                if m:
                    cols[cur].append(m.group(1))
                elif line.startswith(")"):
                    cur = None
    return cols


def parse_values(text_iter):
    """Yield row tuples from the text following 'VALUES' up to the closing ';'."""
    row, tok, in_str, esc, started = [], [], False, False, False
    quoted = False
    for chunk in text_iter:
        for ch in chunk:
            if in_str:
                if esc:
                    tok.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0"}.get(ch, ch))
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == "'":
                    in_str = False
                else:
                    tok.append(ch)
                continue
            if ch == "'":
                in_str, quoted = True, True
            elif ch == "(" and not started:
                started, row, tok, quoted = True, [], [], False
            elif started and ch in ",)":
                s = "".join(tok)
                if quoted:
                    row.append(s)
                elif s == "NULL" or s == "":
                    row.append(None)
                else:
                    try:
                        row.append(int(s))
                    except ValueError:
                        row.append(float(s))
                tok, quoted = [], False
                if ch == ")":
                    started = False
                    yield tuple(row)
            elif started:
                tok.append(ch)
            elif ch == ";":
                return


def main(src, dst):
    cols = table_columns(src)
    missing = {t: [c for c in KEEP[t] if c not in cols.get(t, [])] for t in KEEP}
    for t, m in missing.items():
        if m or t not in cols:
            sys.exit(f"schema mismatch: {t} missing {m or 'whole table'}")
    db = sqlite3.connect(dst)
    for t in KEEP:
        db.execute(f"DROP TABLE IF EXISTS {t}")
        db.execute(f"CREATE TABLE {t} ({', '.join(KEEP[t])})")
    idx = {t: [cols[t].index(c) for c in KEEP[t]] for t in KEEP}
    counts = dict.fromkeys(KEEP, 0)

    def lines_after(f, first):
        yield first
        for line in f:
            yield line
            if line.rstrip().endswith(");"):
                return

    with open(src, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"INSERT INTO `(\w+)` VALUES(.*)", line)
            if not m or m.group(1) not in KEEP:
                continue
            t = m.group(1)
            ins = f"INSERT INTO {t} VALUES ({','.join('?' * len(KEEP[t]))})"
            for r in parse_values(lines_after(f, m.group(2))):
                if len(r) != len(cols[t]):
                    sys.exit(f"{t}: row has {len(r)} values, schema has {len(cols[t])}")
                db.execute(ins, [r[i] for i in idx[t]])
                counts[t] += 1
    db.execute("CREATE INDEX i_sg ON spawnentry(spawngroupID)")
    db.execute("CREATE INDEX i_s2 ON spawn2(spawngroupID)")
    db.commit()
    for t, n in counts.items():
        print(f"{t:12s} {n:8d} rows")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(*sys.argv[1:])
