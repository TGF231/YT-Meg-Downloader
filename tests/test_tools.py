"""Testes da localização de ferramentas externas, sem depender do sistema."""

import os
import pathlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pen_drive_maker.tools import (  # noqa: E402
    describe_missing_ffmpeg,
    find_executable,
    find_ffmpeg,
    iter_path_dirs,
    runs_ok,
)

IS_WINDOWS = os.name == "nt"


def make_fake_ffmpeg(directory: pathlib.Path, name: str = "ffmpeg") -> pathlib.Path:
    """Cria um executável que responde a -version como o ffmpeg."""
    directory.mkdir(parents=True, exist_ok=True)
    if IS_WINDOWS:
        path = directory / f"{name}.bat"
        path.write_text("@echo ffmpeg version 7.0 fake\r\n", encoding="utf-8")
    else:
        path = directory / name
        path.write_text("#!/bin/sh\necho 'ffmpeg version 7.0 fake'\n", encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class PathParsingTest(unittest.TestCase):
    """Formatos de PATH que o shell aceita e o shutil.which não."""

    def setUp(self):
        self.original_path = os.environ.get("PATH", "")
        self.folder = pathlib.Path(tempfile.mkdtemp()) / "bin"
        make_fake_ffmpeg(self.folder)

    def tearDown(self):
        os.environ["PATH"] = self.original_path

    def _only_dir(self, raw_entry: str):
        os.environ["PATH"] = raw_entry
        return [str(p) for p in iter_path_dirs()]

    def test_plain_entry(self):
        self.assertIn(str(self.folder), self._only_dir(str(self.folder)))

    def test_quoted_entry(self):
        self.assertIn(str(self.folder), self._only_dir(f'"{self.folder}"'))

    def test_entry_with_surrounding_spaces(self):
        self.assertIn(str(self.folder), self._only_dir(f"  {self.folder}  "))

    def test_entry_with_environment_variable(self):
        os.environ["PDM_TEST_DIR"] = str(self.folder)
        marker = "%PDM_TEST_DIR%" if IS_WINDOWS else "$PDM_TEST_DIR"
        self.assertIn(str(self.folder), self._only_dir(marker))

    def test_empty_entries_are_skipped(self):
        self.assertEqual(self._only_dir(os.pathsep * 3), [])

    def test_finds_ffmpeg_through_a_quoted_path_entry(self):
        """O caso relatado: está no PATH, mas entre aspas."""
        os.environ["PATH"] = f'"{self.folder}"'
        found = find_ffmpeg()
        self.assertIsNotNone(found, "deveria achar mesmo com a entrada entre aspas")
        self.assertTrue(found.startswith(str(self.folder)))


class ConfiguredPathTest(unittest.TestCase):
    def setUp(self):
        self.original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = ""
        self.folder = pathlib.Path(tempfile.mkdtemp()) / "bin"
        self.exe = make_fake_ffmpeg(self.folder)

    def tearDown(self):
        os.environ["PATH"] = self.original_path

    def test_accepts_the_executable_itself(self):
        self.assertEqual(find_ffmpeg(str(self.exe)), str(self.exe))

    def test_accepts_the_containing_folder(self):
        found = find_ffmpeg(str(self.folder))
        self.assertIsNotNone(found)
        self.assertTrue(pathlib.Path(found).is_file())

    def test_rejects_a_wrong_path_instead_of_falling_back(self):
        """Caminho errado deve avisar, não mascarar com a busca automática."""
        self.assertIsNone(find_ffmpeg(str(self.folder / "nao-existe")))

    def test_message_explains_a_bad_configured_path(self):
        message = describe_missing_ffmpeg("C:/errado/ffmpeg.exe")
        self.assertIn("C:/errado/ffmpeg.exe", message)

    def test_message_without_configuration_mentions_the_field(self):
        self.assertIn("ffmpeg", describe_missing_ffmpeg().lower())


class ProbeTest(unittest.TestCase):
    def test_runs_ok_rejects_a_non_executable(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "nada.txt"
        path.write_text("nao sou um programa", encoding="utf-8")
        self.assertFalse(runs_ok(str(path)))

    def test_runs_ok_checks_the_expected_output(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        exe = make_fake_ffmpeg(folder)
        self.assertTrue(runs_ok(str(exe), "ffmpeg version"))
        self.assertFalse(runs_ok(str(exe), "deno"))

    def test_app_dir_is_searched_when_frozen(self):
        folder = pathlib.Path(tempfile.mkdtemp())
        make_fake_ffmpeg(folder)
        original_frozen = getattr(sys, "frozen", None)
        original_executable = sys.executable
        original_path = os.environ.get("PATH", "")
        sys.frozen = True
        sys.executable = str(folder / "PenDriveMaker")
        os.environ["PATH"] = ""
        try:
            self.assertIsNotNone(find_ffmpeg())
        finally:
            sys.executable = original_executable
            os.environ["PATH"] = original_path
            if original_frozen is None:
                del sys.frozen
            else:
                sys.frozen = original_frozen

    def test_find_executable_returns_none_when_absent(self):
        original = os.environ.get("PATH", "")
        os.environ["PATH"] = str(pathlib.Path(tempfile.mkdtemp()))
        try:
            self.assertIsNone(find_executable("programa-que-nao-existe-xyz"))
        finally:
            os.environ["PATH"] = original


class EngineIntegrationTest(unittest.TestCase):
    def test_engine_passes_ffmpeg_location_to_ytdlp(self):
        """Achar o ffmpeg não basta: o yt-dlp precisa receber o caminho."""
        from pen_drive_maker.config import AppConfig
        from pen_drive_maker.downloader import DownloadEngine, EngineCallbacks
        from pen_drive_maker.store import QueueStore

        folder = pathlib.Path(tempfile.mkdtemp()) / "bin"
        exe = make_fake_ffmpeg(folder)
        out = tempfile.mkdtemp()
        cfg = AppConfig(output_dir=out, ffmpeg_path=str(exe), playlist_url="https://x/y")
        cfg.normalize()
        store = QueueStore(cfg.db_path())
        try:
            engine = DownloadEngine(cfg, store, EngineCallbacks())
            self.assertNotIn("ffmpeg_location", engine._base_opts())
            engine._verify_environment()
            self.assertEqual(engine._base_opts().get("ffmpeg_location"), str(exe))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
