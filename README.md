# lenovo-dmi-rescue

Recover the machine identity data — product name, MTM, serial numbers, UUID and
the Windows OA3 product key — that Lenovo InsydeH2O firmware keeps in an
obfuscated variable store, after a bad BIOS flash has wiped it.

No dependencies. Pure standard library Python 3.10+, so it runs from a rescue
USB stick or a borrowed laptop when the machine being repaired will not boot.

```
dmi-rescue scan     dump.bin                  what is in this image?
dmi-rescue diff     backup.bin current.bin    field by field comparison
dmi-rescue splice   backup.bin current.bin -o fixed.bin
dmi-rescue restore  backup.bin current.bin -o usb/
dmi-rescue verify   backup.bin fixed.bin
dmi-rescue journal  dump.bin                  who wrote what, and when
```

---

## The situation this solves

You brick a Lenovo laptop, recover it with a CH341A, flash a clean vendor BIOS —
and now the machine boots fine but BIOS Setup shows `INVALID` for the product
name, the baseboard model is gone, the Windows licence has deactivated, and
`Win32_ComputerSystemProduct.UUID` is blank.

The data was never in plain text, so a `grep` for your serial number finds
nothing. It lives in `LENV` blocks: XOR-obfuscated, checksummed, duplicated, and
sitting at an offset that changes between BIOS versions.

Meanwhile the *old* dump you made before things went wrong still has all of it.

This tool moves that data back, either way you want to do it.

## Two ways to repair

| | `splice` — programmer | `restore` — UEFI scripts |
|---|---|---|
| Opens the case | **yes** | no |
| Needs a CH341A / SPI clip | yes | no |
| Result | byte-identical to the donor | every writable field matches |
| Internal variables (no LVAR flag) | restored | **cannot be restored** |
| Risk | bricking on a bad write | a mistyped value in a field |
| Speed | ~20 min | ~10 min |

Pick `splice` when you want it exactly right and you are already opening the
machine. Pick `restore` when the machine boots and you would rather not take it
apart — which is most of the time.

Both start from the same analysis, so run `diff` first and see what you are
dealing with.

---

## Quick start

Nothing to install:

```bash
git clone https://github.com/Emilelu/lenovo-dmi-rescue
cd lenovo-dmi-rescue
python -m pip install -e .      # optional, only adds the `dmi-rescue` command
```

Run it straight from the checkout if you prefer:

```bash
PYTHONPATH=src python -m lenovo_dmi_rescue scan dump.bin
```

### 1. See what you have

```
$ dmi-rescue scan current.bin
image current.bin  (16777216 bytes)

LDBG   @ 0x75A000   write_offset=0x300   xor=0x43
LENV1  @ 0x75C000   gen=31    entries=16  xor=0x43   checksum=OK   <- active
LENV2  @ 0x75D000   gen=30    entries=15  xor=0x43   checksum=OK
region 0x75A000 - 0x75E000 (16384 bytes)

entries (active block @ 0x75C000, generation 31):

  type    LVAR        field            value
  ------- ----------- ---------------  ----------------------------------
  0x0100  /pjn        Project Name     LNVNB161216
  0x0200  /mt         MTM              9999ABCD1
  0x0000  /pn         Product Name     Lenovo Legion R7000 2020
  ...
```

### 2. Compare against the backup

```
$ dmi-rescue diff backup.bin current.bin

  type    LVAR        field                  status    donor -> target
  ------- ----------- ---------------------- --------- ------------------
  0x0100  /pjn        Project Name           ok        LNVNB161216 -> LNVNB161216
  0x0200  /mt         MTM                    WRITE     9999ABCD1 -> (absent)
  0x0500  /uu         UUID                   WRITE     ... -> (absent)
  0x0001  /msdm       OA3 / MSDM             WRITE     XXXXX-XXXXX-... -> (absent)
  0x0011  -           internal flag          NO WAY    FE -> FF

  restorable : 11
  already ok : 2
  impossible : 1
```

