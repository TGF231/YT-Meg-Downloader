"""Download e conversão em paralelo: enquanto uma faixa converte, a próxima baixa."""

import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pen_drive_maker.downloader import DownloadEngine, EngineCallbacks  # noqa: E402
from pen_drive_maker.store import STATUS_DONE, STATUS_FAILED  # noqa: E402
from test_engine import FakeYDL  # noqa: E402
from test_library import URL_A, BibliotecaBase  # noqa: E402


def _esperar(condicao, limite=10.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        if condicao():
            return True
        time.sleep(0.01)
    return False


class PipelineTest(BibliotecaBase):
    def setUp(self):
        super().setUp()
        FakeYDL.eventos = []
        FakeYDL.porta_pos = None
        FakeYDL.falhas_pos = {}

    def tearDown(self):
        if FakeYDL.porta_pos is not None:
            FakeYDL.porta_pos.set()  # nunca deixa uma thread presa depois do teste
        FakeYDL.porta_pos = None
        FakeYDL.falhas_pos = {}
        super().tearDown()

    def _motor(self):
        return DownloadEngine(
            self._cfg(self.raiz, URL_A), self.store,
            EngineCallbacks(log=lambda n, m: self.logs.append((n, m))),
        )

    def _em_thread(self, motor):
        t = threading.Thread(target=motor.run, daemon=True)
        t.start()
        return t

    def test_next_download_starts_while_the_previous_one_converts(self):
        FakeYDL.porta_pos = threading.Event()  # a conversão fica presa
        motor = self._motor()
        t = self._em_thread(motor)
        baixou = _esperar(lambda: ("download", "id02") in FakeYDL.eventos)
        pos_antes = [e for e in FakeYDL.eventos if e[0] == "pos"]
        FakeYDL.porta_pos.set()
        t.join(10)
        self.assertTrue(baixou, "a segunda faixa deveria baixar com a primeira ainda convertendo")
        self.assertEqual(pos_antes, [], "nenhuma conversão terminou antes")
        self.assertEqual(self.store.stats().done, 3)

    def test_queue_order_and_totals_are_kept(self):
        self._baixar()
        itens = self.store.list_items(STATUS_DONE)
        self.assertEqual([i.video_id for i in itens], ["id01", "id02", "id03"])
        for item in itens:
            self.assertTrue(item.relpath.endswith(".m4a"), item.relpath)
            self.assertTrue((self.raiz / item.relpath).is_file())

    def test_no_raw_audio_or_staging_leftovers(self):
        self._baixar()
        sobras = [p for p in self.raiz.rglob("*") if p.is_file() and p.suffix == ".webm"]
        self.assertEqual(sobras, [])
        staging = self.raiz / ".pen-drive-maker" / "staging"
        self.assertEqual([p for p in staging.rglob("*") if p.is_file()] if staging.exists() else [], [])

    def test_stopping_still_finishes_what_was_already_downloaded(self):
        FakeYDL.porta_pos = threading.Event()
        motor = self._motor()
        t = self._em_thread(motor)
        self.assertTrue(_esperar(lambda: ("download", "id02") in FakeYDL.eventos))
        motor.stop()
        FakeYDL.porta_pos.set()
        t.join(10)
        feitos = {i.video_id for i in self.store.list_items(STATUS_DONE)}
        baixados = {e[1] for e in FakeYDL.eventos if e[0] == "download"}
        self.assertTrue({"id01", "id02"} <= feitos, f"baixadas e perdidas: {baixados - feitos}")
        self.assertEqual(feitos, baixados, "toda faixa baixada precisa terminar")

    def test_a_conversion_failure_retries_the_track_without_touching_the_others(self):
        FakeYDL.falhas_pos = {"id02": [RuntimeError("ffmpeg quebrou")]}
        self._baixar()
        self.assertEqual(self.store.stats().done, 3)
        self.assertTrue(any("Falha ao converter" in m and "ffmpeg quebrou" in m
                            for _n, m in self.logs))

    def test_a_track_that_never_converts_ends_as_failed(self):
        FakeYDL.falhas_pos = {"id02": [RuntimeError("ruim")] * 10}
        self._baixar()
        self.assertEqual(self.store.get_item("id02").status, STATUS_FAILED)
        self.assertEqual(self.store.stats().done, 2)

    def test_a_download_failure_does_not_stop_conversions_in_flight(self):
        FakeYDL.failures = {"id02": [RuntimeError("Connection timed out")] * 10}
        self._baixar()
        self.assertEqual(self.store.get_item("id01").status, STATUS_DONE)
        self.assertEqual(self.store.get_item("id03").status, STATUS_DONE)

    def test_a_foreign_file_at_the_destination_is_never_overwritten(self):
        alvo = self.raiz / "Viagem" / "Artista 1" / "Album" / "01 - Musica 1.m4a"
        alvo.parent.mkdir(parents=True)
        alvo.write_bytes(b"meu arquivo")
        self._baixar()
        self.assertEqual(alvo.read_bytes(), b"meu arquivo")
        item = self.store.get_item("id01")
        self.assertIn("[id01]", item.relpath)
        self.assertTrue((self.raiz / item.relpath).is_file())

    def test_downloading_again_replaces_the_tracks_own_file(self):
        self._baixar()
        antigo = self.store.get_item("id01").relpath
        self.store.requeue(["id01"], forget_location=False)
        self._baixar()
        self.assertEqual(self.store.get_item("id01").relpath, antigo)
        pasta = (self.raiz / antigo).parent
        self.assertEqual(
            [p.name for p in pasta.glob("*Musica 1*")], [pathlib.Path(antigo).name],
            "baixar de novo não deve deixar uma segunda cópia",
        )


class NextPendingTest(BibliotecaBase):
    def test_exclude_skips_tracks_already_in_flight(self):
        self._baixar()
        self.store.requeue(["id01", "id02"], forget_location=False)
        self.assertEqual(self.store.next_pending().video_id, "id01")
        self.assertEqual(self.store.next_pending(exclude=["id01"]).video_id, "id02")
        self.assertIsNone(self.store.next_pending(exclude=["id01", "id02"]))


def _ffmpeg():
    import os
    import shutil

    for candidato in (shutil.which("ffmpeg"), os.environ.get("PDM_TEST_FFMPEG")):
        if candidato and os.path.isfile(candidato):
            return candidato
    return None


@unittest.skipIf(_ffmpeg() is None, "ffmpeg indisponível")
class ConversaoDeVerdadeTest(unittest.TestCase):
    """A etapa local com yt-dlp e ffmpeg reais: o que o motor faz depois de baixar."""

    def test_raw_audio_becomes_a_tagged_file_with_cover_in_the_right_folder(self):
        import subprocess
        import tempfile

        from pen_drive_maker import library
        from pen_drive_maker.config import AppConfig
        from pen_drive_maker.downloader import _Job
        from pen_drive_maker.store import QueueStore
        import mutagen

        ff = _ffmpeg()
        raiz = pathlib.Path(tempfile.mkdtemp())
        cfg = AppConfig(output_dir=str(raiz), min_interval=0.0, jitter=0.0,
                        genre_source="playlist", audio_format="mp3", embed_thumbnail=True)
        cfg.normalize()
        store = QueueStore(cfg.db_path())
        try:
            vid = "pIWaVJPl0-c"
            store.add_items([{"video_id": vid, "url": f"https://youtu.be/{vid}", "title": "Faded"}])
            item = store.get_item(vid)
            motor = DownloadEngine(cfg, store, EngineCallbacks())
            motor._ffmpeg_path = ff
            staging = motor._staging_dir(vid)
            staging.mkdir(parents=True)
            cru = staging / "audio.webm"
            capa = staging / "audio.jpg"
            subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i",
                            "sine=frequency=440:duration=1", "-c:a", "libopus", str(cru)],
                           check=True)
            subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i",
                            "color=c=red:s=64x64", "-frames:v", "1", str(capa)], check=True)
            info = {"id": vid, "title": "Faded", "artist": "Alan Walker", "album": "Different World",
                    "track_number": 3, "ext": "webm", "extractor_key": "Youtube",
                    "webpage_url": f"https://www.youtube.com/watch?v={vid}"}
            result = dict(info, filepath=str(cru), _filename=str(cru),
                          requested_downloads=[{"filepath": str(cru)}],
                          thumbnails=[{"id": "0", "url": "x", "filepath": str(capa)}])
            job = _Job(item, info, result, str(cru), staging, 1, 1, time.monotonic())

            caminho, tags = motor._finish(job)
        finally:
            store.close()

        final = pathlib.Path(caminho)
        self.assertEqual(final.suffix, ".mp3")
        self.assertEqual(final.relative_to(raiz.resolve()).parts[1:3],
                         ("Alan Walker", "Different World"))
        self.assertFalse(staging.exists(), "o áudio cru e a capa não ficam para trás")
        self.assertEqual(library.read_video_id(final), vid)
        lidas = library.read_tags(final)
        self.assertEqual((lidas["title"], lidas["artist"], lidas["album"], lidas["track"]),
                         ("Faded", "Alan Walker", "Different World", 3))
        self.assertTrue(any(k.startswith("APIC") for k in mutagen.File(str(final)).tags),
                        "a capa deveria estar embutida")


if __name__ == "__main__":
    unittest.main()
