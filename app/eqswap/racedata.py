"""racedata.txt: ^-separated rows, CRLF. field0=race, field1=gender, field 50 = model tag,
fields 46/47 = native model height (the client's size divisor). Only the fields we edit
are touched; every other byte, including non-row lines, round-trips exactly."""
from __future__ import annotations

TAG_FIELD = 50
ANIM_DONOR_FIELD = 53   # race whose animations this row borrows (GUL -> GUS); 0 = uses its own
SIZE_FIELDS = (46, 47)
MIN_FIELDS = 58  # rows with fewer fields are not race rows


class RaceData:
    def __init__(self, raw: bytes):
        self._lines = raw.split(b"\n")
        self._index: dict[tuple[int, int], int] = {}
        for i, ln in enumerate(self._lines):
            f = ln.split(b"^")
            if len(f) >= MIN_FIELDS and f[0].isdigit() and f[1].isdigit():
                self._index.setdefault((int(f[0]), int(f[1])), i)

    # --- reading
    def keys(self) -> list[tuple[int, int]]:
        return sorted(self._index)

    def has(self, race: int, gender: int) -> bool:
        return (race, gender) in self._index

    def genders(self, race: int) -> list[int]:
        return sorted(g for r, g in self._index if r == race)

    def _fields(self, race: int, gender: int) -> list[bytes]:
        try:
            return self._lines[self._index[(race, gender)]].split(b"^")
        except KeyError:
            raise KeyError(f"no racedata row for race {race} gender {gender}") from None

    def get_tag(self, race: int, gender: int) -> str:
        return self._fields(race, gender)[TAG_FIELD].decode("ascii")

    def get_size(self, race: int, gender: int) -> tuple[str, str]:
        f = self._fields(race, gender)
        return f[SIZE_FIELDS[0]].decode("ascii"), f[SIZE_FIELDS[1]].decode("ascii")

    def tag_for(self, race: int, gender: int) -> str | None:
        """Model tag the client would use for an NPC of this race/gender (falls back like the
        client table does: exact row, then the neutral row, then any row of the race)."""
        for g in (gender, 2):
            if self.has(race, g):
                return self.get_tag(race, g)
        gs = self.genders(race)
        return self.get_tag(race, gs[0]) if gs else None

    def anim_donor(self, race: int, gender: int) -> int:
        """Race id whose animations this row borrows, or 0 when the model animates itself."""
        v = self._fields(race, gender)[ANIM_DONOR_FIELD]
        return int(v) if v.isdigit() else 0

    def rows_with_tag(self, tag: str) -> list[tuple[int, int]]:
        t = tag.upper()
        return [k for k in self.keys() if self.get_tag(*k).upper() == t]

    # --- editing
    def _set(self, race: int, gender: int, edits: dict[int, str]) -> None:
        i = self._index[(race, gender)]
        f = self._lines[i].split(b"^")
        for idx, val in edits.items():
            if idx >= len(f) - 1:
                raise ValueError("refusing to edit the last field (carries the CR)")
            f[idx] = val.encode("ascii")
        self._lines[i] = b"^".join(f)

    def set_tag(self, race: int, gender: int, tag: str) -> None:
        self._fields(race, gender)  # existence check
        if not tag or not tag.isalnum() or len(tag) > 8:
            raise ValueError(f"bad model tag {tag!r}")
        self._set(race, gender, {TAG_FIELD: tag.upper()})

    def set_size(self, race: int, gender: int, height: str) -> None:
        self._fields(race, gender)
        float(height)  # must parse
        self._set(race, gender, {SIZE_FIELDS[0]: height, SIZE_FIELDS[1]: height})

    def tobytes(self) -> bytes:
        return b"\n".join(self._lines)
