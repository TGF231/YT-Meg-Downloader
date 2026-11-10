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
from test_library import URL_A, BibliotecaBase, _faixa  # noqa: E402


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
        staging = pathlib.Path(self.stg) / "staging"
        self.assertEqual([p for p in staging.rglob("*") if p.is_file()] if staging.exists() else [], [])

    def test_staging_is_outside_the_library_so_the_pen_drive_is_written_once(self):
        motor = self._motor()
        pasta = motor._staging_dir("abc")
        self.assertNotIn(self.raiz.resolve(), pasta.resolve().parents)
        self.assertIn(pathlib.Path(self.stg).resolve(), pasta.resolve().parents)

    def test_staging_falls_back_to_the_library_when_the_temp_dir_is_unusable(self):
        arquivo = pathlib.Path(self.stg) / "nao_e_pasta"
        arquivo.write_text("x")
        cfg = self._cfg(self.raiz, URL_A)
        cfg.staging_dir = str(arquivo)
        motor = DownloadEngine(cfg, self.store, EngineCallbacks())
        self.assertIn(self.raiz.resolve(), motor._staging_dir("abc").resolve().parents)

    def test_old_staging_leftovers_are_cleaned_but_recent_ones_are_kept(self):
        import os

        motor = self._motor()
        antiga, recente = motor._staging_dir("velha"), motor._staging_dir("nova")
        for pasta in (antiga, recente):
            pasta.mkdir(parents=True)
            (pasta / "audio.webm").write_bytes(b"x")
        velho = time.time() - 10 * 86400
        os.utime(antiga, (velho, velho))
        motor._limpar_staging_antigo()
        self.assertFalse(antiga.exists())
        self.assertTrue(recente.exists(), "pode ser uma conversão em andamento ou retomável")

    def test_the_log_says_where_the_conversion_time_went(self):
        self._baixar()
        linhas = [m for _n, m in self.logs if m.startswith("Conversão:")]
        self.assertEqual(len(linhas), 3)
        self.assertTrue(all("gravar no destino" in m for m in linhas), linhas)

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


