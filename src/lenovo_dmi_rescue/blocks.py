"""On-flash structures that hold the Lenovo machine identity data.

Everything here is a pure function of a raw SPI image -- nothing touches the
network, the registry, or the running machine.

Two structures matter:

``LENV``
    The variable store.  Lenovo firmware keeps three copies side by side::

        LDBG   0x2000   append-only change journal
        LENV1  0x1000   variable store, copy A
        LENV2  0x1000   variable store, copy B (higher generation wins)

    The *body* of each block is XOR-obfuscated with a single byte key stored
    in the LENV header itself, so the plaintext MTM/SN are never greppable.

``LDBG``
    Journal of write events.  Each record carries a BCD timestamp, which is
    how you prove *which tool wrote which field and when* -- the difference
    between "the factory wrote this" and "a repair utility wrote this".
"""

from __future__ import annotations

import datetime as _dt
import struct
import uuid as _uuid
from dataclasses import dataclass

LENV_MAGIC = b"LENV"
LDBG_MAGIC = b"LDBG"

#: ``magic(4) generation(4) entry_count(4) access_flag(1) xor_key(1) checksum(2)``
LENV_HEADER_SIZE = 0x10

#: ``namespace(14) type(2) data_size(4) flags(1) u1(1) u2(2)``
LENV_ENTRY_HEADER_SIZE = 0x18

#: ``magic(4) write_offset(4)`` -- everything up to 0x20 is plaintext
LDBG_HEADER_SIZE = 0x08
LDBG_BODY_OFFSET = 0x20
LDBG_RECORD_SIZE = 0x20

#: Fixed block sizes on every InsydeH2O Lenovo machine seen so far.
LDBG_BLOCK_SIZE = 0x2000
LENV_BLOCK_SIZE = 0x1000

#: Namespace ids observed in the wild.
NAMESPACE_DMI = bytes.fromhex("55570ec26911564ca48a9824ab43")
NAMESPACE_INTERNAL = bytes.fromhex("468f4464236e88429349fdd887c4")


def _bcd(value: int) -> int:
    """Decode one packed BCD byte; returns ``-1`` when the nibbles are invalid."""
    hi, lo = value >> 4, value & 0x0F
    if hi > 9 or lo > 9:
        return -1
    return hi * 10 + lo


@dataclass(frozen=True)
class LenvEntry:
    """One variable inside an :class:`LenvBlock`."""

    namespace_id: bytes
    type: int
    data: bytes
    flags: int = 0
    u1: int = 0
    u2: int = 0

    @property
    def type_hex(self) -> str:
        return f"0x{self.type:04X}"

    @property
    def is_dmi(self) -> bool:
        return self.namespace_id == NAMESPACE_DMI

    @property
    def text(self) -> str | None:
        """Decoded text when the payload looks like printable ASCII."""
        if not self.data:
            return None
        if all(0x20 <= b < 0x7F for b in self.data):
            return self.data.decode("ascii")
        # Some fields are UTF-16LE with trailing NULs.
        if len(self.data) % 2 == 0:
            try:
                candidate = self.data.rstrip(b"\x00").decode("utf-16-le")
            except UnicodeDecodeError:
                return None
            if candidate and all(0x20 <= ord(c) < 0x7F for c in candidate):
                return candidate
        return None

    @property
    def guid(self) -> str | None:
        """Interpret a 16 byte payload as a GUID (SMBIOS byte order)."""
        if len(self.data) != 16:
            return None
        return str(_uuid.UUID(bytes_le=self.data)).upper()

    @property
    def hex(self) -> str:
        return " ".join(f"{b:02X}" for b in self.data)

    def render(self) -> str:
        """Best-effort one-line human readable value."""
        if self.guid:
            return self.guid
        if self.text:
            return self.text
        return self.hex


