"""<zone>_chr.txt: first line = entry count, then `tag,archive` lines (CRLF).
WLD models: `orc,orc_chr` (orc_chr.s3d). EQG models: `dke,dke` (dke.eqg)."""
from __future__ import annotations


class ZoneList:
    def __init__(self, raw: bytes):
        self.nl = b"\r\n" if b"\r\n" in raw else b"\n"
        lines = raw.split(self.nl)
        if lines and lines[-1] == b"":
            lines.pop()
        if not lines or not lines[0].strip().isdigit():
            raise ValueError("zone list does not start with an entry count")
        self.entries: list[tuple[str, str]] = []
        for ln in lines[1:]:
            tag, _, arc = ln.decode("ascii").partition(",")
            self.entries.append((tag.strip(), arc.strip()))

    def get(self, tag: str) -> str | None:
        t = tag.lower()
        for tg, arc in self.entries:
            if tg.lower() == t:
                return arc
        return None

    def ensure(self, tag: str, archive: str) -> bool:
        """Make the list load `tag` from `archive`. Returns True if anything changed."""
        t = tag.lower()
        for i, (tg, arc) in enumerate(self.entries):
            if tg.lower() == t:
                if arc == archive:
                    return False
                self.entries[i] = (tg, archive)
                return True
        self.entries.append((t, archive))
        return True

    def tobytes(self) -> bytes:
        out = [str(len(self.entries)).encode()]
        out += [f"{t},{a}".encode() for t, a in self.entries]
        return self.nl.join(out) + self.nl
