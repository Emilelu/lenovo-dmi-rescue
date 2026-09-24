"""Command line entry point.

    dmi-rescue scan     dump.bin                  what is in this image?
    dmi-rescue diff     backup.bin current.bin    field by field comparison
    dmi-rescue splice   backup.bin current.bin -o fixed.bin
    dmi-rescue restore  backup.bin current.bin -o usb/
    dmi-rescue verify   backup.bin fixed.bin
    dmi-rescue journal  dump.bin                  decode the LDBG journal
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .locate import LocateError, locate
from .report import render_diff, render_entries, render_journal, render_layout
from .scriptgen import generate, plan_restore, write_markdown
from .splice import SpliceError, splice

EPILOG = """\
Two ways to get the machine identity back after a bad flash:

  splice   Offline.  Copy the LENV blocks from a trusted backup into the freshly
           flashed image, then write the result with an SPI programmer.  Exact,
           byte for byte, but the case has to come apart.

  restore  In place.  Generate UEFI Shell scripts that write the same variables
           through the firmware itself.  No disassembly, no programmer, but a
           few internal variables have no write path and stay as they are.

Run 'scan' first to see what the images actually contain.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dmi-rescue",
        description="Recover Lenovo DMI (MTM / serial / UUID / OA3) after a bad BIOS flash.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("scan", help="locate the DMI store in one image and print it")
    p.add_argument("image", help="full SPI dump")

    p = sub.add_parser("diff", help="compare DMI between a backup and a current dump")
    p.add_argument("donor", help="trusted backup image")
    p.add_argument("target", help="current (stripped) image")

    p = sub.add_parser("splice", help="method A: build a flashable image")
    p.add_argument("donor", help="trusted backup image")
    p.add_argument("target", help="current (stripped) image")
    p.add_argument("-o", "--output", required=True, help="image to write")
    p.add_argument(
        "--include-ldbg",
        action="store_true",
        help="also copy the change journal (not needed in normal use)",
    )

    p = sub.add_parser("restore", help="method B: generate UEFI scripts for a USB stick")
    p.add_argument("donor", help="trusted backup image")
    p.add_argument("target", help="current (stripped) image")
    p.add_argument("-o", "--out-dir", required=True, help="directory for the USB payload")
    p.add_argument(
        "--tool",
        default="LvarEfi64V252.efi",
        help="name of the LVAR EFI binary on the stick (default: %(default)s)",
    )
    p.add_argument("--markdown", help="also write the plan to this .md file")

    p = sub.add_parser("verify", help="re-check a spliced image against its donor")
    p.add_argument("donor", help="the backup the image was built from")
    p.add_argument("image", help="image produced by splice")

    p = sub.add_parser("journal", help="decode the LDBG change journal")
    p.add_argument("image", help="full SPI dump")
    p.add_argument("-n", "--limit", type=int, default=40, help="records to show")

    return parser


def _cmd_scan(args: argparse.Namespace) -> int:
    data = Path(args.image).read_bytes()
    print(f"image {args.image}  ({len(data)} bytes)")
    print()
    layout = locate(data)
    print(render_layout(layout))
    print()
    print(render_entries(layout))
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    plan = plan_restore(args.donor, args.target)
    print(render_diff(plan))
    if plan.unrecoverable:
        print()
        print("fields with no write path (cannot be restored by software):")
        for diff in plan.unrecoverable:
            print(f"  0x{diff.type:04X}  {diff.label}  ({diff.status})")
    return 0


def _cmd_splice(args: argparse.Namespace) -> int:
    result = splice(
        args.donor,
        args.target,
        args.output,
        include_ldbg=args.include_ldbg,
    )
    print(result.render())
    if not result.ok:
        print()
        print("ABORT: changes leaked outside the DMI region. Do not flash this image.")
        return 2
    print()
    print("Next: write this file with your SPI programmer, then boot and verify.")
    return 0


def _cmd_restore(args: argparse.Namespace) -> int:
    plan = plan_restore(args.donor, args.target)
    print(render_diff(plan))
    print()
    generated = generate(plan, args.out_dir, tool=args.tool)
    if args.markdown:
        path = write_markdown(plan, args.markdown)
        print(f"plan written to {path}")
    print()
    print(f"USB payload written to {generated.out_dir}")
    for path in generated.all:
        print(f"  {path.name}")
    print(Path(generated.out_dir, "README.txt").read_text(encoding="ascii"))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    donor_layout = locate(Path(args.donor).read_bytes())
    image_layout = locate(Path(args.image).read_bytes())
    donor = {e.type: e for e in donor_layout.store.active.entries() if e.is_dmi}
    image = {e.type: e for e in image_layout.store.active.entries() if e.is_dmi}

    missing = sorted(set(donor) - set(image))
    differing = sorted(t for t in set(donor) & set(image) if donor[t].data != image[t].data)

    print(f"donor entries : {len(donor)}")
    print(f"image entries : {len(image)}")
    print()
    if not missing and not differing:
        print("OK - every donor field is present with the same value.")
        extras = sorted(set(image) - set(donor))
        if extras:
            print("extra fields in the image (harmless):")
            for etype in extras:
                print(f"  0x{etype:04X}  {image[etype].render()}")
        return 0

    if missing:
        print("MISSING from the image:")
        for etype in missing:
            print(f"  0x{etype:04X}  {donor[etype].render()}")
    if differing:
        print("DIFFERENT:")
        for etype in differing:
            print(f"  0x{etype:04X}  donor={donor[etype].render()}  image={image[etype].render()}")
    return 1


def _cmd_journal(args: argparse.Namespace) -> int:
    layout = locate(Path(args.image).read_bytes())
    print(render_layout(layout))
    print()
    print(render_journal(layout.ldbg, limit=args.limit))
    return 0


HANDLERS = {
    "scan": _cmd_scan,
    "diff": _cmd_diff,
    "splice": _cmd_splice,
    "restore": _cmd_restore,
    "verify": _cmd_verify,
    "journal": _cmd_journal,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except LocateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except SpliceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 4