@dataclass
class LenvBlock:
    """A single ``LENV`` block as stored in flash."""

    offset: int
    size: int
    generation: int
    entry_count: int
    access_flag: int
    xor_key: int
    checksum: int
    raw: bytes

    # ---------------------------------------------------------------- parsing

    @classmethod
    def parse(cls, data: bytes, offset: int, size: int = LENV_BLOCK_SIZE) -> "LenvBlock | None":
        """Parse a candidate block, returning ``None`` when it is not valid."""
        if offset < 0 or offset + size > len(data):
            return None
        if data[offset : offset + 4] != LENV_MAGIC:
            return None
        magic, generation, entry_count, access, key, checksum = struct.unpack_from(
            "<4sIIBBH", data, offset
        )
        del magic  # already verified
        block = cls(
            offset=offset,
            size=size,
            generation=generation,
            entry_count=entry_count,
            access_flag=access,
            xor_key=key,
            checksum=checksum,
            raw=bytes(data[offset : offset + size]),
        )
        if not block.checksum_ok():
            return None
        if not (0 <= entry_count <= 0x40):
            return None
        # An entry walk that does not land cleanly means we found a false hit.
        if not block.walk_ok():
            return None
        return block

    # ------------------------------------------------------------------- body

    @property
    def encrypted_body(self) -> bytes:
        return self.raw[LENV_HEADER_SIZE:]

    @property
    def body(self) -> bytes:
        """Decrypted payload (everything after the 16 byte header)."""
        key = self.xor_key
        return bytes(b ^ key for b in self.encrypted_body)

    def computed_checksum(self) -> int:
        return sum(self.encrypted_body) & 0xFFFF

    def checksum_ok(self) -> bool:
        return self.computed_checksum() == self.checksum

    # ---------------------------------------------------------------- entries

    def entries(self) -> list[LenvEntry]:
        body = self.body
        out: list[LenvEntry] = []
        p = 0
        for _ in range(self.entry_count):
            if p + LENV_ENTRY_HEADER_SIZE > len(body):
                break
            namespace_id = body[p : p + 0x0E]
            (etype,) = struct.unpack_from("<H", body, p + 0x0E)
            (dsize,) = struct.unpack_from("<I", body, p + 0x10)
            flags = body[p + 0x14]
            u1 = body[p + 0x15]
            (u2,) = struct.unpack_from("<H", body, p + 0x16)
            data = body[p + LENV_ENTRY_HEADER_SIZE : p + LENV_ENTRY_HEADER_SIZE + dsize]
            out.append(
                LenvEntry(
                    namespace_id=namespace_id,
                    type=etype,
                    data=bytes(data),
                    flags=flags,
                    u1=u1,
                    u2=u2,
                )
            )
            p += LENV_ENTRY_HEADER_SIZE + dsize
        return out

    def walk_ok(self) -> bool:
        """True when the declared entry count walks the body exactly."""
        body = self.body
        p = 0
        for _ in range(self.entry_count):
            if p + LENV_ENTRY_HEADER_SIZE > len(body):
                return False
            (dsize,) = struct.unpack_from("<I", body, p + 0x10)
            if dsize > 0x800:
                return False
            p += LENV_ENTRY_HEADER_SIZE + dsize
            if p > len(body):
                return False
        # The remaining bytes must be padding (0x00 or 0xFF after XOR).
        tail = body[p:]
        if not tail:
            return True
        pad = {0x00, 0xFF, self.xor_key}
        return set(tail) <= pad or all(b in pad for b in tail[:16])

    def find(self, etype: int) -> LenvEntry | None:
        for entry in self.entries():
            if entry.type == etype:
                return entry
        return None

    def used_bytes(self) -> int:
        """Offset just past the last entry, relative to the body start."""
        p = 0
        body = self.body
        for _ in range(self.entry_count):
            if p + LENV_ENTRY_HEADER_SIZE > len(body):
                break
            (dsize,) = struct.unpack_from("<I", body, p + 0x10)
            p += LENV_ENTRY_HEADER_SIZE + dsize
        return p


@dataclass(frozen=True)
class LdbgRecord:
    offset: int
    timestamp: _dt.datetime | None
    op: int
    namespace_id: bytes
    type: int
    data_size: int

    @property
    def type_hex(self) -> str:
        return f"0x{self.type:04X}"


