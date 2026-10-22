"""Conferência entre a fila e o disco. Nenhum teste toca a rede."""

import pathlib
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pen_drive_maker.downloader as downloader  # noqa: E402
from pen_drive_maker.config import AppConfig  # noqa: E402
from pen_drive_maker.downloader import DownloadEngine, EngineCallbacks  # noqa: E402
from pen_drive_maker.store import QueueStore, file_present  # noqa: E402

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from test_engine import FakeLookup, FakeYDL  # noqa: E402


def _faixa(store, video_id, caminho, status="done"):
    store.add_items([{"video_id": video_id, "url": f"https://x/{video_id}", "title": video_id}])
    if status == "done":
        store.mark_done(video_id, str(caminho), "EDM", "Artista", "Album", video_id)


class FilePresentTest(unittest.TestCase):
    def setUp(self):
        self.pasta = pathlib.Path(tempfile.mkdtemp())

    def test_existing_file(self):
        arquivo = self.pasta / "a.mp3"
        arquivo.write_bytes(b"x")
        self.assertTrue(file_present(str(arquivo)))

    def test_missing_file(self):
        self.assertFalse(file_present(str(self.pasta / "nao-existe.mp3")))

    def test_missing_folder(self):
        self.assertFalse(file_present(str(self.pasta / "sumiu" / "a.mp3")))

    def test_other_extension_with_the_same_name_counts(self):
        """O caminho gravado pode ser anterior à conversão para mp3."""
        (self.pasta / "musica.mp3").write_bytes(b"x")
        self.assertTrue(file_present(str(self.pasta / "musica.webm")))

    def test_leftovers_do_not_count_as_the_track(self):
        for sobra in ("musica.part", "musica.jpg", "musica.webp", "musica.ytdl"):
            (self.pasta / sobra).write_bytes(b"x")
        self.assertFalse(file_present(str(self.pasta / "musica.mp3")))

    def test_a_different_name_does_not_count(self):
        (self.pasta / "outra.mp3").write_bytes(b"x")
        self.assertFalse(file_present(str(self.pasta / "musica.mp3")))


class VerifyFilesTest(unittest.TestCase):
    def setUp(self):
        self.pasta = pathlib.Path(tempfile.mkdtemp())
        self.store = QueueStore(self.pasta / ".db" / "q.db")

    def tearDown(self):
        self.store.close()

    def _arquivo(self, nome):
        caminho = self.pasta / "EDM" / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(b"x")
        return caminho

    def test_missing_files_go_back_to_the_queue(self):
        presente, sumido = self._arquivo("a.mp3"), self._arquivo("b.mp3")
        _faixa(self.store, "a", presente)
        _faixa(self.store, "b", sumido)
        sumido.unlink()

        voltaram = self.store.verify_files()

        self.assertEqual([i.video_id for i in voltaram], ["b"])
        stats = self.store.stats()
        self.assertEqual((stats.done, stats.pending), (1, 1))

    def test_deleting_the_whole_folder_requeues_everything(self):
        for nome in ("a", "b", "c"):
            _faixa(self.store, nome, self._arquivo(f"{nome}.mp3"))
        shutil.rmtree(self.pasta / "EDM")
        self.assertEqual(len(self.store.verify_files()), 3)
        self.assertEqual(self.store.stats().pending, 3)

    def test_requeued_tracks_forget_their_old_location(self):
        """Senão a árvore da interface continuaria mostrando a pasta apagada."""
        arquivo = self._arquivo("a.mp3")
        _faixa(self.store, "a", arquivo)
        arquivo.unlink()
        self.store.verify_files()
        self.assertEqual(self.store.done_tree(), [])
        item = self.store.list_items()[0]
        self.assertIsNone(item.filepath)
        self.assertEqual(item.attempts, 0)

    def test_nothing_missing_changes_nothing(self):
        _faixa(self.store, "a", self._arquivo("a.mp3"))
        self.assertEqual(self.store.verify_files(), [])
        self.assertEqual(self.store.stats().done, 1)

    def test_pending_and_failed_tracks_are_left_alone(self):
        _faixa(self.store, "p", "", status="pending")
        _faixa(self.store, "f", "", status="pending")
        self.store.register_failure("f", "erro", max_retries=1)
        self.assertEqual(self.store.verify_files(), [])
        self.assertEqual(self.store.stats().failed, 1)

    def test_tracks_without_a_recorded_path_are_kept(self):
        """Sem caminho não há como provar que sumiu; manter é o lado seguro."""
        _faixa(self.store, "a", "")
        self.assertEqual(self.store.verify_files(), [])
        self.assertEqual(self.store.stats().done, 1)

    def test_reset_clears_everything_but_not_the_genre_cache(self):
        _faixa(self.store, "a", self._arquivo("a.mp3"))
        self.store.set_playlist("https://x/y", "EDM")
        self.store.set_genre("chave", "House")
        self.store.clear_all()
        self.assertEqual(self.store.stats().total, 0)
        self.assertIsNone(self.store.get_playlist())
        self.assertEqual(self.store.get_genre("chave"), "House")


