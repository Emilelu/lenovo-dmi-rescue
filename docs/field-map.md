# LENV type and LVAR flag map

## How this table was produced

By **probing**, not by reading. Write a sentinel value with the LVAR utility,
re-dump the SPI image, and see which type changed:

```bash
# on the machine, from the UEFI Shell
LvarEfi64V252.efi /w /sn /c "1111111111111"

# back in Windows, dump read-only and diff
H2OFFT-Wx64.exe fresh.bin -g
dmi-rescue diff before.bin fresh.bin
```

The order of strings inside the LVAR binary is **not** a reliable guide. On the
first attempt `/at` and `/osp` were assigned the wrong way round by reading the
string table, and that error survived into a working restore script.

## Types

| LENV type | LVAR flag | field | notes |
|---|---|---|---|
| `0x0000` | `/pn` | Product Name | e.g. `Lenovo Legion R7000 2020` |
| `0x0001` | `/msdm` | OA3 / MSDM | 20 byte header + 29 char key = 49 bytes |
| `0x0005` | `/cf` | Keyboard ID | 8 bytes |
| `0x000B` | `/oa3keyid` | OA3 Key ID | 13 digits |
| `0x000D` | `/mfgmode` | MFG mode flag | `'Y'` while in manufacturing mode |
| `0x0011` | — | internal flag | **no write path** |
| `0x0100` | `/pjn`, `/pn2` | Project Name | Baseboard Product; `LNVNB161216` on Legion |
| `0x0200` | `/mt` | MTM | machine type model, e.g. `82B6000ACD` |
| `0x0300` | `/sn` | Serial Number 1 | 13 digits |
| `0x0400` | `/ln` | Lenovo SN | sticker serial |
| `0x0500` | `/uu` | UUID | 16 bytes, SMBIOS byte order |
| `0x0700` | `/kd` | KD flag | observed value `'S'` |
| `0x0B00` | `/fd` | Family Name | e.g. `Legion R7000 2020` |
| `0x0C00` | `/at` | Asset Tag | **absent on some consumer models** |
| `0x0F00` | `/osp` | OS PN number | e.g. `SDK0L77769` |
| `0x1000` | `/oss` | OS descriptor | `WIN` |
| `0xE10D` | — | internal | eight zero bytes |

### Where each SMBIOS field comes from

| SMBIOS / WMI | LENV type |
|---|---|
| Type 1 UUID (`Win32_ComputerSystemProduct.UUID`) | `0x0500` |
| Type 1 Version | `0x0000` |
| Type 1 SerialNumber | `0x0400` |
| Type 2 BaseBoard Product | `0x0100` — missing gives `INVALID` |
| ACPI MSDM / `OA3xOriginalProductKey` | `0x0001` |
| BIOS Setup "Preinstalled OS license" | `0x0F00` + `" "` + `0x1000` |

Note that `Win32_ComputerSystem.Model` is **not** the MTM: it is the short model
code (`82B6`). The full MTM only exists in the variable store and in BIOS Setup.

## Not every flag exists on every board

The utility exposes 24 flags. On one Legion R7000 2020, only 14 were present:

| status | flags |
|---|---|
| present | `/pn` `/pn2` `/mt` `/sn` `/ln` `/uu` `/pjn` `/fd` `/osp` `/oss` `/msdm` `/cf` `/kd` `/mfgmode` |
| `Not found` | `/mfg` `/comp` `/sku` `/slic` `/cd` `/fl` `/odmpjn` `/el` `/at` |

Check before you plan around a flag:

```bash
LvarEfi64V252.efi /r /<flag>      # "Not found." means it does not exist here
```

The generator does this for you: fields with no LVAR flag are reported as
unrecoverable rather than emitted as commands that would fail.

## Value encoding

| shape | written with |
|---|---|
| printable ASCII | `/c "value"` |
| binary (UUID, keyboard ID) | `/b "00 11 22 33 ..."` |
| OA3 / MSDM | `/f msdm.bin` — 49 bytes cannot go on a command line |

Digit-only values such as the serial number are written with `/c` rather than
`/b`. Both produce identical bytes, but a readable command line can be checked
against the donor table before it runs, and a typo in a long hex string is far
easier to miss.

## The internal flag, `0x0011`

| question | answer |
|---|---|
| Is it the MFG mode flag? | **No.** It is independent of `0x000D`: a re-flash takes `0x0011` from `0xFE` to `0xFF` while `0x000D` is not created at all, and writing `/mfgmode Y` afterwards leaves `0x0011` untouched |
| Can `/mfg` write it? | **No.** `/r /mfg` returns `Not found` on the affected board |
| Does any other flag map to it? | **No.** All 24 were checked |
| What is it? | **Speculation:** a state bit maintained by the flash process itself. `0xFE` and `0xFF` differ only in bit 0, which is the shape of a "written / not written" marker |
| Does it matter? | **No observable effect.** It maps to no LVAR flag, appears in no SMBIOS field, and no journal record references it |

Restoring that byte requires a programmer.
