# MFG MODE — the one thing this tool cannot fix

If BIOS Setup shows `MFG MODE` where the security processor setting should read
Enabled or Disabled, that is a different problem from a wiped DMI store, and
**no user-level software fix exists**. This document records what was ruled out
so nobody has to repeat the work.

## The symptom

Security tab, one line that should be toggleable:

```
AMD Platform Security Processor        MFG MODE
```

or on Intel machines:

```
Intel Platform Trust Technology        MFG MODE
```

That setting *is* the TPM switch. Enabled turns the TPM on, Disabled turns it
off. `MFG MODE` is neither, and the row cannot be changed.

## First: check whether the TPM actually works

**The label being wrong does not mean the TPM is broken.** On a machine with
this exact symptom, Windows reported a fully working TPM:

```powershell
PS> Get-Tpm
TpmPresent                : True
TpmReady                  : True
TpmEnabled                : True
TpmActivated              : True
TpmOwned                  : True
ManufacturerIdTxt         : AMD
ManufacturerVersion       : 3.47.0.5
AutoProvisioning          : Enabled
LockedOut                 : False
```

| result | meaning |
|---|---|
| `TpmPresent` and `TpmReady` both `True` | **nothing to fix.** BitLocker, Windows Hello, Secure Boot and Windows 11 setup all work. The BIOS label is cosmetic |
| `TpmPresent: False` | the TPM really is unavailable, and this is the vendor's problem |

Time spent "repairing" a working TPM is time spent risking a working machine.

## What was ruled out

Each of these was tested on hardware, or documented from a case where it was:

| attempt | outcome |
|---|---|
| Toggle the row in BIOS Setup | not possible — the value is `MFG MODE`, not an enum |
| AC power cycle (unplug, hold power 30 s) | no change |
| Load Setup Defaults | no change |
| Delete the `mfgmode` variable with LVAR | deleted successfully (`Not found` on read-back); MFG MODE persisted |
| `DisableMfgMode.efi` (Wistron production tool) | runs silently, no effect; the state re-reads as enabled |
| Compare the PSP firmware region against a pre-brick backup | **byte identical** — the state is not persisted there |
| Re-flash the vendor BIOS | no change (§case reports below) |
| CMOS clear, clean OS install, driver reinstall | no change |

The third result is the decisive one: if the PSP region is identical between a
dump that was fine and one that reports MFG MODE, the flag is not stored in PSP
firmware. It is behaviour of the security processor at POST, not a writable
variable.

The variable that *does* differ is the internal flag `0x0011` (`0xFE` -> `0xFF`
across a re-flash, see [`field-map.md`](field-map.md)). Its timing matches the
symptom but it has no write path, so it cannot be used — and no evidence shows
that changing it would help.

## Why there is no user-level fix

Lenovo's own documentation is explicit:

> **Security Chip** — If shows MFG Mode (manufacturing mode), then TPM must be
> provisioned correctly. If this occurs on a ship-level system, please contact
> Lenovo Support for assistance.
>
> — Lenovo Commercial Deployment Readiness Team, security chip settings

The store that would have to be rewritten sits behind protections that block
software writes. On the equivalent Intel problem, a repair forum puts it plainly:
the ME is already in Production Mode but the BIOS flag will not clear, and the
closing commands are blocked by the flash descriptor — so the practical route is
to write an image whose MFG mode is already off.

## Cases from the field

| source | machine | result |
|---|---|---|
| Lenovo documentation | ThinkPad, general | contact support — TPM was not provisioned correctly |
| Intel community | Legion 5 15IMH05 (82AU) | factory keys restore, setup defaults, re-flash, EC/power/CMOS reset, clean Windows install: **all ineffective**; vendor answer: a ME/PTT region problem that user settings cannot resolve |
| Intel community | Legion | same symptom, same conclusion |
| repair forum (EletronicaBR) | IdeaPad Gaming 3i NM-C871 | flag stuck in NVRAM; software close commands blocked by the flash descriptor; recommended route is reflashing with a clean image and merging the original DMI into it |
| Microsoft Q&A | IdeaPad 5 AMD | `fTPM/PSP NV corrupted` prompt offering `Erase PSP NVRAM` |
| yomotherboard | Lenovo AMD | `PSP found PSP NVRAM exist but not healthy`, offering to clear the fTPM (type 0x04) and RPMV (type 0x54) regions |

## Do not try to switch it off

Turning the security processor off has a documented failure mode on at least one
machine matching this symptom — a Legion R7000 2020 on BIOS EUCN41WW:

> disabling AMD PSP in BIOS, save and exit, the Legion logo shows for two
> seconds, then black, then the logo again, looping

An infinite boot loop is a worse outcome than a cosmetic label.

## Recommendation

If `Get-Tpm` reports a ready TPM: leave it alone. The row in BIOS Setup is
wrong, everything that depends on the TPM works, and the only routes to a
different label are a vendor repair or a programmer-level rewrite of regions
this tool deliberately does not touch.

If the TPM is genuinely absent: that is a warranty conversation, not a software
problem.
