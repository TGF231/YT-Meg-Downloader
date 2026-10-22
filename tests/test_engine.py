"""Testes offline do motor: o yt-dlp é substituído por um duplo, sem rede."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from yt_dlp.utils import DownloadError  # noqa: E402

import pen_drive_maker.downloader as downloader  # noqa: E402
from pen_drive_maker.config import AppConfig  # noqa: E402
from pen_drive_maker.downloader import (  # noqa: E402
    ERROR_BLOCK,
    ERROR_JS_RUNTIME,
    EnvironmentProblem,
    ERROR_PERMANENT,
    ERROR_TRANSIENT,
    CookieError,
    DownloadEngine,
    EngineCallbacks,
    check_cookies,
    classify_error,
    describe_cookie_failure,
    describe_missing_js_runtime,
    find_js_runtime,
)
from pen_drive_maker.organizer import (  # noqa: E402
    build_relative_path,
    preview_path,
    resolve_tags,
    sanitize_component,
)
from pen_drive_maker.store import QueueStore  # noqa: E402


class FakeYDL:
    """Duplo do yt_dlp.YoutubeDL, controlado por atributos de classe."""

    calls = {"playlist": 0, "probe": 0, "download": 0}
    tracks: dict = {}
    failures: dict = {}

    def __init__(self, params):
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @staticmethod
    def sanitize_info(info, remove_private_keys=False):
        return dict(info)

    def extract_info(self, url, download=False):
        if self.params.get("extract_flat"):
            FakeYDL.calls["playlist"] += 1
            return {
                "title": "Playlist de Teste",
                "entries": [
                    {"id": t["id"], "title": t["title"], "url": f"https://youtu.be/{t['id']}"}
                    for t in FakeYDL.tracks.values()
                ],
            }
        video_id = url.rsplit("/", 1)[-1]
        FakeYDL.calls["probe"] += 1
        planned = FakeYDL.failures.get(video_id)
        if planned:
            raise planned.pop(0)
        return dict(FakeYDL.tracks[video_id])

    def process_ie_result(self, info, download=True):
        FakeYDL.calls["download"] += 1
        template = self.params["outtmpl"]["default"]
        path = pathlib.Path((template % {"ext": "m4a"}).replace("%%", "%"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"audio")
        result = dict(info)
        result["requested_downloads"] = [{"filepath": str(path)}]
        return result


class CookieErrorTypeTest(unittest.TestCase):
    def test_cookie_error_is_its_own_type(self):
        self.assertTrue(issubclass(CookieError, Exception))


class ClassifyErrorTest(unittest.TestCase):
    def test_block_signals(self):
        for message in (
            "ERROR: HTTP Error 429: Too Many Requests",
            "ERROR: HTTP Error 403: Forbidden",
            "Sign in to confirm you're not a bot",
            "Failed to extract any player response",
        ):
            self.assertEqual(classify_error(message), ERROR_BLOCK, message)

    def test_permanent_signals(self):
        for message in ("Private video", "Video unavailable", "This video is members-only"):
            self.assertEqual(classify_error(message), ERROR_PERMANENT, message)

    def test_transient_default(self):
        self.assertEqual(classify_error("Connection timed out"), ERROR_TRANSIENT)


class OrganizerTest(unittest.TestCase):
    def test_fat32_safe_names(self):
        self.assertEqual(sanitize_component('a<b>c:d"e/f\\g|h?i*j'), "a b c d e f g h i j")
        self.assertEqual(sanitize_component("CON"), "_CON")
        self.assertEqual(sanitize_component("nome com ponto final."), "nome com ponto final")
        self.assertEqual(sanitize_component(""), "Desconhecido")

    def test_keeps_band_names_with_separators(self):
        tags = resolve_tags({"artist": "Earth, Wind & Fire", "title": "September"}, "X", "Y")
        self.assertEqual(tags.artist, "Earth, Wind & Fire")

    def test_drops_featuring_and_topic_suffix(self):
        tags = resolve_tags({"uploader": "Djavan - Topic", "title": "Sina"}, "X", "Y")
        self.assertEqual(tags.artist, "Djavan")
        tags = resolve_tags({"artist": "Ana feat. Beto", "title": "Z"}, "X", "Y")
        self.assertEqual(tags.artist, "Ana")

    def test_defaults_and_path(self):
        tags = resolve_tags(
            {"title": "Título", "categories": ["Music"], "artist": "Beto", "track_number": 4},
            "Sem Genero",
            "Singles",
        )
        self.assertEqual(
            build_relative_path(tags), "Sem Genero/Beto/Singles/04 - Titulo"
        )


class JsRuntimeTest(unittest.TestCase):
    def test_challenge_errors_are_classified(self):
        for message in (
            "ERROR: [youtube] abc: The page needs to be reloaded.",
            "Signature solving failed: Some formats may be missing",
            "n challenge solving failed: Some formats may be missing",
        ):
            self.assertEqual(classify_error(message), ERROR_JS_RUNTIME, message)

    def test_block_still_wins_over_nothing_else(self):
        self.assertEqual(classify_error("HTTP Error 429"), ERROR_BLOCK)

    def test_environment_problem_is_its_own_type(self):
        self.assertTrue(issubclass(EnvironmentProblem, Exception))

    def test_message_names_the_runtimes_and_how_to_install(self):
        message = describe_missing_js_runtime()
        for token in ("deno", "bun", "node", "qjs"):
            self.assertIn(token, message)
        self.assertIn("Deno", message)

    def test_finds_runtime_next_to_a_frozen_executable(self):
        """Empacotado, o yt-dlp aceita o deno na pasta do próprio programa."""
        folder = pathlib.Path(tempfile.mkdtemp())
        fake = folder / "deno"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        original_frozen = getattr(sys, "frozen", None)
        original_executable = sys.executable
        sys.frozen = True
        sys.executable = str(folder / "PenDriveMaker")
        try:
            self.assertEqual(find_js_runtime(), str(fake))
        finally:
            sys.executable = original_executable
            if original_frozen is None:
                del sys.frozen
            else:
                sys.frozen = original_frozen

    def test_engine_aborts_when_no_runtime_is_available(self):
        tmp = tempfile.mkdtemp()
        cfg = AppConfig(
            playlist_url="https://music.youtube.com/playlist?list=TEST", output_dir=tmp
        )
        cfg.normalize()
        logs, reasons = [], []
        store = QueueStore(cfg.db_path())
        real_ydl, real_find = downloader.YoutubeDL, downloader.find_js_runtime
        downloader.YoutubeDL = FakeYDL
        downloader.find_js_runtime = lambda: None
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        try:
            DownloadEngine(
                cfg,
                store,
                EngineCallbacks(
                    log=lambda level, msg: logs.append((level, msg)),
                    finished=lambda reason: reasons.append(reason),
                ),
            ).run()
        finally:
            downloader.YoutubeDL, downloader.find_js_runtime = real_ydl, real_find
            store.close()
        self.assertEqual(FakeYDL.calls["playlist"], 0, "não deve extrair sem runtime")
        self.assertTrue(any("runtime JavaScript" in m for _, m in logs))
        self.assertFalse(any("Erro inesperado" in m for _, m in logs))

    def test_challenge_failure_mid_queue_aborts_instead_of_retrying(self):
        """Sem runtime, repetir a faixa é desperdício: a fila para na hora."""
        tmp = tempfile.mkdtemp()
        cfg = AppConfig(
            playlist_url="https://music.youtube.com/playlist?list=TEST",
            output_dir=tmp,
            min_interval=0.0,
            jitter=0.0,
            max_retries=3,
        )
        cfg.normalize()
        store = QueueStore(cfg.db_path())
        real_ydl, real_find = downloader.YoutubeDL, downloader.find_js_runtime
        downloader.YoutubeDL = FakeYDL
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        FakeYDL.tracks = {"id01": {"id": "id01", "title": "M1", "artist": "A"}}
        FakeYDL.failures = {
            "id01": [DownloadError("ERROR: [youtube] id01: The page needs to be reloaded.")] * 5
        }
        # Presente na extração, ausente quando a faixa falha.
        seen = {"n": 0}

        def flaky_find():
            seen["n"] += 1
            return "/usr/bin/deno" if seen["n"] == 1 else None

        downloader.find_js_runtime = flaky_find
        try:
            DownloadEngine(cfg, store, EngineCallbacks()).run()
        finally:
            downloader.YoutubeDL, downloader.find_js_runtime = real_ydl, real_find
            store.close()
        self.assertEqual(FakeYDL.calls["probe"], 1, "não deve retentar sem runtime")


class CookieTest(unittest.TestCase):
    def test_no_cookies_is_fine(self):
        self.assertIsNone(check_cookies(AppConfig(cookies_mode="none")))

    def test_missing_cookie_file_is_reported(self):
        cfg = AppConfig(cookies_mode="file", cookies_file="/caminho/que/nao/existe.txt")
        problem = check_cookies(cfg)
        self.assertIsNotNone(problem)
        self.assertIn("arquivo de cookies", problem)

    def test_malformed_cookie_file_is_reported(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "cookies.txt"
        path.write_text("isto nao e um cookies.txt\n", encoding="utf-8")
        cfg = AppConfig(cookies_mode="file", cookies_file=str(path))
        self.assertIsNotNone(check_cookies(cfg))

    def test_valid_cookie_file_loads(self):
        path = pathlib.Path(tempfile.mkdtemp()) / "cookies.txt"
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".youtube.com\tTRUE\t/\tTRUE\t2000000000\tSID\tvalor\n",
            encoding="utf-8",
        )
        cfg = AppConfig(cookies_mode="file", cookies_file=str(path))
        self.assertIsNone(check_cookies(cfg))

    def test_chrome_message_mentions_closing_the_browser(self):
        cfg = AppConfig(cookies_mode="browser", cookies_browser="chrome")
        message = describe_cookie_failure(cfg, ["Could not copy Chrome cookie database"])
        self.assertIn("Google Chrome", message)
        self.assertIn("chrome://settings/system", message)
        self.assertIn("cookies.txt", message)
        self.assertIn("Could not copy Chrome cookie database", message)

    def test_edge_message_is_edge_specific(self):
        cfg = AppConfig(cookies_mode="browser", cookies_browser="edge")
        message = describe_cookie_failure(cfg, ["Could not copy Chrome cookie database"])
        self.assertIn("Microsoft Edge", message)
        self.assertIn("edge://settings/system", message)
        self.assertIn("msedge.exe", message)
        self.assertNotIn("chrome://", message)

    def test_chrome_wording_is_explained_for_other_chromium_browsers(self):
        """O yt-dlp escreve "Chrome" para qualquer navegador Chromium."""
        detail = "Could not copy Chrome cookie database. See ... for more info"
        for browser, label in (("edge", "Microsoft Edge"), ("brave", "Brave")):
            message = describe_cookie_failure(
                AppConfig(cookies_mode="browser", cookies_browser=browser), [detail]
            )
            self.assertIn("qualquer navegador Chromium", message)
            self.assertIn(f"consultado foi o {label}", message)

    def test_chrome_note_is_absent_when_chrome_was_selected(self):
        message = describe_cookie_failure(
            AppConfig(cookies_mode="browser", cookies_browser="chrome"),
            ["Could not copy Chrome cookie database"],
        )
        self.assertNotIn("qualquer navegador Chromium", message)

    def test_non_chromium_browser_gets_generic_guidance(self):
        message = describe_cookie_failure(
            AppConfig(cookies_mode="browser", cookies_browser="firefox"), ["perfil ausente"]
        )
        self.assertIn("Firefox", message)
        self.assertNotIn("Chromium (Chrome, Edge", message)

    def test_engine_stops_cleanly_on_cookie_failure(self):
        """Falha de cookie encerra com orientação, sem tocar na playlist."""
        tmp = tempfile.mkdtemp()
        cfg = AppConfig(
            playlist_url="https://music.youtube.com/playlist?list=TEST",
            output_dir=tmp,
            cookies_mode="file",
            cookies_file="/caminho/que/nao/existe.txt",
        )
        cfg.normalize()
        logs = []
        reasons = []
        store = QueueStore(cfg.db_path())
        real = downloader.YoutubeDL
        real_find = downloader.find_js_runtime
        downloader.YoutubeDL = FakeYDL
        downloader.find_js_runtime = lambda: "/fake/deno"
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        try:
            DownloadEngine(
                cfg,
                store,
                EngineCallbacks(
                    log=lambda level, msg: logs.append((level, msg)),
                    finished=lambda reason: reasons.append(reason),
                ),
            ).run()
        finally:
            downloader.YoutubeDL = real
            downloader.find_js_runtime = real_find
            store.close()
        self.assertEqual(FakeYDL.calls["playlist"], 0, "não deve extrair a playlist")
        self.assertTrue(any("arquivo de cookies" in m for _, m in logs))
        self.assertFalse(any("Erro inesperado" in m for _, m in logs))
        self.assertTrue(reasons and "cookies" in reasons[0].lower())


class FakeLookup:
    """Dublê de GenreLookup: nunca sai para a rede."""

    resposta = None
    consultas = []

    def __init__(self, cache, group=True, log=None, **kwargs):
        self.disabled = False

    def lookup(self, artist, title):
        FakeLookup.consultas.append((artist, title))
        return FakeLookup.resposta


class PreviewPathTest(unittest.TestCase):
    """O exemplo mostrado na interface tem de casar com o que é criado."""

    def test_windows_never_mixes_separators(self):
        import ntpath

        for root in (
            r"C:\Users\User\PenDrive",
            "C:/Users/User/PenDrive",     # o QFileDialog do Qt devolve assim
            "C:/Users/User/PenDrive/",    # com barra sobrando
        ):
            example = preview_path(root, path_module=ntpath)
            self.assertNotIn("/", example, f"separador misturado em {example}")
            esperado = ntpath.normpath("C:/Users/User/PenDrive") + ntpath.sep
            self.assertTrue(example.startswith(esperado), example)

    def test_posix_uses_forward_slashes(self):
        import posixpath

        example = preview_path("/media/usuario/PENDRIVE", path_module=posixpath)
        self.assertEqual(
            example, "/media/usuario/PENDRIVE/Genero/Artista/Album/01 - Titulo.ext"
        )

    def test_ascii_option_is_reflected(self):
        import posixpath

        com_acento = preview_path("/x", ascii_only=False, path_module=posixpath)
        sem_acento = preview_path("/x", ascii_only=True, path_module=posixpath)
        self.assertIn("Gênero", com_acento)
        self.assertNotIn("Gênero", sem_acento)
        self.assertIn("Genero", sem_acento)

    def test_preview_shows_each_folder_structure(self):
        """O que sai da pasta tem de aparecer no nome do arquivo."""
        import posixpath

        esperado = {
            "genre_artist_album": "/x/EDM/Artista/Album/01 - Titulo.ext",
            "genre_artist": "/x/EDM/Artista/Titulo.ext",
            "genre": "/x/EDM/Artista - Titulo.ext",
        }
        for estrutura, caminho in esperado.items():
            self.assertEqual(
                preview_path("/x", genre="EDM", structure=estrutura, path_module=posixpath),
                caminho,
                estrutura,
            )

    def test_genre_placeholder_is_configurable(self):
        import posixpath

        example = preview_path("/x", genre="EDM", path_module=posixpath)
        self.assertEqual(example, "/x/EDM/Artista/Album/01 - Titulo.ext")

    def test_genre_placeholder_is_sanitized_like_the_rest(self):
        import posixpath

        example = preview_path("/x", genre="Eletrônica/Dance", path_module=posixpath)
        self.assertIn("Eletronica Dance", example)

    def test_extension_follows_the_chosen_format(self):
        import posixpath

        self.assertTrue(
            preview_path("/x", extension="mp3", path_module=posixpath).endswith(".mp3")
        )

    def test_empty_root_has_a_placeholder(self):
        import posixpath

        self.assertTrue(preview_path("  ", path_module=posixpath).startswith("<saída>"))

    def test_preview_matches_build_relative_path(self):
        """O exemplo e o caminho real usam o mesmo saneamento."""
        import posixpath

        tags = resolve_tags(
            {"genre": "Gênero", "artist": "Artista", "album": "Álbum", "title": "Título",
             "track_number": 1},
            "X",
            "Y",
        )
        real = build_relative_path(tags)
        example = preview_path("/raiz", extension="mp3", path_module=posixpath)
        self.assertTrue(example.endswith(f"{real}.mp3"), f"{example} != {real}")


class EngineFlowTest(unittest.TestCase):
    def setUp(self):
        self._real_ydl = downloader.YoutubeDL
        downloader.YoutubeDL = FakeYDL
        # Os testes não podem depender do que está instalado na máquina: sem
        # isso a suíte passa onde há um runtime e falha no CI, onde não há.
        self._real_find_runtime = downloader.find_js_runtime
        self._real_find_ffmpeg = downloader.find_ffmpeg
        downloader.find_js_runtime = lambda: "/fake/deno"
        downloader.find_ffmpeg = lambda configured="": "/fake/ffmpeg"
        # Nenhum teste pode tocar a rede: a consulta de gênero é dublada e os
        # testes que a exercitam trocam FakeLookup.resposta.
        self._real_lookup = downloader.GenreLookup
        downloader.GenreLookup = FakeLookup
        FakeLookup.resposta = None
        FakeLookup.consultas = []
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        FakeYDL.tracks = {
            f"id{i:02d}": {
                "id": f"id{i:02d}",
                "title": f"Musica {i}",
                "artist": "Artista",
                "album": "Album",
                "genre": "Rock" if i % 2 else None,
                "track_number": i,
            }
            for i in range(1, 6)
        }
        FakeYDL.failures = {}
        self.tmp = tempfile.mkdtemp()
        self.logs = []
        self.cfg = AppConfig(
            playlist_url="https://music.youtube.com/playlist?list=TEST",
            output_dir=self.tmp,
            min_interval=0.0,
            jitter=0.0,
            block_base_backoff=5.0,
            max_block_events=3,
            max_retries=2,
            max_consecutive_failures=3,
            embed_thumbnail=False,
            genre_source="playlist",
        )
        self.cfg.normalize()
        self.store = QueueStore(self.cfg.db_path())

    def tearDown(self):
        downloader.YoutubeDL = self._real_ydl
        downloader.find_js_runtime = self._real_find_runtime
        downloader.find_ffmpeg = self._real_find_ffmpeg
        downloader.GenreLookup = self._real_lookup
        self.store.close()

    def _run(self, force=False):
        callbacks = EngineCallbacks(log=lambda level, msg: self.logs.append((level, msg)))
        DownloadEngine(self.cfg, self.store, callbacks).run(force_reextract=force)

    def test_happy_path_builds_folder_tree(self):
        self._run()
        stats = self.store.stats()
        self.assertEqual((stats.total, stats.done, stats.failed), (5, 5, 0))
        self.assertEqual(FakeYDL.calls["playlist"], 1, "a playlist deve ser extraída uma só vez")
        self.assertEqual(FakeYDL.calls["probe"], 5, "uma extração de metadados por faixa")
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        # Por padrão a pasta de gênero é o nome da playlist: o YouTube Music
        # quase nunca informa gênero, e quem monta a playlist já classificou.
        self.assertIn("Playlist de Teste/Artista/Album/01 - Musica 1.m4a", files)
        self.assertIn("Playlist de Teste/Artista/Album/02 - Musica 2.m4a", files)

    def test_online_mode_uses_the_looked_up_genre(self):
        self.cfg.genre_source = "online"
        FakeLookup.resposta = "Eletronica"
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertTrue(all(f.startswith("Eletronica/") for f in files), files)
        self.assertEqual(len(FakeLookup.consultas), 5, "uma consulta por faixa")

    def test_online_mode_falls_back_when_nothing_is_found(self):
        """Sem resposta: metadados primeiro, nome da playlist depois."""
        self.cfg.genre_source = "online"
        FakeLookup.resposta = None
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        # As faixas ímpares do dublê têm genre="Rock"; as pares não têm nada.
        self.assertIn("Rock/Artista/Album/01 - Musica 1.m4a", files)
        self.assertIn("Playlist de Teste/Artista/Album/02 - Musica 2.m4a", files)
        # O que nunca pode acontecer é faixa sem pasta de gênero.
        self.assertTrue(all(f.count("/") == 3 for f in files), files)

    def test_online_mode_queries_with_cleaned_artist_and_title(self):
        self.cfg.genre_source = "online"
        FakeLookup.resposta = "Rock"
        self._run()
        artistas = {a for a, _ in FakeLookup.consultas}
        self.assertEqual(artistas, {"Artista"}, FakeLookup.consultas)

    def test_genre_artist_structure(self):
        self.cfg.folder_structure = "genre_artist"
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertIn("Playlist de Teste/Artista/Musica 1.m4a", files)
        self.assertTrue(all(f.count("/") == 2 for f in files), files)

    def test_genre_only_structure_keeps_the_artist_in_the_name(self):
        self.cfg.folder_structure = "genre"
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertIn("Playlist de Teste/Artista - Musica 1.m4a", files)
        self.assertTrue(all(f.count("/") == 1 for f in files), files)

    def test_levels_that_do_not_exist_are_not_recorded(self):
        """A árvore da interface não pode mostrar pasta que não está no disco."""
        self.cfg.folder_structure = "genre"
        self._run()
        for _genero, artista, album, _n in self.store.done_tree():
            self.assertEqual((artista, album), ("", ""))

    def test_duplicate_names_do_not_overwrite_each_other(self):
        """Com menos pastas, dois títulos iguais cairiam no mesmo arquivo."""
        self.cfg.folder_structure = "genre_artist"
        FakeYDL.tracks = {
            "id01": {"id": "id01", "title": "Repetida", "artist": "Artista",
                     "album": "Primeiro", "track_number": 1},
            "id02": {"id": "id02", "title": "Repetida", "artist": "Artista",
                     "album": "Segundo", "track_number": 1},
        }
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertEqual(len(files), 2, f"uma faixa foi perdida: {files}")
        self.assertEqual(self.store.stats().done, 2)

    def test_metadata_mode_prefers_the_track_genre(self):
        self.cfg.genre_source = "metadata"
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        # A faixa 1 tem genre="Rock"; a 2 não tem e cai no nome da playlist.
        self.assertIn("Rock/Artista/Album/01 - Musica 1.m4a", files)
        self.assertIn("Playlist de Teste/Artista/Album/02 - Musica 2.m4a", files)

    def test_fixed_mode_ignores_both(self):
        self.cfg.genre_source = "fixed"
        self.cfg.default_genre = "Minhas Musicas"
        self._run()
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertTrue(all(f.startswith("Minhas Musicas/") for f in files), files)

    def test_playlist_genre_survives_a_resumed_run(self):
        """Ao retomar não há extração: o nome tem de vir do banco."""
        self._run()
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        pathlib.Path(self.tmp, "Playlist de Teste").rename(
            pathlib.Path(self.tmp, "movido")
        )
        self.store.requeue_failed()
        for item in self.store.list_items():
            self.store.register_failure(item.video_id, "forcando", max_retries=99)
        self._run()
        self.assertEqual(FakeYDL.calls["playlist"], 0, "não deveria reextrair")
        files = sorted(p.relative_to(self.tmp).as_posix() for p in pathlib.Path(self.tmp).rglob("*.m4a"))
        self.assertTrue(
            any(f.startswith("Playlist de Teste/") for f in files),
            f"gênero perdido ao retomar: {files}",
        )

    def test_resume_does_not_repeat_or_reextract(self):
        self._run()
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        self._run()
        self.assertEqual(FakeYDL.calls, {"playlist": 0, "probe": 0, "download": 0})

    def test_block_triggers_backoff_and_queue_continues(self):
        self.cfg.block_base_backoff = 5.0  # o _sleep é interrompível; valor baixo p/ teste
        FakeYDL.failures["id03"] = [DownloadError("ERROR: HTTP Error 429: Too Many Requests")]
        self._run()
        self.assertEqual(self.store.stats().done, 5)
        self.assertTrue(any("Bloqueio detectado" in m for _, m in self.logs))
        # O bloqueio não consome tentativa da faixa.
        item = next(i for i in self.store.list_items() if i.video_id == "id03")
        self.assertEqual(item.attempts, 0)

    def test_permanent_error_is_not_retried(self):
        FakeYDL.failures["id02"] = [DownloadError("ERROR: Private video")] * 5
        self._run()
        stats = self.store.stats()
        self.assertEqual((stats.done, stats.failed), (4, 1))
        self.assertEqual(FakeYDL.calls["probe"], 5, "faixa permanente não deve ser retentada")

    def test_transient_error_is_retried_then_requeued(self):
        FakeYDL.failures["id02"] = [DownloadError("ERROR: Connection timed out")] * 2
        self._run()
        self.assertEqual(self.store.stats().failed, 1)
        self.assertEqual(self.store.requeue_failed(), 1)
        self._run()
        self.assertEqual(self.store.stats().done, 5)

    def test_outtmpl_escapes_percent_in_names(self):
        engine = DownloadEngine(self.cfg, self.store, EngineCallbacks())
        tags = resolve_tags({"artist": "100% Banda", "title": "T", "genre": "Pop"}, "X", "Y")
        self.assertIn("100%% Banda", engine._outtmpl_for(tags))


if __name__ == "__main__":
    unittest.main()
