"""Installer regression checks. Never modifies the real registry or elevates."""
import hashlib
import io
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from m4sconverter import ffmpeg_setup as setup


class InstallerTests(unittest.TestCase):
    def test_path_preserves_existing_and_deduplicates(self):
        value = r"%SystemRoot%\system32;C:\Existing"
        self.assertEqual(setup.add_path_entry(value, r"C:\New"), value + r";C:\New")
        self.assertEqual(setup.add_path_entry(value, "c:\\existing\\"), value)
        self.assertEqual(setup.add_path_entry("", r"C:\New"), r"C:\New")
        self.assertEqual(setup.add_path_entry("a;", "b"), "a;b")

    def test_invalid_or_non_ffmpeg_executable(self):
        with patch.object(setup.subprocess, "run", return_value=Mock(returncode=0, stdout=b"some program")):
            self.assertFalse(setup.validate_ffmpeg(Path("fake.exe")))
        with patch.object(setup.subprocess, "run", side_effect=OSError("broken")):
            self.assertFalse(setup.validate_ffmpeg(Path("fake.exe")))

    def test_checksum_pass_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "archive.zip"
            file.write_bytes(b"example")
            digest = hashlib.sha256(b"example").hexdigest()
            with patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(digest.encode())):
                setup.verify_archive(file)
            with patch.object(setup.urllib.request, "urlopen", return_value=io.BytesIO(b"0" * 64)):
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    setup.verify_archive(file)

    def test_extract_never_uses_archive_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "test.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("../../outside.txt", b"bad")
                z.writestr("../../ffmpeg.exe", b"ffmpeg")
                z.writestr("root/bin/ffprobe.exe", b"ffprobe")
            target = root / "bin"
            setup.extract_tools(archive, target)
            self.assertEqual(sorted(p.name for p in target.iterdir()), ["ffmpeg.exe", "ffprobe.exe"])
            self.assertFalse((root / "outside.txt").exists())

    def test_missing_binary_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "test.zip"
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr("ffmpeg.exe", b"ffmpeg")
            with self.assertRaisesRegex(ValueError, "ffprobe.exe"):
                setup.extract_tools(archive, Path(tmp) / "bin")

    def test_cancel_does_not_elevate_or_touch_path(self):
        cancel = threading.Event()
        cancel.set()
        with patch.object(setup, "download_archive"), patch.object(setup, "_run_elevated") as elevate, patch.object(setup, "register_user_path") as register:
            with self.assertRaises(setup.InstallCancelled):
                setup.install_ffmpeg(True, cancel, Mock())
            elevate.assert_not_called()
            register.assert_not_called()

    def test_user_and_system_install_flows(self):
        for system in (False, True):
            with self.subTest(system=system), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "installed"
                def extract(archive, staging):
                    staging.mkdir()
                    for name in ("ffmpeg.exe", "ffprobe.exe"):
                        (staging / name).write_bytes(b"verified")
                with patch.dict(os.environ), patch.object(setup, "download_archive"), patch.object(setup, "verify_archive"), patch.object(setup, "extract_tools", side_effect=extract), patch.object(setup, "validate_ffmpeg", return_value=True), patch.object(setup, "install_bin", return_value=target), patch.object(setup, "_broadcast_environment"), patch.object(setup, "_run_elevated") as elevate, patch.object(setup, "register_user_path") as register:
                    self.assertEqual(setup.install_ffmpeg(system, threading.Event(), Mock()), target / "ffmpeg.exe")
                    if system:
                        elevate.assert_called_once()
                        register.assert_not_called()
                    else:
                        elevate.assert_not_called()
                        register.assert_called_once_with(target)
                        self.assertTrue((target / "ffprobe.exe").is_file())
                    self.assertIn(str(target), os.environ["PATH"])

    def test_elevated_script_destination_and_quoting(self):
        script = setup.system_install_script(Path("C:/a'b"), {"ffmpeg.exe": "a" * 64, "ffprobe.exe": "b" * 64})
        self.assertIn("C:\\a''b" if os.name == "nt" else "C:/a''b", script)
        self.assertIn("GetFolderPath('ProgramFiles')", script)
        self.assertIn("DoNotExpandEnvironmentNames", script)
        self.assertIn("Get-FileHash", script)
        self.assertNotIn("SOURCE", script)


if __name__ == "__main__":
    unittest.main(verbosity=2)
