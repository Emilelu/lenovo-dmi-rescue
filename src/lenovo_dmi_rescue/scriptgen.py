"""Method B -- restore DMI in place, without opening the case.

The firmware exposes the same variables that a programmer would write, through
a utility that runs in the UEFI Shell.  That means a stripped board can be
repaired from a USB stick: no disassembly, no SPI clip, no soldering.

What this module produces:

``read_first.nsh``
    Read-only.  Dumps the current values so the operator has a "before"
    snapshot, and so the LVAR tool can be verified to work at all.

``restore_dmi.nsh``
    Writes every field the donor has and the target is missing or has wrong.

``msdm.bin``
    Only when the OA3 / MSDM blob has to be restored: a 49 byte binary payload
    that cannot be passed as a command line string.

``README.txt``
    Plain instructions for the USB stick itself.

Fields whose LENV type has no LVAR flag (the internal ones) are listed as
unrecoverable rather than silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import fields
from .blocks import LenvEntry
from .locate import Layout, locate
from .uefi import NshBuilder, write_ascii

DEFAULT_TOOL = "LvarEfi64V252.efi"

#: Types whose payload is binary and best written from a file.
FILE_BACKED_TYPES = {0x0001}


@dataclass
class FieldDiff:
    type: int
    donor: LenvEntry | None
    target: LenvEntry | None

    @property
    def label(self) -> str:
        return fields.label(self.type)

    @property
    def lvar(self) -> str | None:
        return fields.info(self.type).lvar

    @property
    def status(self) -> str:
        if self.donor is None:
            return "extra"
        if self.target is None:
            return "missing"
        if self.donor.data == self.target.data:
            return "same"
        return "different"

    @property
    def restorable(self) -> bool:
        return self.status in ("missing", "different") and self.lvar is not None

    @property
    def value(self) -> str:
        source = self.donor if self.donor is not None else self.target
        return fields.render_value(source) if source is not None else ""


@dataclass
class RestorePlan:
    donor_layout: Layout
    target_layout: Layout
    diffs: list[FieldDiff] = field(default_factory=list)

    @property
    def to_write(self) -> list[FieldDiff]:
        return [d for d in self.diffs if d.restorable]

    @property
    def already_ok(self) -> list[FieldDiff]:
        return [d for d in self.diffs if d.status == "same"]

    @property
    def unrecoverable(self) -> list[FieldDiff]:
        return [
            d
            for d in self.diffs
            if d.status in ("missing", "different") and d.lvar is None
        ]

    @property
    def extras(self) -> list[FieldDiff]:
        return [d for d in self.diffs if d.status == "extra"]


@dataclass
class GeneratedFiles:
    out_dir: Path
    scripts: list[Path] = field(default_factory=list)
    payloads: list[Path] = field(default_factory=list)

    @property
    def all(self) -> list[Path]:
        return [*self.scripts, *self.payloads]


def plan_restore(
    donor_path: str | Path,
    target_path: str | Path,
    *,
    block_size: int | None = None,
) -> RestorePlan:
    """Compare the DMI of two images field by field."""
    from .blocks import LENV_BLOCK_SIZE

    size = block_size or LENV_BLOCK_SIZE
    donor = locate(Path(donor_path).read_bytes(), size)
    target = locate(Path(target_path).read_bytes(), size)

    donor_entries = {e.type: e for e in donor.store.active.entries() if e.is_dmi}
    target_entries = {e.type: e for e in target.store.active.entries() if e.is_dmi}

    diffs: list[FieldDiff] = []
    for etype in sorted(set(donor_entries) | set(target_entries)):
        diffs.append(
            FieldDiff(
                type=etype,
                donor=donor_entries.get(etype),
                target=target_entries.get(etype),
            )
        )
    diffs.sort(key=lambda d: fields.sort_key(
        d.donor if d.donor is not None else d.target  # type: ignore[arg-type]
    ))
    return RestorePlan(donor_layout=donor, target_layout=target, diffs=diffs)


def _payload_bytes(entry: LenvEntry) -> bytes:
    return entry.data


def generate(
    plan: RestorePlan,
    out_dir: str | Path,
    *,
    tool: str = DEFAULT_TOOL,
    include_read_script: bool = True,
    usb_root: str | None = None,
) -> GeneratedFiles:
    """Write the UEFI scripts and payloads for ``plan`` into ``out_dir``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated = GeneratedFiles(out_dir=out_dir)
    location = usb_root or str(out_dir).replace("\\", "/")

    # ---------------------------------------------------------------- payloads
    msdm_entry = next((d.donor for d in plan.diffs if d.type == 0x0001 and d.donor), None)
    if msdm_entry is not None and any(
        d.type == 0x0001 and d.restorable for d in plan.diffs
    ):
        blob = out_dir / "msdm.bin"
        blob.write_bytes(_payload_bytes(msdm_entry))
        generated.payloads.append(blob)

    # ------------------------------------------------------------ read script
    if include_read_script:
        reader = NshBuilder(title="READ FIRST - current DMI state (read only)")
        reader.blank()
        for entry in plan.target_layout.store.active.entries():
            flag = fields.info(entry.type).lvar
            reader.annotate(entry.type, flag, f"expect {fields.label(entry.type)}")
            if flag:
                reader.raw(f"{tool} /r {flag}")
            else:
                reader.annotate(entry.type, None, "no LVAR flag - cannot be read")
        reader.blank()
        reader.hint("Nothing above was modified. Safe to reboot.")
        generated.scripts.append(reader.write(out_dir / "read_first.nsh"))

    # --------------------------------------------------------- restore script
    writer = NshBuilder(
        title="RESTORE DMI from a trusted backup image (run from UEFI Shell)"
    )
    writer.blank()
    if not plan.to_write:
        writer.hint("Nothing to do - every restorable field already matches the donor.")
    else:
        writer.hint(f"{len(plan.to_write)} field(s) will be written.")
    writer.blank()

    for diff in plan.to_write:
        entry = diff.donor
        assert entry is not None
        writer.annotate(diff.type, diff.lvar, f"{diff.label}  ({diff.status})")
        if diff.type in FILE_BACKED_TYPES:
            writer.raw(f"{tool} /w {diff.lvar} /f msdm.bin")
        else:
            command = fields.lvar_invocation(entry, tool)
            assert command is not None
            writer.raw(command)
        writer.raw(f"{tool} /r {diff.lvar}")
        writer.blank()

    if plan.unrecoverable:
        writer.rule()
        writer.hint("These fields exist on the donor but have NO write path.")
        writer.hint("They cannot be restored by any software tool:")
        for diff in plan.unrecoverable:
            writer.annotate(diff.type, None, f"{diff.label} - {diff.status}")
        writer.rule()

    if plan.extras:
        writer.blank()
        writer.hint("Extra variables on this machine that the donor does not have.")
        writer.hint("Usually leftovers from a probe. Delete only if you are sure:")
        for diff in plan.extras:
            writer.annotate(
                diff.type, diff.lvar, f"{diff.label} - delete with: {tool} /d {diff.lvar}"
                if diff.lvar
                else f"{diff.label} - no LVAR flag"
            )

    writer.blank()
    writer.rule()
    writer.hint("Reboot when done. Verify with read_first.nsh or in BIOS Setup.")
    writer.rule()
    generated.scripts.append(writer.write(out_dir / "restore_dmi.nsh"))

    # ------------------------------------------------------------- stick notes
    notes = [
        "lenovo-dmi-rescue - USB stick payload",
        "=====================================",
        "",
        "Files:",
    ]
    for path in generated.scripts:
        notes.append(f"  {path.name}")
    for path in generated.payloads:
        notes.append(f"  {path.name}")
    notes += [
        "",
        "How to run (UEFI Shell, USB stick as boot device):",
        "",
        f"  fs0:            (try fs1:, fs2: ... until you land on this stick)",
        f"  cd {location}",
        "  read_first.nsh  (read only - shows the current values)",
        "  restore_dmi.nsh (writes the missing fields)",
        "",
        "Both scripts must be pure ASCII with CRLF line endings. Do not edit",
        "them in an editor that changes the encoding, and do not add comments",
        "with non-ASCII characters.",
        "",
        "Reading the output:",
        "  Success!    - the write was accepted",
        "  Not found.  - this variable does not exist on this board",
        "  Region is locked. / Variable is read only.",
        "              - the variable exists but is protected",
        "",
    ]
    write_ascii(out_dir / "README.txt", "\n".join(notes) + "\n")

    return generated


def write_markdown(plan: RestorePlan, path: str | Path) -> Path:
    """Human readable plan, handy for archiving next to the dumps."""
    path = Path(path)
    lines = [
        "# DMI restore plan",
        "",
        f"- donor : `{plan.donor_layout.store.active.generation}` generation, "
        f"{len(plan.donor_layout.store.active.entries())} entries",
        f"- target: `{plan.target_layout.store.active.generation}` generation, "
        f"{len(plan.target_layout.store.active.entries())} entries",
        "",
        "| LENV type | LVAR | field | donor | target | action |",
        "|---|---|---|---|---|---|",
    ]
    for diff in plan.diffs:
        action = {
            "same": "keep",
            "missing": "write",
            "different": "overwrite",
            "extra": "extra",
        }[diff.status]
        if action in ("write", "overwrite") and diff.lvar is None:
            action = "**impossible**"
        donor_value = diff.donor.render() if diff.donor else "-"
        target_value = diff.target.render() if diff.target else "-"
        lines.append(
            f"| `0x{diff.type:04X}` | `{diff.lvar or '-'}` | {diff.label} | "
            f"`{donor_value}` | `{target_value}` | {action} |"
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
