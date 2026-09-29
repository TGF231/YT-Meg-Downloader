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
    ERROR_PERMANENT,
    ERROR_TRANSIENT,
    DownloadEngine,
    EngineCallbacks,
    classify_error,
)
from pen_drive_maker.organizer import (  # noqa: E402
    build_relative_path,
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


class EngineFlowTest(unittest.TestCase):
    def setUp(self):
        self._real_ydl = downloader.YoutubeDL
        downloader.YoutubeDL = FakeYDL
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
        )
        self.cfg.normalize()
        self.store = QueueStore(self.cfg.db_path())

    def tearDown(self):
        downloader.YoutubeDL = self._real_ydl
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
        self.assertIn("Rock/Artista/Album/01 - Musica 1.m4a", files)
        self.assertIn("Sem Genero/Artista/Album/02 - Musica 2.m4a", files)

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