class EngineSyncTest(unittest.TestCase):
    def setUp(self):
        self._real = (downloader.YoutubeDL, downloader.find_js_runtime,
                      downloader.find_ffmpeg, downloader.GenreLookup)
        downloader.YoutubeDL = FakeYDL
        downloader.find_js_runtime = lambda: "/fake/deno"
        downloader.find_ffmpeg = lambda configured="": "/fake/ffmpeg"
        downloader.GenreLookup = FakeLookup
        FakeLookup.resposta = None
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        FakeYDL.failures = {}
        FakeYDL.tracks = {
            f"id{i:02d}": {"id": f"id{i:02d}", "title": f"Musica {i}", "artist": "Artista",
                           "album": "Album", "track_number": i}
            for i in range(1, 4)
        }
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.cfg = AppConfig(
            playlist_url="https://music.youtube.com/playlist?list=T",
            output_dir=str(self.tmp), min_interval=0.0, jitter=0.0,
            genre_source="playlist", embed_thumbnail=False,
        )
        self.cfg.normalize()
        self.store = QueueStore(self.cfg.db_path())
        self.logs = []

    def tearDown(self):
        (downloader.YoutubeDL, downloader.find_js_runtime,
         downloader.find_ffmpeg, downloader.GenreLookup) = self._real
        self.store.close()

    def _run(self):
        cb = EngineCallbacks(log=lambda n, m: self.logs.append((n, m)))
        DownloadEngine(self.cfg, self.store, cb).run()

    def _arquivos(self):
        return sorted(p.relative_to(self.tmp).as_posix() for p in self.tmp.rglob("*.m4a"))

    def test_deleted_files_are_downloaded_again_on_the_next_run(self):
        """O caso relatado: apagar as pastas e iniciar de novo."""
        self._run()
        self.assertEqual(len(self._arquivos()), 3)

        shutil.rmtree(self.tmp / "Playlist de Teste")
        self.assertEqual(self._arquivos(), [])
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        self._run()

        self.assertEqual(len(self._arquivos()), 3, "as faixas apagadas deveriam voltar")
        self.assertEqual(FakeYDL.calls["download"], 3)
        self.assertEqual(FakeYDL.calls["playlist"], 0, "não precisa reextrair a playlist")
        self.assertTrue(any("não está mais no disco" in m for _, m in self.logs))

    def test_only_the_deleted_tracks_come_back(self):
        self._run()
        apagado = next(self.tmp.rglob("*Musica 2*"))
        apagado.unlink()
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        self._run()
        self.assertEqual(FakeYDL.calls["download"], 1)
        self.assertEqual(len(self._arquivos()), 3)

    def test_an_unplugged_drive_does_not_requeue_everything(self):
        """Com a unidade ausente todo arquivo pareceria sumido."""
        self._run()
        engine = DownloadEngine(self.cfg, self.store, EngineCallbacks(
            log=lambda n, m: self.logs.append((n, m))))
        voltaram = engine.sync_with_disk(root_existed=False)
        self.assertEqual(voltaram, 0)
        self.assertEqual(self.store.stats().done, 3, "nada deveria voltar à fila")
        self.assertTrue(any("pasta de saída não existia" in m for _, m in self.logs))

    def test_run_itself_skips_the_check_when_the_output_folder_is_missing(self):
        """Passa pelo run() de verdade: é ele quem decide se a pasta existia."""
        banco = QueueStore(pathlib.Path(tempfile.mkdtemp()) / "q.db")
        try:
            banco.set_playlist(self.cfg.playlist_url, "Playlist de Teste")
            banco.add_items([
                {"video_id": f"id{i:02d}", "url": f"https://youtu.be/id{i:02d}",
                 "title": f"Musica {i}"} for i in range(1, 4)
            ])
            for i in range(1, 4):
                # caminhos que não existem: sem a proteção todos voltariam à fila
                banco.mark_done(f"id{i:02d}", f"/unidade/ausente/m{i}.m4a",
                                "EDM", "Artista", "Album", f"M{i}")
            ausente = self.tmp / "unidade-removivel" / "saida"   # não existe ainda
            cfg = AppConfig(
                playlist_url=self.cfg.playlist_url, output_dir=str(ausente),
                min_interval=0.0, jitter=0.0, genre_source="playlist",
                embed_thumbnail=False,
            )
            cfg.normalize()
            DownloadEngine(cfg, banco, EngineCallbacks(
                log=lambda n, m: self.logs.append((n, m)))).run()

            self.assertEqual(banco.stats().done, 3, "a fila inteira voltou por engano")
            self.assertEqual(FakeYDL.calls["download"], 0)
        finally:
            banco.close()

    def test_a_run_with_everything_present_requeues_nothing(self):
        self._run()
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        self._run()
        self.assertEqual(FakeYDL.calls["download"], 0)


if __name__ == "__main__":
    unittest.main()