@dataclass
class LdbgBlock:
    """The change journal that sits in front of ``LENV1``."""

    offset: int
    size: int
    write_offset: int
    xor_key: int
    raw: bytes

    @classmethod
    def parse(
        cls,
        data: bytes,
        offset: int,
        xor_key: int,
        size: int = LDBG_BLOCK_SIZE,
    ) -> "LdbgBlock | None":
        if offset < 0 or offset + LDBG_HEADER_SIZE > len(data):
            return None
        if data[offset : offset + 4] != LDBG_MAGIC:
            return None
        (write_offset,) = struct.unpack_from("<I", data, offset + 4)
        if not (LDBG_BODY_OFFSET <= write_offset <= size):
            return None
        return cls(
            offset=offset,
            size=size,
            write_offset=write_offset,
            xor_key=xor_key,
            raw=bytes(data[offset : offset + size]),
        )

    @property
    def body(self) -> bytes:
        """Decrypted records region (header/padding below 0x20 stays plain)."""
        key = self.xor_key
        return bytes(b ^ key for b in self.raw[LDBG_BODY_OFFSET:])

    def records(self) -> list[LdbgRecord]:
        body = self.body
        out: list[LdbgRecord] = []
        limit = max(self.write_offset - LDBG_BODY_OFFSET, 0)
        p = 0
        while p + LDBG_RECORD_SIZE <= limit:
            rec = body[p : p + LDBG_RECORD_SIZE]
            ts = self._decode_timestamp(rec[:7])
            op = rec[7]
            namespace_id = rec[8:0x16]
            (etype,) = struct.unpack_from("<H", rec, 0x16)
            (dsize,) = struct.unpack_from("<I", rec, 0x18)
            out.append(
                LdbgRecord(
                    offset=self.offset + LDBG_BODY_OFFSET + p,
                    timestamp=ts,
                    op=op,
                    namespace_id=namespace_id,
                    type=etype,
                    data_size=dsize,
                )
            )
            p += LDBG_RECORD_SIZE
        return out

    def timestamps_plausible(self) -> bool:
        """Sanity check used to confirm the XOR key / body offset are right."""
        recs = self.records()
        if not recs:
            return False
        good = 0
        for rec in recs[:8]:
            ts = rec.timestamp
            if ts and 2015 <= ts.year <= 2045:
                good += 1
        return good >= max(1, min(3, len(recs) // 2))

    @staticmethod
    def _decode_timestamp(raw: bytes) -> _dt.datetime | None:
        """Decode the 7 byte EFI_TIME prefix of an LDBG record.

        The layout is ``Year(u16) Month(u8) Day(u8) Hour(u8) Minute(u8)
        Second(u8)``, and **every field is packed BCD** -- including the
        two byte year, little endian, so the bytes ``26 20`` mean 2026.

        Reading the year as a single byte and adding 2000 yields 2038, which
        is wrong by twelve years yet still passes any "is this a real date"
        sanity check.  That is exactly the kind of plausible looking error
        worth spelling out.
        """
        if len(raw) < 7:
            return None
        year_lo, year_hi = _bcd(raw[0]), _bcd(raw[1])
        month, day = _bcd(raw[2]), _bcd(raw[3])
        hour, minute, second = _bcd(raw[4]), _bcd(raw[5]), _bcd(raw[6])
        if -1 in (year_lo, year_hi, month, day, hour, minute, second):
            return None
        year = year_hi * 100 + year_lo
        if not (2000 <= year <= 2099):
            return None
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
        if hour > 23 or minute > 59 or second > 59:
            return None
        try:
            return _dt.datetime(year, month, day, hour, minute, second)
        except ValueError:
            return None


def xor_bytes(data: bytes, key: int) -> bytes:
    """Symmetric helper -- same operation encrypts and decrypts."""
    return bytes(b ^ key for b in data)


def build_lenv_block(
    template: LenvBlock,
    entries: list[LenvEntry],
    *,
    generation: int | None = None,
) -> bytes:
    """Rebuild a block from ``entries``, reusing the template's key and XOR scheme.

    The result is a drop-in replacement for ``template.raw`` and already carries
    a correct checksum, so it can be written straight to flash.
    """
    body = bytearray()
    for entry in entries:
        body += entry.namespace_id[:0x0E].ljust(0x0E, b"\x00")
        body += struct.pack("<H", entry.type)
        body += struct.pack("<I", len(entry.data))
        body += bytes([entry.flags & 0xFF, entry.u1 & 0xFF])
        body += struct.pack("<H", entry.u2 & 0xFFFF)
        body += entry.data

    if len(body) + LENV_HEADER_SIZE > template.size:
        raise ValueError(
            f"entries need {len(body) + LENV_HEADER_SIZE} bytes, block holds {template.size}"
        )
    body += b"\x00" * (template.size - LENV_HEADER_SIZE - len(body))

    key = template.xor_key
    encrypted = bytes(b ^ key for b in body)
    checksum = sum(encrypted) & 0xFFFF
    gen = template.generation + 1 if generation is None else generation
    header = struct.pack(
        "<4sIIBBH",
        LENV_MAGIC,
        gen,
        len(entries),
        template.access_flag,
        key,
        checksum,
    )
    return header + encrypted
