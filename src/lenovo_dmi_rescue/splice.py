"""Method A -- transplant the DMI region and flash with a programmer.

This is the offline, guaranteed approach: take the LENV blocks out of a trusted
backup and drop them into the freshly re-flashed image, then write the result
back with a CH341A (or any SPI programmer).

Only the DMI region is copied.  Everything else -- most importantly the NVRAM
area -- keeps the *target* image's contents, because the donor's NVRAM usually
holds stale boot variables, Wi-Fi pairings, and crash dumps from the bricked
state.  :func:`splice` verifies that byte for byte and refuses to hand back an
image whose changes leaked outside the intended region.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .blocks import LDBG_BLOCK_SIZE, LENV_BLOCK_SIZE
from .locate import Layout, locate


class SpliceError(RuntimeError):
    pass


@dataclass
class ByteChange:
    offset: int
    before: int
    after: int


@dataclass
class SpliceResult:
    out_path: Path
    donor_layout: Layout
    target_layout: Layout
    region_start: int
    region_end: int
    blocks_copied: list[tuple[int, int]] = field(default_factory=list)
    changes: list[ByteChange] = field(default_factory=list)
    outside_changes: int = 0
    donor_entries: int = 0
    target_entries_before: int = 0

    @property
    def changed_bytes(self) -> int:
        return len(self.changes)

    @property
    def ok(self) -> bool:
        return self.outside_changes == 0

    def render(self, max_rows: int = 24) -> str:
        lines = [
            "splice result",
            "=============",
            f"  output image      : {self.out_path}",
            f"  DMI region        : 0x{self.region_start:06X} - 0x{self.region_end:06X}"
            f"  ({self.region_end - self.region_start} bytes)",
            f"  blocks copied     : "
            + ", ".join(
                f"0x{src:06X}->0x{dst:06X}" for src, dst in self.blocks_copied
            ),
            f"  donor entries     : {self.donor_entries}",
            f"  target entries    : {self.target_entries_before}",
            f"  bytes changed     : {self.changed_bytes}",
            f"  changes outside region: {self.outside_changes}"
            + ("  <-- DO NOT FLASH" if self.outside_changes else "  (clean)"),
        ]
        if self.changes:
            lines.append("")
            lines.append(f"  first {min(max_rows, len(self.changes))} changed bytes:")
            for change in self.changes[:max_rows]:
                lines.append(
                    f"    0x{change.offset:06X}  {change.before:02X} -> {change.after:02X}"
                )
            if len(self.changes) > max_rows:
                lines.append(f"    ... {len(self.changes) - max_rows} more")
        return "\n".join(lines)


def splice(
    donor_path: str | Path,
    target_path: str | Path,
    out_path: str | Path,
    *,
    include_ldbg: bool = False,
    block_size: int = LENV_BLOCK_SIZE,
) -> SpliceResult:
    """Copy the LENV store from ``donor`` into ``target`` and write ``out``.

    ``include_ldbg`` also copies the change journal.  Leave it off unless you
    specifically want the donor's write history preserved: the journal is
    cosmetic and copying it rewrites 8 KB more than necessary.
    """
    donor_path = Path(donor_path)
    target_path = Path(target_path)
    out_path = Path(out_path)

    donor_data = donor_path.read_bytes()
    target_data = target_path.read_bytes()

    donor = locate(donor_data, block_size)
    target = locate(target_data, block_size)

    if len(donor_data) != len(target_data):
        raise SpliceError(
            f"image sizes differ (donor {len(donor_data)} vs target {len(target_data)}); "
            "this tool only transplants between same-size SPI images"
        )

    out = bytearray(target_data)

    # Pair the blocks by ascending offset: the low block of the donor replaces
    # the low block of the target.  The absolute offsets may differ between BIOS
    # versions, which is exactly why we look them up each time.
    donor_blocks = sorted(donor.store.blocks, key=lambda b: b.offset)
    target_blocks = sorted(target.store.blocks, key=lambda b: b.offset)

    region_parts: list[tuple[int, int]] = []
    copied: list[tuple[int, int]] = []

    for donor_block, target_block in zip(donor_blocks, target_blocks):
        size = min(donor_block.size, target_block.size)
        out[target_block.offset : target_block.offset + size] = donor_block.raw[:size]
        region_parts.append((target_block.offset, target_block.offset + size))
        copied.append((donor_block.offset, target_block.offset))

    if include_ldbg and donor.ldbg is not None and target.ldbg is not None:
        size = min(donor.ldbg.size, target.ldbg.size)
        out[target.ldbg.offset : target.ldbg.offset + size] = donor.ldbg.raw[:size]
        region_parts.append((target.ldbg.offset, target.ldbg.offset + size))
        copied.append((donor.ldbg.offset, target.ldbg.offset))

    region_start = min(s for s, _ in region_parts)
    region_end = max(e for _, e in region_parts)

    changes: list[ByteChange] = []
    outside = 0
    for index in range(len(target_data)):
        if target_data[index] == out[index]:
            continue
        if region_start <= index < region_end:
            changes.append(ByteChange(index, target_data[index], out[index]))
        else:
            outside += 1

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))

    return SpliceResult(
        out_path=out_path,
        donor_layout=donor,
        target_layout=target,
        region_start=region_start,
        region_end=region_end,
        blocks_copied=copied,
        changes=changes,
        outside_changes=outside,
        donor_entries=len(donor.store.active.entries()),
        target_entries_before=len(target.store.active.entries()),
    )


def region_of(path: str | Path, block_size: int = LENV_BLOCK_SIZE) -> tuple[int, int]:
    """Convenience: ``(start, end)`` of the DMI region in an image."""
    layout = locate(Path(path).read_bytes(), block_size)
    start = layout.ldbg.offset if layout.ldbg else layout.store.start - LDBG_BLOCK_SIZE
    return start, layout.store.end
