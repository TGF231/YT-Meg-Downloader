"""Testes da localização de ferramentas externas, sem depender do sistema."""

import os
import pathlib
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pen_drive_maker.tools import (  # noqa: E402
    app_dirs,
    bundled_dir,
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


class BundledToolsTest(unittest.TestCase):
    """Ferramentas embutidas no executável têm de vencer as do sistema."""

    def setUp(self):
        self.original_meipass = getattr(sys, "_MEIPASS", None)
        self.original_path = os.environ.get("PATH", "")

    def tearDown(self):
        os.environ["PATH"] = self.original_path
        if self.original_meipass is None:
            if hasattr(sys, "_MEIPASS"):
                del sys._MEIPASS
        else:
            sys._MEIPASS = self.original_meipass

    def test_bundled_dir_is_none_when_not_frozen(self):
        if hasattr(sys, "_MEIPASS"):
            del sys._MEIPASS
        self.assertIsNone(bundled_dir())

    def test_bundled_dir_points_at_meipass(self):
        folder = tempfile.mkdtemp()
        sys._MEIPASS = folder
        self.assertEqual(bundled_dir(), pathlib.Path(folder))

    def test_bundled_dir_ignores_a_missing_folder(self):
        sys._MEIPASS = "/pasta/que/nao/existe/xyz"
        self.assertIsNone(bundled_dir())

    def test_bundled_dir_comes_first(self):
        folder = tempfile.mkdtemp()
        sys._MEIPASS = folder
        self.assertEqual(app_dirs()[0], pathlib.Path(folder))

    def test_bundled_ffmpeg_wins_over_the_system_one(self):
        bundle = pathlib.Path(tempfile.mkdtemp())
        system = pathlib.Path(tempfile.mkdtemp())
        make_fake_ffmpeg(bundle)
        make_fake_ffmpeg(system)
        sys._MEIPASS = str(bundle)
        os.environ["PATH"] = str(system)
        found = find_ffmpeg()
        self.assertIsNotNone(found)
        self.assertTrue(
            found.startswith(str(bundle)),
            f"deveria usar o embutido ({bundle}), usou {found}",
        )

    def test_configured_path_still_wins_over_the_bundled_one(self):
        bundle = pathlib.Path(tempfile.mkdtemp())
        chosen = pathlib.Path(tempfile.mkdtemp())
        make_fake_ffmpeg(bundle)
        exe = make_fake_ffmpeg(chosen)
        sys._MEIPASS = str(bundle)
        self.assertEqual(find_ffmpeg(str(exe)), str(exe))


class VersionFlagTest(unittest.TestCase):
    """O deno recusa '-version'; o ffmpeg recusa '--version' em versoes antigas."""

    def _make(self, body: str) -> str:
        folder = pathlib.Path(tempfile.mkdtemp())
        path = folder / "tool"
        path.write_text(body, encoding="utf-8")
        path.chmod(0o755)
        return str(path)

    @unittest.skipIf(IS_WINDOWS, "script de shell")
    def test_accepts_a_tool_that_only_knows_double_dash(self):
        tool = self._make(
            "#!/bin/sh\n"
            'if [ "$1" = "--version" ]; then echo "deno 2.9.7"; exit 0; fi\n'
            'echo "error: unexpected argument"; exit 1\n'
        )
        self.assertTrue(runs_ok(tool))

    @unittest.skipIf(IS_WINDOWS, "script de shell")
    def test_accepts_a_tool_that_only_knows_single_dash(self):
        tool = self._make(
            "#!/bin/sh\n"
            'if [ "$1" = "-version" ]; then echo "ffmpeg version 7.0"; exit 0; fi\n'
            "exit 1\n"
        )
        self.assertTrue(runs_ok(tool, "ffmpeg version"))

    @unittest.skipIf(IS_WINDOWS, "script de shell")
    def test_rejects_a_zero_exit_that_reports_a_bad_argument(self):
        """Alguns programas saem com 0 reclamando do argumento."""
        tool = self._make('#!/bin/sh\necho "error: unexpected argument"; exit 0\n')
        self.assertFalse(runs_ok(tool))


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
        # O runtime JavaScript não é o assunto aqui, e a máquina de teste pode
        # não ter nenhum: fixamos para o teste valer em qualquer ambiente.
        import pen_drive_maker.downloader as downloader

        real_find = downloader.find_js_runtime
        downloader.find_js_runtime = lambda: "/fake/deno"
        try:
            engine = DownloadEngine(cfg, store, EngineCallbacks())
            self.assertNotIn("ffmpeg_location", engine._base_opts())
            engine._verify_environment()
            self.assertEqual(engine._base_opts().get("ffmpeg_location"), str(exe))
        finally:
            downloader.find_js_runtime = real_find
            store.close()


if __name__ == "__main__":
    unittest.main()
