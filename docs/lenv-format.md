# The LENV variable store

Everything below was derived by reading real SPI images and cross-checking
against the behaviour of Lenovo's own `LVAR` utility. Nothing here is
speculative unless it says so.

## Why the serial number is not greppable

The data is XOR-obfuscated with a single byte key stored in the block header, so
`grep -a "PF27SLDS" dump.bin` finds nothing. A plain `b"LENV"` search *does* hit,
but most of those hits are false — the four bytes also occur inside BIOS code
and inside PSP directory entries — which is why candidates must be validated
structurally rather than by magic alone.

## Block layout

Three contiguous blocks. Their absolute offset changes between BIOS versions and
board revisions, so it is derived from the image every time:

| block | size | contents |
|---|---|---|
| `LDBG` | `0x2000` | append-only change journal |
| `LENV1` | `0x1000` | variable store, copy A |
| `LENV2` | `0x1000` | variable store, copy B |

Firmware reads whichever copy has the higher `generation`. The other one is
stale, not corrupt — both carry valid checksums.

The journal normally sits immediately *before* the first store copy, but code
should not assume it exists: a journal that has been sliced off, or an image
trimmed to the store, is still usable.

## LENV block

```
offset  size  field
------  ----  -----
0x00    4     magic "LENV"
0x04    4     generation          u32 LE
0x08    4     entry count         u32 LE
0x0C    1     access flag
0x0D    1     XOR key
0x0E    2     checksum            u16 LE
0x10    ...   body (XOR obfuscated)
```

The checksum is the **sum of the encrypted body truncated to 16 bits**:

```python
checksum == sum(raw[0x10:]) & 0xFFFF
```

Two consequences worth noting:

* The checksum is computed over the *ciphertext*, not the plaintext.
* Copying a whole block wholesale preserves validity for free, because the key
  travels with it. Two images can use different keys (`0x26` vs `0x43` have both
  been seen) and this does not matter for a block copy.

## Entries

The decrypted body is a packed array of entries:

```
offset  size  field
------  ----  -----
0x00    14    namespace id
0x0E    2     type                u16 LE
0x10    4     data size           u32 LE
0x14    1     flags
0x15    1     reserved
0x16    2     reserved
0x18    n     data
```

Two namespaces have been observed:

| namespace id (hex) | meaning |
|---|---|
| `55570ec26911564ca48a9824ab43` | DMI fields — everything in the field map |
| `468f4464236e88429349fdd887c4` | internal (type `0xE10D`, eight zero bytes) |

Anything past the last entry is padding and reads as `0x00` after decryption.

## LDBG journal

```
offset  size  field
------  ----  -----
0x00    4     magic "LDBG"
0x04    4     write offset        u32 LE
0x08    0x18  padding (plaintext)
0x20    ...   records (XOR obfuscated with the LENV1 key)
```

The first `0x20` bytes are plaintext; everything after that is XOR obfuscated
with the same key as `LENV1`. Valid `write_offset` values run from `0x20` up to
the block size; records occupy `0x20 .. write_offset`.

Each record is a fixed `0x20` bytes:

```
offset  size  field
------  ----  -----
0x00    2     year                BCD, little endian  <- see below
0x02    1     month               BCD
0x03    1     day                 BCD
0x04    1     hour                BCD
0x05    1     minute              BCD
0x06    1     second              BCD
0x07    1     operation
0x08    14    namespace id
0x16    2     type                u16 LE
0x18    4     data size           u32 LE
```

This mirrors a UEFI `EFI_TIME`, with every field packed BCD.

### The year is two bytes, not one

This is the trap. The bytes `26 20` mean **2026**, because the year is a two byte
*binary coded decimal* value, little endian: low byte `0x26` -> 26, high byte
`0x20` -> 20, combined as `20 * 100 + 26`.

Reading only the first byte and adding 2000 gives **2038** — which is a perfectly
valid looking date, off by twelve years, and passes any "does this look real"
sanity check you might write. A journal full of 2038 timestamps is the symptom.

Decode it as:

```python
year = bcd(raw[1]) * 100 + bcd(raw[0])
```

Records with non-BCD nibbles (typically an all-`0xFF` first record on a
re-flashed board) have no valid timestamp and should be skipped rather than
guessed at.

## Locating the store

`b"LENV"` alone is not enough. Accept a candidate only if:

1. the magic matches at the offset,
2. `sum(raw[0x10:]) & 0xFFFF` equals the stored checksum,
3. the entry count is plausible (`<= 0x40`),
4. walking that many entries lands exactly at or before the block end, and every
   data size along the way is plausible.

Then pair blocks that are exactly `0x1000` apart — a store is a *pair*, and a
lone block is a false hit or a truncated image. When several pairs exist, prefer
the one whose active block has the most entries, then the highest generation: a
populated store beats an empty skeleton.

The full procedure lives in `lenovo_dmi_rescue/locate.py`.