class ParalelismoTest(BibliotecaBase):
    """Mais de um download e mais de uma conversão ao mesmo tempo."""

    def setUp(self):
        super().setUp()
        FakeYDL.eventos = []
        FakeYDL.porta_pos = None
        FakeYDL.porta_download = None
        FakeYDL.falhas_pos = {}
        FakeYDL.em_andamento = {"download": 0, "pos": 0}
        FakeYDL.pico = {"download": 0, "pos": 0}
        FakeYDL.tracks = {f"id{n:02d}": _faixa(n) for n in range(1, 9)}
        FakeYDL.playlists = {URL_A: ("Viagem", [f"id{n:02d}" for n in range(1, 9)])}

    def tearDown(self):
        for porta in (FakeYDL.porta_pos, FakeYDL.porta_download):
            if porta is not None:
                porta.set()
        FakeYDL.porta_pos = FakeYDL.porta_download = None
        super().tearDown()

    def _motor(self, downloads, conversoes):
        cfg = self._cfg(self.raiz, URL_A, max_downloads=downloads, max_conversions=conversoes)
        return DownloadEngine(cfg, self.store, EngineCallbacks(
            log=lambda n, m: self.logs.append((n, m)),
            file_progress=lambda p, t: self.progresso.append(t)))

    def _rodar(self, motor):
        self.progresso = []
        t = threading.Thread(target=motor.run, daemon=True)
        t.start()
        return t

    def test_several_downloads_run_at_the_same_time(self):
        FakeYDL.porta_download = threading.Event()
        motor = self._motor(3, 1)
        t = self._rodar(motor)
        juntos = _esperar(lambda: FakeYDL.pico["download"] == 3)
        FakeYDL.porta_download.set()
        t.join(15)
        self.assertTrue(juntos, f"pico de downloads: {FakeYDL.pico['download']}")
        self.assertEqual(self.store.stats().done, 8)

    def test_a_single_slot_never_overlaps_downloads(self):
        FakeYDL.porta_download = threading.Event()
        FakeYDL.porta_download.set()
        t = self._rodar(self._motor(1, 1))
        t.join(15)
        self.assertEqual(FakeYDL.pico["download"], 1)

    def test_several_conversions_run_at_the_same_time(self):
        FakeYDL.porta_pos = threading.Event()
        motor = self._motor(4, 3)
        t = self._rodar(motor)
        juntas = _esperar(lambda: FakeYDL.pico["pos"] == 3)
        FakeYDL.porta_pos.set()
        t.join(15)
        self.assertTrue(juntas, f"pico de conversões: {FakeYDL.pico['pos']}")
        self.assertEqual(self.store.stats().done, 8)

    def test_a_download_does_not_wait_for_a_slow_conversion(self):
        """O caso do relato: a conversão da faixa 1 demora, e as outras seguem baixando."""
        FakeYDL.porta_pos = threading.Event()
        motor = self._motor(2, 1)
        t = self._rodar(motor)
        baixou_varias = _esperar(
            lambda: len([e for e in FakeYDL.eventos if e[0] == "download"]) >= 4)
        convertidas = [e for e in FakeYDL.eventos if e[0] == "pos"]
        FakeYDL.porta_pos.set()
        t.join(15)
        self.assertTrue(baixou_varias, "os downloads travaram esperando a conversão")
        self.assertEqual(convertidas, [])

    def test_every_track_is_downloaded_exactly_once(self):
        motor = self._motor(4, 4)
        self._rodar(motor).join(15)
        baixados = [e[1] for e in FakeYDL.eventos if e[0] == "download"]
        self.assertEqual(sorted(baixados), [f"id{n:02d}" for n in range(1, 9)])
        self.assertEqual(self.store.stats().done, 8)
        itens = self.store.list_items(STATUS_DONE)
        self.assertEqual(len({i.relpath for i in itens}), 8, "dois arquivos no mesmo caminho")

    def test_same_name_tracks_converted_together_never_overwrite_each_other(self):
        FakeYDL.tracks = {
            "id01": {"id": "id01", "title": "Repetida", "artist": "A", "album": "X", "track_number": 1},
            "id02": {"id": "id02", "title": "Repetida", "artist": "A", "album": "X", "track_number": 1},
            "id03": {"id": "id03", "title": "Repetida", "artist": "A", "album": "X", "track_number": 1},
        }
        FakeYDL.playlists = {URL_A: ("Viagem", ["id01", "id02", "id03"])}
        self._rodar(self._motor(3, 3)).join(15)
        arquivos = [p for p in self.raiz.rglob("*.m4a")]
        self.assertEqual(len(arquivos), 3, "uma faixa foi sobrescrita")

    def test_the_bar_reports_downloads_and_conversions(self):
        FakeYDL.porta_pos = threading.Event()
        motor = self._motor(2, 2)
        t = self._rodar(motor)
        _esperar(lambda: any("convertendo" in x for x in self.progresso))
        FakeYDL.porta_pos.set()
        t.join(15)
        self.assertTrue(any("convertendo" in x for x in self.progresso), self.progresso[-5:])

    def test_a_fatal_error_in_one_slot_stops_all_of_them(self):
        from pen_drive_maker.downloader import DownloadEngine as Motor
        from yt_dlp.utils import DownloadError

        FakeYDL.failures = {f"id{n:02d}": [DownloadError("ERROR: HTTP Error 429: Too Many Requests")] * 20
                            for n in range(1, 9)}
        cfg = self._cfg(self.raiz, URL_A, max_downloads=3, max_conversions=1, max_block_events=1)
        razoes = []
        motor = Motor(cfg, self.store, EngineCallbacks(
            log=lambda n, m: self.logs.append((n, m)), finished=razoes.append))
        motor._wait_unblocked = lambda: None  # sem esperar o backoff de verdade
        t = threading.Thread(target=motor.run, daemon=True)
        t.start()
        t.join(15)
        self.assertFalse(t.is_alive(), "o motor não encerrou")
        self.assertTrue(razoes and "bloqueios repetidos" in razoes[0], razoes)

    def test_a_block_found_by_one_slot_pauses_the_others(self):
        from yt_dlp.utils import DownloadError

        FakeYDL.failures = {"id01": [DownloadError("ERROR: HTTP Error 429: Too Many Requests")]}
        motor = self._motor(3, 1)
        esperas = []
        real = motor._wait_unblocked

        def observa():
            esperas.append(motor._block_until)
            real()

        motor.cfg.block_base_backoff = 5.0
        motor._wait_unblocked = observa
        motor._sleep = lambda segundos, rotulo: setattr(motor, "_block_until", 0.0)
        self._rodar(motor).join(15)
        self.assertTrue(any(b > 0 for b in esperas), "as outras vagas deveriam ver o bloqueio")
        self.assertEqual(self.store.stats().done, 8)


class NextPendingTest(BibliotecaBase):
    def test_exclude_skips_tracks_already_in_flight(self):
        self._baixar()
        self.store.requeue(["id01", "id02"], forget_location=False)
        self.assertEqual(self.store.next_pending().video_id, "id01")
        self.assertEqual(self.store.next_pending(exclude=["id01"]).video_id, "id02")
        self.assertIsNone(self.store.next_pending(exclude=["id01", "id02"]))


def _dimensoes_jpeg(dados: bytes):
    """(largura, altura) lidas do cabeçalho SOF do JPEG; sem depender do Pillow."""
    i = 2
    while i + 9 < len(dados):
        if dados[i] != 0xFF:
            i += 1
            continue
        marcador = dados[i + 1]
        if marcador in (0xC0, 0xC1, 0xC2):
            return (dados[i + 7] << 8) | dados[i + 8], (dados[i + 5] << 8) | dados[i + 6]
        i += 2 + ((dados[i + 2] << 8) | dados[i + 3])
    return None


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
            # 16:9 como a miniatura do YouTube: a arte quadrada fica no meio
            subprocess.run([ff, "-loglevel", "error", "-f", "lavfi", "-i",
                            "testsrc2=s=1280x720", "-frames:v", "1", str(capa)], check=True)
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
        capas = [v for k, v in mutagen.File(str(final)).tags.items() if k.startswith("APIC")]
        self.assertTrue(capas, "a capa deveria estar embutida")
        imagem = capas[0].data
        self.assertEqual(imagem[:2], b"\xff\xd8", "a capa deveria ser JPEG, não PNG")
        self.assertEqual(_dimensoes_jpeg(imagem), (500, 500), "quadrado de 500 px")
        self.assertLess(len(imagem), 150_000)


if __name__ == "__main__":
    unittest.main()