`NO WAY` means the field exists on the donor but has no write path at all —
see [Known limits](#known-limits).

### 3a. Method A — build a flashable image

```
$ dmi-rescue splice backup.bin current.bin -o fixed.bin

  DMI region        : 0x75C000 - 0x75E000  (8192 bytes)
  blocks copied     : 0x75C000->0x75C000, 0x75D000->0x75D000
  bytes changed     : 8153
  changes outside region: 0  (clean)
```

The region check is the point: if anything changed *outside* the DMI area, the
tool exits with an error and tells you not to flash it. Your NVRAM, Wi-Fi
pairings and boot history stay exactly as they are.

Write `fixed.bin` with your programmer, then:

```
$ dmi-rescue verify backup.bin fixed.bin
OK - every donor field is present with the same value.
```

### 3b. Method B — build a USB stick

```
$ dmi-rescue restore backup.bin current.bin -o /media/usb/dmi
```

Produces:

| file | purpose |
|---|---|
| `read_first.nsh` | read-only; writes nothing, shows every current value |
| `restore_dmi.nsh` | writes each field that is missing or wrong |
| `msdm.bin` | the OA3 payload, only when it needs restoring |
| `README.txt` | instructions for the stick itself |

Boot the stick, drop to the UEFI Shell, and run the scripts:

```
fs0:            (try fs1:, fs2: ... until you land on the stick)
cd dmi
read_first.nsh
restore_dmi.nsh
```

The generator handles the two things that quietly break a `.nsh` file:

* **ASCII + CRLF only.** A UTF-8 comment on a GBK console can swallow the CR of
  a CRLF pair and glue two commands together.
* **No slash-prefixed tokens in `echo` lines.** The shell reads `/mfgmode` as an
  unknown flag and drops the entire line, so hint text vanishes exactly when the
  operator needs it. The builder strips them and refuses to emit a file that
  still contains one.

### Bonus: who wrote what, and when

`LDBG` is an append-only journal. Every record carries a timestamp, which is how
you tell factory data from something a repair utility wrote later:

```
$ dmi-rescue journal current.bin

  2026-09-23 20:56:13  0x02  0x0400  Lenovo SN
  2026-09-23 20:56:17  0x02  0x0200  MTM
  2026-09-23 20:56:20  0x02  0x0000  Product Name
  2026-09-23 20:56:24  0x02  0x0B00  Family Name
  2026-09-23 20:56:28  0x02  0x0500  UUID
```

Five fields written seconds apart is a utility run, not a factory.

---

## What it knows about LENV

Three contiguous blocks, the absolute offset of which varies by BIOS version and
is therefore always derived from the image:

```
LDBG   0x2000   append-only change journal
LENV1  0x1000   variable store, copy A
LENV2  0x1000   variable store, copy B (higher generation wins)
```

Each block body is XOR-obfuscated with a single byte key stored in the header.
A raw `b"LENV"` search returns loose hits from BIOS code and PSP directory
entries, so every candidate is validated structurally — checksum *and* an entry
walk that lands on the block boundary — before it is accepted.

See [`docs/lenv-format.md`](docs/lenv-format.md) for the byte layout and
[`docs/field-map.md`](docs/field-map.md) for the type table.

## Known limits

**The internal flag `0x0011` cannot be restored by any software.**

It goes `0xFE` on a factory image and `0xFF` after a full re-flash. It maps to no
LVAR flag, appears in no SMBIOS field, and nothing in the firmware appears to
read it. On a real machine this was confirmed three ways: it is independent of
the MFG-mode variable's lifecycle, the only untested candidate flag turns out
not to exist on that board, and none of the 24 flags in the utility address it.

Byte-level restoration of that one byte needs a programmer.

**Some LVAR flags do not exist on every board.** On one Legion R7000 2020, 9 of
the 24 flags returned `Not found`. `restore` reports this rather than emitting a
command that would fail.

**MFG MODE is out of scope.** If BIOS Setup shows `MFG MODE` instead of
Enabled/Disabled for the security processor, that is a firmware provisioning
issue with no user-level fix. See [`docs/mfg-mode.md`](docs/mfg-mode.md).

---

## Tested on

| machine | BIOS | what was done |
|---|---|---|
| Lenovo Legion R7000 2020 (82B6) | EUCN41WW | full DMI restore, both methods |

Reports from other InsydeH2O Lenovo machines are welcome — the parser is written
around the structure rather than a hard-coded offset, so it should generalise.

## Safety

Read this before running anything.

* **Keep every dump.** `splice` writes a *new* file and never touches its
  inputs, but you should still treat your original images as irreplaceable.
* **Do not flash the whole donor image.** Only the DMI region is transplanted.
  A whole-image write drags the donor's NVRAM, stale boot variables, Wi-Fi
  pairings and crash history onto the machine.
* **Changing UUID and baseboard fields deactivates Windows.** They are part of
  Microsoft's activation hardware hash. Reactivation is a few seconds:

  ```powershell
  $p = Get-CimInstance -Namespace root\cimv2 -ClassName SoftwareLicensingProduct |
       Where-Object { $_.PartialProductKey -eq '3V66T' -and $_.Name -like '*Windows(R), Professional edition*' }
  Invoke-CimMethod -InputObject $p -MethodName Activate
  ```

* **Never commit a firmware dump.** It carries your serial number, UUID,
  product key and Wi-Fi pairings. `.gitignore` blocks the usual patterns; the
  test suite uses synthetic images only.

## Licence

MIT — see [LICENSE](LICENSE).

Not affiliated with Lenovo. This is a repair tool for hardware you own.
