"""Synthetic image factories for the test suite.

The tests never touch a real firmware dump.  A SPI image carries the machine's
serial number, UUID, Wi-Fi pairings and Windows product key -- none of that
belongs in a repository, so everything below is fabricated.

The structures are byte identical to what real hardware produces, which is what
makes the tests meaningful: :meth:`Synth.lenv` lays out entries exactly the way
the firmware does, including the XOR obfuscation and the additive checksum.
"""

from __future__ import annotations

import struct

from lenovo_dmi_rescue.blocks import (
    LDBG_BODY_OFFSET,
    LDBG_MAGIC,
    LENV_HEADER_SIZE,
    LENV_MAGIC,
    NAMESPACE_DMI,
    NAMESPACE_INTERNAL,
)

DUMMY_UUID = bytes.fromhex("00112233445566778899aabbccddeeff")
DUMMY_MSDM = b"\x01" + b"\x00" * 19 + b"AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"

#: A plausible but entirely invented set of DMI entries.
DEFAULT_ENTRIES: list[tuple[bytes, int, bytes]] = [
    (NAMESPACE_DMI, 0x0000, b"Test Book 15"),
    (NAMESPACE_DMI, 0x0100, b"TESTBOARD1"),
    (NAMESPACE_DMI, 0x0200, b"9999TEST1"),
    (NAMESPACE_DMI, 0x0300, b"1234567890123"),
    (NAMESPACE_DMI, 0x0400, b"TESTSN01"),
    (NAMESPACE_DMI, 0x0500, DUMMY_UUID),
    (NAMESPACE_DMI, 0x0B00, b"Test Family"),
    (NAMESPACE_DMI, 0x0F00, b"TESTOS01"),
    (NAMESPACE_DMI, 0x1000, b"WIN"),
    (NAMESPACE_DMI, 0x0001, DUMMY_MSDM),
    # The internal flag has no LVAR write path.  A factory image carries 0xFE;
    # a full re-flash leaves 0xFF.
    (NAMESPACE_DMI, 0x0011, b"\xfe"),
    (NAMESPACE_INTERNAL, 0xE10D, b"\x00" * 8),
]

#: Default LDBG stamp: ``26 20 09 23 20 56 13`` -> 2026-09-23 20:56:13.
DEFAULT_STAMP = (0x26, 0x20, 0x09, 0x23, 0x20, 0x56, 0x13)


class Synth:
    """Builders for LENV blocks, LDBG journals and whole images."""

    # ------------------------------------------------------------------ blocks

    @staticmethod
    def lenv(
        entries: list[tuple[bytes, int, bytes]] | None = None,
        *,
        key: int = 0x26,
        generation: int = 1,
        size: int = 0x1000,
        access: int = 0,
        count: int | None = None,
        corrupt_checksum: bool = False,
    ) -> bytes:
        entries = DEFAULT_ENTRIES if entries is None else entries
        body = bytearray()
        for namespace, etype, data in entries:
            body += bytes(namespace)[:0x0E].ljust(0x0E, b"\x00")
            body += struct.pack("<H", etype)
            body += struct.pack("<I", len(data))
            body += b"\x00\x00\x00\x00"
            body += data
        body += b"\x00" * (size - LENV_HEADER_SIZE - len(body))

        encrypted = bytes(b ^ key for b in body)
        checksum = sum(encrypted) & 0xFFFF
        if corrupt_checksum:
            checksum ^= 0xFFFF
        header = struct.pack(
            "<4sIIBBH",
            LENV_MAGIC,
            generation,
            len(entries) if count is None else count,
            access,
            key,
            checksum,
        )
        return header + encrypted

    @staticmethod
    def ldbg(
        *,
        key: int = 0x26,
        size: int = 0x2000,
        stamp: tuple[int, ...] = DEFAULT_STAMP,
        namespace: bytes = NAMESPACE_DMI,
        etype: int = 0x0400,
        data_size: int = 8,
    ) -> bytes:
        body = bytearray(size - LDBG_BODY_OFFSET)
        record = (
            bytes(stamp)
            + bytes([0x02])
            + bytes(namespace)[:0x0E].ljust(0x0E, b"\x00")
            + struct.pack("<H", etype)
            + struct.pack("<I", data_size)
            + b"\x00" * 4
        )
        body[: len(record)] = record
        encrypted = bytes(b ^ key for b in body)
        header = LDBG_MAGIC + struct.pack("<I", LDBG_BODY_OFFSET + len(record))
        header += b"\x00" * (LDBG_BODY_OFFSET - len(header))
        return header + encrypted

    # ------------------------------------------------------------------ images

    @staticmethod
    def image(
        *,
        entries: list[tuple[bytes, int, bytes]] | None = None,
        key: int = 0x26,
        generation: int = 5,
        total: int = 0x10000,
        base: int = 0x8000,
        with_ldbg: bool = True,
        filler: int = 0xFF,
    ) -> tuple[bytes, int]:
        """Return ``(image_bytes, lenv_offset)`` for a synthetic SPI dump."""
        buf = bytearray(bytes([filler]) * total)

        if with_ldbg:
            journal = Synth.ldbg(key=key)
            buf[base - 0x2000 : base - 0x2000 + len(journal)] = journal

        active = Synth.lenv(entries, key=key, generation=generation)
        stale = Synth.lenv(entries, key=key, generation=max(0, generation - 1))
        buf[base : base + len(active)] = active
        buf[base + 0x1000 : base + 0x2000] = stale

        return bytes(buf), base

    @staticmethod
    def stripped_image(
        *,
        key: int = 0x26,
        total: int = 0x10000,
        base: int = 0x8000,
    ) -> tuple[bytes, int]:
        """An image whose store holds almost nothing -- the post-bad-flash state.

        Mirrors what a re-flashed board looks like: the LENV area exists and is
        structurally valid, but the identity fields are gone.
        """
        skeleton = [
            (NAMESPACE_INTERNAL, 0xE10D, b"\x00" * 8),
            (NAMESPACE_DMI, 0x0011, b"\xff"),
        ]
        return Synth.image(entries=skeleton, key=key, generation=2, total=total, base=base)


__all__ = ["Synth", "DEFAULT_ENTRIES", "DEFAULT_STAMP", "DUMMY_UUID", "DUMMY_MSDM"]
