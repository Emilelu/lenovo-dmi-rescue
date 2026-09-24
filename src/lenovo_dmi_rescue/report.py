"""Plain text renderers for the CLI."""

from __future__ import annotations

from . import fields
from .blocks import LdbgBlock
from .locate import Layout
from .scriptgen import RestorePlan

STATUS_MARK = {
    "same": "ok",
    "missing": "WRITE",
    "different": "FIX",
    "extra": "extra",
}


def render_layout(layout: Layout) -> str:
    low, high = sorted(layout.store.blocks, key=lambda b: b.offset)
    lines: list[str] = []
    if layout.ldbg is not None:
        lines.append(
            f"LDBG   @ 0x{layout.ldbg.offset:06X}   "
            f"write_offset=0x{layout.ldbg.write_offset:X}   xor=0x{layout.ldbg.xor_key:02X}"
        )
    else:
        lines.append("LDBG   @ (not found)")
    for tag, block in (("LENV1", low), ("LENV2", high)):
        mark = "   <- active" if block.offset == layout.store.active.offset else ""
        lines.append(
            f"{tag}  @ 0x{block.offset:06X}   gen={block.generation:<5} "
            f"entries={block.entry_count:<3} xor=0x{block.xor_key:02X}   "
            f"checksum={'OK' if block.checksum_ok() else 'BAD'}{mark}"
        )
    lines.append(
        f"region 0x{layout.region_start:06X} - 0x{layout.region_end:06X} "
        f"({layout.region_size} bytes)"
    )
    return "\n".join(lines)


def render_entries(layout: Layout, *, title: str = "entries") -> str:
    block = layout.store.active
    rows = sorted(block.entries(), key=fields.sort_key)
    width = max((len(fields.label(e.type)) for e in rows), default=12)
    lines = [
        f"{title} (active block @ 0x{block.offset:06X}, generation {block.generation}):",
        "",
        f"  {'type':<7} {'LVAR':<11} {'field':<{width}}  value",
        f"  {'-' * 7} {'-' * 11} {'-' * width}  {'-' * 34}",
    ]
    for entry in rows:
        flag = fields.info(entry.type).lvar or "-"
        value = fields.render_value(entry)
        if len(value) > 48:
            value = value[:45] + "..."
        lines.append(
            f"  0x{entry.type:04X}  {flag:<11} {fields.label(entry.type):<{width}}  {value}"
        )
    return "\n".join(lines)


def render_journal(ldbg: LdbgBlock | None, limit: int = 40) -> str:
    if ldbg is None:
        return "LDBG journal: not found"
    records = [r for r in ldbg.records() if r.timestamp is not None]
    lines = [
        f"LDBG journal @ 0x{ldbg.offset:06X} - {len(records)} timestamped record(s)",
        "",
    ]
    if not records:
        lines.append("  (empty - typical right after a full re-flash)")
        return "\n".join(lines)
    lines.append(f"  {'timestamp':<19}  {'op':<4} {'type':<7} field")
    lines.append(f"  {'-' * 19}  {'-' * 4} {'-' * 7} {'-' * 28}")
    for record in records[:limit]:
        assert record.timestamp is not None
        lines.append(
            f"  {record.timestamp.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"0x{record.op:02X}  0x{record.type:04X}  {fields.label(record.type)}"
        )
    if len(records) > limit:
        lines.append(f"  ... {len(records) - limit} more")
    return "\n".join(lines)


def render_diff(plan: RestorePlan, *, limit: int | None = None) -> str:
    diffs = plan.diffs if limit is None else plan.diffs[:limit]
    lines = [
        "field comparison",
        "=================",
        "",
        f"  donor  : generation {plan.donor_layout.store.active.generation}, "
        f"{len(plan.donor_layout.store.active.entries())} entries",
        f"  target : generation {plan.target_layout.store.active.generation}, "
        f"{len(plan.target_layout.store.active.entries())} entries",
        "",
        f"  {'type':<7} {'LVAR':<11} {'field':<22} {'status':<9} donor -> target",
        f"  {'-' * 7} {'-' * 11} {'-' * 22} {'-' * 9} {'-' * 30}",
    ]
    for diff in diffs:
        flag = diff.lvar or "-"
        status = STATUS_MARK.get(diff.status, diff.status)
        if status in ("WRITE", "FIX") and diff.lvar is None:
            status = "NO WAY"
        donor = fields.render_value(diff.donor) if diff.donor else "(absent)"
        target = fields.render_value(diff.target) if diff.target else "(absent)"
        if len(donor) > 26:
            donor = donor[:23] + "..."
        if len(target) > 26:
            target = target[:23] + "..."
        lines.append(
            f"  0x{diff.type:04X}  {flag:<11} {diff.label:<22} {status:<9} "
            f"{donor} -> {target}"
        )
    lines += [
        "",
        f"  restorable : {len(plan.to_write)}",
        f"  already ok : {len(plan.already_ok)}",
        f"  impossible : {len(plan.unrecoverable)}",
        f"  extras     : {len(plan.extras)}",
    ]
    return "\n".join(lines)
