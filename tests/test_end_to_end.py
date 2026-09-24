"""End to end tests: locate an image, plan a repair, then splice or generate."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lenovo_dmi_rescue.locate import LocateError, locate
from lenovo_dmi_rescue.scriptgen import generate, plan_restore, write_markdown
from lenovo_dmi_rescue.splice import SpliceError, splice
from lenovo_dmi_rescue.uefi import NshBuilder, sanitize_echo_line, validate, write_nsh

from tests._synth import Synth


class LocateTests(unittest.TestCase):
    def test_locates_the_store_and_its_journal(self):
        data, base = Synth.image(generation=5)
        layout = locate(data)
        self.assertEqual(layout.store.active.offset, base)
        self.assertEqual(layout.store.stale.offset, base + 0x1000)
        self.assertEqual(layout.store.active.generation, 5)
        assert layout.ldbg is not None
        self.assertEqual(layout.ldbg.offset, base - 0x2000)
        self.assertEqual(layout.region_start, base - 0x2000)
        self.assertEqual(layout.region_end, base + 0x2000)

    def test_raises_when_there_is_no_store(self):
        with self.assertRaises(LocateError):
            locate(b"\xff" * 0x10000)

    def test_ignores_loose_magic_bytes(self):
        # 'LENV' also appears inside BIOS code, so a bare search hit must not
        # be mistaken for a block.
        data = bytearray(b"\xff" * 0x10000)
        data[0x400:0x404] = b"LENV"
        with self.assertRaises(LocateError):
            locate(bytes(data))

    def test_falls_back_when_the_journal_is_missing(self):
        data, base = Synth.image(with_ldbg=False)
        layout = locate(data)
        self.assertIsNone(layout.ldbg)
        self.assertEqual(layout.region_start, base - 0x2000)


class SpliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_touches_only_the_dmi_region(self):
        donor_data, _ = Synth.image(generation=9)
        target_data, _ = Synth.stripped_image()
        result = splice(
            self._write("donor.bin", donor_data),
            self._write("target.bin", target_data),
            self.tmp / "out.bin",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.outside_changes, 0)
        self.assertGreater(result.changed_bytes, 0)

        image = (self.tmp / "out.bin").read_bytes()
        layout = locate(donor_data)
        before = image[layout.store.active.offset : layout.store.active.offset + 0x1000]
        after = donor_data[layout.store.active.offset : layout.store.active.offset + 0x1000]
        self.assertEqual(before, after)

    def test_leaves_other_regions_untouched(self):
        donor_data, _ = Synth.image()
        target_data = bytearray(Synth.stripped_image()[0])
        marker_at = 0x200  # far from the DMI region
        target_data[marker_at : marker_at + 4] = b"NVRM"

        splice(
            self._write("donor.bin", donor_data),
            self._write("target.bin", bytes(target_data)),
            self.tmp / "out.bin",
        )
        image = (self.tmp / "out.bin").read_bytes()
        self.assertEqual(image[marker_at : marker_at + 4], b"NVRM")

    def test_rejects_mismatched_image_sizes(self):
        donor = self._write("donor.bin", Synth.image(total=0x10000)[0])
        target = self._write("target.bin", Synth.image(total=0x20000)[0])
        with self.assertRaises(SpliceError):
            splice(donor, target, self.tmp / "out.bin")

    def test_output_matches_the_donor_field_for_field(self):
        donor_data, _ = Synth.image()
        donor = self._write("donor.bin", donor_data)
        target = self._write("target.bin", Synth.stripped_image()[0])
        splice(donor, target, self.tmp / "out.bin")

        out_map = {
            e.type: e.data for e in locate((self.tmp / "out.bin").read_bytes()).store.active.entries()
        }
        donor_map = {e.type: e.data for e in locate(donor_data).store.active.entries()}
        self.assertEqual(out_map, donor_map)


class RestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.donor = self.tmp / "donor.bin"
        self.target = self.tmp / "target.bin"
        self.donor.write_bytes(Synth.image()[0])
        self.target.write_bytes(Synth.stripped_image()[0])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_plan_separates_restorable_from_impossible(self):
        plan = plan_restore(self.donor, self.target)
        missing = {d.type for d in plan.diffs if d.status == "missing"}
        self.assertTrue({0x0000, 0x0200, 0x0500} <= missing)
        self.assertTrue(all(d.restorable for d in plan.to_write))
        # 0x0011 differs (donor 0xFE, a re-flash leaves 0xFF) and has no flag.
        self.assertIn(0x0011, {d.type for d in plan.unrecoverable})

    def test_script_writes_the_donor_values(self):
        generate(plan_restore(self.donor, self.target), self.tmp / "usb")
        script = (self.tmp / "usb" / "restore_dmi.nsh").read_text(encoding="ascii")
        self.assertIn('/w /mt /c "9999TEST1"', script)
        self.assertIn('/w /pn /c "Test Book 15"', script)
        self.assertIn('/w /uu /b "00 11 22 33 44 55 66 77 88 99 AA BB CC DD EE FF"', script)
        self.assertIn("/w /msdm /f msdm.bin", script)
        self.assertTrue((self.tmp / "usb" / "msdm.bin").exists())
        self.assertTrue((self.tmp / "usb" / "read_first.nsh").exists())

    def test_generated_files_are_ascii_crlf_and_pass_validation(self):
        generate(plan_restore(self.donor, self.target), self.tmp / "usb")
        for path in (self.tmp / "usb").iterdir():
            raw = path.read_bytes()
            self.assertTrue(all(b < 128 for b in raw), f"{path.name} is not ASCII")
            if path.suffix in {".nsh", ".txt"}:
                self.assertEqual(
                    raw.count(b"\r\n"),
                    raw.count(b"\n"),
                    f"{path.name} contains a bare LF",
                )
            if path.suffix == ".nsh":
                self.assertEqual(validate(raw.decode("ascii")), [], path.name)

    def test_no_msdm_payload_when_it_is_already_present(self):
        same = self.tmp / "same.bin"
        same.write_bytes(self.donor.read_bytes())
        generated = generate(plan_restore(self.donor, same), self.tmp / "usb2")
        self.assertEqual(generated.payloads, [])
        self.assertFalse((self.tmp / "usb2" / "msdm.bin").exists())

    def test_markdown_plan_renders(self):
        path = write_markdown(plan_restore(self.donor, self.target), self.tmp / "plan.md")
        self.assertIn("| `0x0200` | `/mt` | MTM |", path.read_text(encoding="utf-8"))


class UefiScriptTests(unittest.TestCase):
    def test_echo_sanitizer_strips_slash_tokens(self):
        self.assertEqual(
            sanitize_echo_line("echo ... TPM / fTPM option"), "echo ... TPM fTPM option"
        )
        self.assertEqual(
            sanitize_echo_line("echo ..... DELETE /mfgmode ....."),
            "echo ..... DELETE mfgmode .....",
        )

    def test_echo_sanitizer_leaves_commands_alone(self):
        command = 'LvarEfi64V252.efi /w /pn /c "Test Book 15"'
        self.assertEqual(sanitize_echo_line(command), command)

    def test_validate_flags_unsafe_echo_and_non_ascii(self):
        problems = validate("echo delete /pn\n")
        self.assertTrue(problems)
        self.assertIn("flag", problems[0])
        problems = validate("echo \u4e2d\u6587\n")
        self.assertTrue(problems)
        self.assertIn("non-ASCII", problems[0])

    def test_write_nsh_refuses_an_unsafe_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                write_nsh(Path(tmp) / "bad.nsh", "echo keep /pn\n")

    def test_builder_never_emits_an_offending_echo(self):
        builder = NshBuilder(title="hints mentioning /pn and /mfgmode")
        builder.annotate(0x000D, "/mfgmode", "the MFG flag")
        self.assertEqual(validate(builder.text()), [])

    def test_written_script_has_crlf(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = NshBuilder().hint("hello").write(Path(tmp) / "ok.nsh")
            raw = path.read_bytes()
            self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
            self.assertTrue(all(b < 128 for b in raw))


if __name__ == "__main__":
    unittest.main()
