"""Parser level tests for LENV and LDBG blocks."""

from __future__ import annotations

import unittest

from lenovo_dmi_rescue.blocks import (
    NAMESPACE_DMI,
    LdbgBlock,
    LenvBlock,
    LenvEntry,
    build_lenv_block,
)
from lenovo_dmi_rescue.fields import lvar_flag, render_value

from tests._synth import DUMMY_UUID, Synth

SAMPLE = [
    (NAMESPACE_DMI, 0x0000, b"Test Book 15"),
    (NAMESPACE_DMI, 0x0200, b"9999TEST1"),
    (NAMESPACE_DMI, 0x0500, DUMMY_UUID),
    (NAMESPACE_DMI, 0x0001, b"\x01" + b"\x00" * 19 + b"AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"),
]


class LenvBlockTests(unittest.TestCase):
    def test_parses_a_well_formed_block(self):
        block = LenvBlock.parse(Synth.lenv(SAMPLE, generation=7), 0)
        self.assertIsNotNone(block)
        assert block is not None
        self.assertEqual(block.generation, 7)
        self.assertEqual(block.entry_count, len(SAMPLE))
        self.assertTrue(block.checksum_ok())

    def test_rejects_a_corrupted_checksum(self):
        raw = Synth.lenv(SAMPLE, corrupt_checksum=True)
        self.assertIsNone(LenvBlock.parse(raw, 0))

    def test_rejects_a_wrong_magic(self):
        raw = bytearray(Synth.lenv(SAMPLE))
        raw[:4] = b"XENV"
        self.assertIsNone(LenvBlock.parse(bytes(raw), 0))

    def test_rejects_an_impossible_entry_count(self):
        self.assertIsNone(LenvBlock.parse(Synth.lenv(SAMPLE, count=999), 0))

    def test_entries_round_trip(self):
        block = LenvBlock.parse(Synth.lenv(SAMPLE), 0)
        assert block is not None
        entries = block.entries()
        self.assertEqual([e.type for e in entries], [t for _, t, _ in SAMPLE])
        self.assertEqual([e.data for e in entries], [d for _, _, d in SAMPLE])

    def test_renders_guid_and_text(self):
        block = LenvBlock.parse(Synth.lenv(SAMPLE), 0)
        assert block is not None
        uuid_entry = block.find(0x0500)
        assert uuid_entry is not None
        # SMBIOS stores the GUID little endian in its first three fields.
        self.assertEqual(uuid_entry.guid, "33221100-5544-7766-8899-AABBCCDDEEFF")
        product = block.find(0x0000)
        assert product is not None
        self.assertEqual(product.text, "Test Book 15")

    def test_msdm_renders_the_product_key_not_hex(self):
        block = LenvBlock.parse(Synth.lenv(SAMPLE), 0)
        assert block is not None
        msdm = block.find(0x0001)
        assert msdm is not None
        self.assertEqual(render_value(msdm), "AAAAA-BBBBB-CCCCC-DDDDD-EEEEE")

    def test_rebuild_keeps_the_key_and_recomputes_the_checksum(self):
        original = LenvBlock.parse(Synth.lenv(SAMPLE, key=0x43, generation=4), 0)
        assert original is not None
        rebuilt = build_lenv_block(original, list(reversed(original.entries())))
        parsed = LenvBlock.parse(rebuilt, 0)
        assert parsed is not None
        self.assertEqual(parsed.xor_key, 0x43)
        self.assertEqual(parsed.generation, 5)
        self.assertTrue(parsed.checksum_ok())
        self.assertEqual(
            [e.type for e in parsed.entries()], [t for _, t, _ in reversed(SAMPLE)]
        )

    def test_rebuild_refuses_to_overflow_the_block(self):
        block = LenvBlock.parse(Synth.lenv(SAMPLE), 0)
        assert block is not None
        too_big = LenvEntry(NAMESPACE_DMI, 0x9999, b"x" * 0x2000)
        with self.assertRaises(ValueError):
            build_lenv_block(block, [too_big])


class LdbgBlockTests(unittest.TestCase):
    def test_decodes_the_two_byte_bcd_year(self):
        block = LdbgBlock.parse(Synth.ldbg(), 0, xor_key=0x26)
        assert block is not None
        self.assertTrue(block.timestamps_plausible())
        record = block.records()[0]
        assert record.timestamp is not None
        # ``26 20`` is 2026.  Reading only the first byte and adding 2000
        # yields 2038 -- wrong by twelve years, yet still a valid date.
        self.assertEqual(
            record.timestamp.strftime("%Y-%m-%d %H:%M:%S"), "2026-09-23 20:56:13"
        )
        self.assertEqual(record.type, 0x0400)
        self.assertEqual(record.data_size, 8)

    def test_ignores_a_nonsense_stamp(self):
        bad = Synth.ldbg(stamp=(0xFF, 0x20, 0x1F, 0x30, 0x01, 0x12, 0x03))
        block = LdbgBlock.parse(bad, 0, xor_key=0x26)
        assert block is not None
        self.assertIsNone(block.records()[0].timestamp)
        self.assertFalse(block.timestamps_plausible())


class FieldMapTests(unittest.TestCase):
    def test_known_types_and_the_internal_flag(self):
        self.assertEqual(lvar_flag(0x0200), "/mt")
        self.assertEqual(lvar_flag(0x0500), "/uu")
        # No utility can write these, which is why they are reported as
        # impossible in every restore plan.
        self.assertIsNone(lvar_flag(0x0011))
        self.assertIsNone(lvar_flag(0xE10D))


if __name__ == "__main__":
    unittest.main()
