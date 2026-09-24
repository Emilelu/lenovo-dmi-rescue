"""LENV type codes, the LVAR flags that write them, and value classification.

The LENV type is a 16 bit tag; the LVAR utility addresses the same variables by
short name (``/pn``, ``/mt``, ...).  The mapping below was derived empirically
by writing a sentinel value with LVAR, re-dumping the SPI image, and observing
which type changed -- guessing from the order of strings inside the tool
binary gets it wrong (``/at`` and ``/osp`` were swapped on the first attempt).

Types marked ``internal`` have no LVAR flag at all.  They are maintained by
BIOS flashing itself and are **not writable** by any utility.
"""

from __future__ import annotations

from dataclasses import dataclass

from .blocks import LenvEntry, NAMESPACE_INTERNAL


@dataclass(frozen=True)
class FieldInfo:
    type: int
    lvar: str | None
    label: str
    note: str = ""


#: LENV type -> meaning.  Keep sorted by type for readability.
KNOWN_TYPES: dict[int, FieldInfo] = {
    0x0000: FieldInfo(0x0000, "/pn", "Product Name"),
    0x0001: FieldInfo(0x0001, "/msdm", "OA3 / MSDM", "20 byte header + 25 char key"),
    0x0005: FieldInfo(0x0005, "/cf", "Keyboard ID"),
    0x000B: FieldInfo(0x000B, "/oa3keyid", "OA3 Key ID", "13 digits"),
    0x000D: FieldInfo(0x000D, "/mfgmode", "MFG mode flag", "'Y' while in manufacturing mode"),
    0x0011: FieldInfo(0x0011, None, "internal flag", "no write path; 0xFE factory / 0xFF after flash"),
    0x0100: FieldInfo(0x0100, "/pjn", "Project Name", "also written as /pn2; Baseboard Product"),
    0x0200: FieldInfo(0x0200, "/mt", "MTM", "machine type model"),
    0x0300: FieldInfo(0x0300, "/sn", "Serial Number 1"),
    0x0400: FieldInfo(0x0400, "/ln", "Lenovo SN", "sticker serial"),
    0x0500: FieldInfo(0x0500, "/uu", "UUID", "16 bytes, SMBIOS byte order"),
    0x0700: FieldInfo(0x0700, "/kd", "KD flag", "observed value 'S'"),
    0x0B00: FieldInfo(0x0B00, "/fd", "Family Name"),
    0x0C00: FieldInfo(0x0C00, "/at", "Asset Tag", "absent on some consumer models"),
    0x0F00: FieldInfo(0x0F00, "/osp", "OS PN number"),
    0x1000: FieldInfo(0x1000, "/oss", "OS descriptor", "e.g. WIN"),
    0xE10D: FieldInfo(0xE10D, None, "internal", "8 zero bytes"),
}

#: Types that participate in Microsoft's activation hardware hash.
ACTIVATION_RELEVANT: frozenset[int] = frozenset({0x0500, 0x0100, 0x0F00, 0x1000})

#: Typical field order used when re-writing a stripped board.
WRITE_ORDER: tuple[int, ...] = (
    0x0100,
    0x0200,
    0x0000,
    0x0300,
    0x0400,
    0x0500,
    0x0B00,
    0x0F00,
    0x1000,
    0x000B,
    0x0001,
    0x0005,
    0x0700,
)


def info(etype: int) -> FieldInfo:
    return KNOWN_TYPES.get(
        etype,
        FieldInfo(etype, None, f"unknown type 0x{etype:04X}"),
    )


def label(etype: int) -> str:
    return info(etype).label


def lvar_flag(etype: int) -> str | None:
    return info(etype).lvar


def writable(entry: LenvEntry) -> bool:
    """False for the internal entries no tool can address."""
    if entry.namespace_id == NAMESPACE_INTERNAL and entry.type not in KNOWN_TYPES:
        return False
    return info(entry.type).lvar is not None


def classify(entry: LenvEntry) -> str:
    """Rough value shape, used by reports and by the script generator."""
    if entry.guid is not None:
        return "uuid"
    if entry.text is not None:
        text = entry.text
        if text.isdigit():
            return "digits"
        return "text"
    if len(entry.data) == 1:
        return "byte"
    return "binary"


def sort_key(entry: LenvEntry) -> tuple[int, int]:
    try:
        rank = WRITE_ORDER.index(entry.type)
    except ValueError:
        rank = len(WRITE_ORDER)
    return (rank, entry.type)


def render_value(entry: LenvEntry) -> str:
    """Field-aware rendering -- pulls the product key out of the MSDM blob.

    The OA3 payload is a fixed 20 byte header followed by the key.  The key is
    **29** characters (``XXXXX-XXXXX-XXXXX-XXXXX-XXXXX``), which makes the whole
    entry 49 bytes; the header's fifth dword holds that length.  Rendering it as
    generic hex would bury the only interesting part of the entry.
    """
    if entry.type == 0x0001 and len(entry.data) > 20:
        tail = entry.data[20:]
        if all(0x20 <= b < 0x7F for b in tail):
            return tail.decode("ascii")
    return entry.render()


def lvar_invocation(entry: LenvEntry, tool: str = "LvarEfi64V252.efi") -> str | None:
    """Build the LVAR command line that writes ``entry`` back.

    Printable text -- including digit-only serials -- is written with ``/c``
    rather than ``/b``: both produce identical bytes, but a readable command
    line can be eyeballed against the donor table before it runs, and a typo
    in a hex string is far easier to miss.

    Returns ``None`` when the entry has no LVAR flag, so callers can report the
    field as unrecoverable instead of emitting a command that would fail.
    """
    flag = info(entry.type).lvar
    if flag is None:
        return None
    text = entry.text
    if text is not None and '"' not in text:
        return f'{tool} /w {flag} /c "{text}"'
    payload = " ".join(f"{b:02X}" for b in entry.data)
    return f'{tool} /w {flag} /b "{payload}"'
