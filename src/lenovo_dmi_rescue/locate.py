"""Locate the LENV variable store inside a full SPI image.

A naive ``b"LENV"`` search returns far more hits than real blocks -- the four
bytes also appear inside BIOS code and inside PSP directory entries.  Every
candidate is therefore validated structurally (:meth:`LenvBlock.parse` checks
the checksum *and* walks the entry list to the block boundary before accepting
it), which is what makes automatic location safe.

On every machine examined so far the blocks are contiguous and ordered
``LDBG`` (0x2000) + ``LENV1`` (0x1000) + ``LENV2`` (0x1000).  The absolute
offset differs between BIOS versions and board revisions, so it is always
derived from the image rather than hard-coded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .blocks import (
    LDBG_BLOCK_SIZE,
    LDBG_MAGIC,
    LENV_BLOCK_SIZE,
    LENV_MAGIC,
    LdbgBlock,
    LenvBlock,
)


class LocateError(RuntimeError):
    """Raised when the image does not contain a usable LENV store."""


@dataclass
class LenvStore:
    """The two LENV copies; firmware reads whichever has the higher generation."""

    active: LenvBlock
    stale: LenvBlock

    @property
    def blocks(self) -> tuple[LenvBlock, LenvBlock]:
        return (self.active, self.stale)

    @property
    def start(self) -> int:
        return min(b.offset for b in self.blocks)

    @property
    def end(self) -> int:
        return max(b.offset + b.size for b in self.blocks)

    @property
    def span(self) -> int:
        return self.end - self.start


@dataclass
class Layout:
    """Everything we found in one image."""

    store: LenvStore
    ldbg: LdbgBlock | None = None

    @property
    def region_start(self) -> int:
        if self.ldbg is not None:
            return self.ldbg.offset
        return self.store.start - LDBG_BLOCK_SIZE

    @property
    def region_end(self) -> int:
        # The region spans the journal *and* both store copies, so take the
        # furthest end rather than assuming the journal comes last.
        if self.ldbg is not None:
            return max(self.ldbg.offset + self.ldbg.size, self.store.end)
        return self.store.end

    @property
    def region_size(self) -> int:
        return self.region_end - self.region_start

    def summary(self) -> str:
        lines = [
            f"LDBG  @ 0x{self.ldbg.offset:06X}  write_offset=0x{self.ldbg.write_offset:X}"
            if self.ldbg
            else "LDBG  @ (not found)",
            f"LENV1 @ 0x{self.store.active.offset:06X}  gen={self.store.active.generation} "
            f"entries={self.store.active.entry_count} xor=0x{self.store.active.xor_key:02X}",
            f"LENV2 @ 0x{self.store.stale.offset:06X}  gen={self.store.stale.generation} "
            f"entries={self.store.stale.entry_count} xor=0x{self.store.stale.xor_key:02X}",
            f"region 0x{self.region_start:06X} - 0x{self.region_end:06X} ({self.region_size} bytes)",
        ]
        return "\n".join(lines)


def find_lenv_candidates(data: bytes, size: int = LENV_BLOCK_SIZE) -> list[LenvBlock]:
    """Return every structurally valid LENV block in ``data``."""
    out: list[LenvBlock] = []
    seen: set[int] = set()
    for match in re.finditer(re.escape(LENV_MAGIC), data):
        offset = match.start()
        if offset in seen:
            continue
        block = LenvBlock.parse(data, offset, size)
        if block is not None:
            seen.add(offset)
            out.append(block)
    return out


def pair_stores(blocks: list[LenvBlock]) -> list[LenvStore]:
    """Group LENV blocks into adjacent copy pairs.

    A store is two blocks exactly ``LENV_BLOCK_SIZE`` apart.  Blocks that have
    no neighbour are ignored rather than guessed at.
    """
    by_offset = {b.offset: b for b in blocks}
    used: set[int] = set()
    stores: list[LenvStore] = []

    for block in sorted(blocks, key=lambda b: b.offset):
        if block.offset in used:
            continue
        mate = by_offset.get(block.offset + LENV_BLOCK_SIZE) or by_offset.get(
            block.offset - LENV_BLOCK_SIZE
        )
        if mate is None or mate.offset in used:
            continue
        used.add(block.offset)
        used.add(mate.offset)
        if block.generation >= mate.generation:
            stores.append(LenvStore(active=block, stale=mate))
        else:
            stores.append(LenvStore(active=mate, stale=block))

    return stores


def find_ldbg(data: bytes, store: LenvStore) -> LdbgBlock | None:
    """Find the journal that belongs to ``store`` (it sits just in front)."""
    key = store.active.xor_key
    for offset in (store.start - LDBG_BLOCK_SIZE, store.end):
        block = LdbgBlock.parse(data, offset, key)
        if block is None:
            continue
        if block.timestamps_plausible():
            return block
        # Accept anyway when it is the only candidate -- the journal may be
        # empty on a freshly re-flashed board.
        if all(r.timestamp is None for r in block.records()):
            return block
    # Journal missing: the image may have been sliced before LENV_BLOCK_SIZE.
    return None


def locate(data: bytes, size: int = LENV_BLOCK_SIZE) -> Layout:
    """Locate the active variable store in a raw SPI image.

    Raises :class:`LocateError` when nothing valid is found, which normally
    means the image is not a Lenovo InsydeH2O dump or is truncated.
    """
    blocks = find_lenv_candidates(data, size)
    if not blocks:
        raise LocateError(
            "no valid LENV block found -- is this a Lenovo InsydeH2O SPI dump?"
        )
    stores = pair_stores(blocks)
    if not stores:
        raise LocateError(
            f"found {len(blocks)} LENV block(s) but no adjacent pair; "
            "the image may be truncated or from an unsupported layout"
        )

    # Prefer the store the firmware itself would read: most entries first
    # (a populated store beats an empty one), then highest generation.
    best = max(
        stores,
        key=lambda s: (len(s.active.entries()), s.active.generation),
    )
    return Layout(store=best, ldbg=find_ldbg(data, best))


def is_lenv_magic_present(data: bytes) -> bool:
    return LENV_MAGIC in data or LDBG_MAGIC in data
