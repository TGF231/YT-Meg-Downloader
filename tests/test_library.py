"""Gerenciamento de biblioteca. Nenhum teste toca a rede.

Os arquivos de áudio são dublês: o FakeYDL grava "PDMID:<id>" no lugar da tag
de origem, e o leitor de tags é trocado por um que lê esse marcador. Um teste
à parte confere a leitura de tags de verdade, com ffmpeg e mutagen.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pen_drive_maker.downloader as downloader  # noqa: E402
from pen_drive_maker import library  # noqa: E402
from pen_drive_maker.config import AppConfig  # noqa: E402
from pen_drive_maker.downloader import DownloadEngine, EngineCallbacks  # noqa: E402
from pen_drive_maker.store import STATUS_DONE, STATUS_EXCLUDED, QueueStore  # noqa: E402
from test_engine import FakeLookup, FakeYDL, ler_id_do_dublê  # noqa: E402

URL_A = "https://music.youtube.com/playlist?list=A"
URL_B = "https://music.youtube.com/playlist?list=B"


def _faixa(n):
    return {"id": f"id{n:02d}", "title": f"Musica {n}", "artist": f"Artista {n % 2}",
            "album": "Album", "track_number": n}


class BibliotecaBase(unittest.TestCase):
    """Motor e leitor de tags dublados; uma biblioteca vazia por teste."""

    def setUp(self):
        self._real = (downloader.YoutubeDL, downloader.find_js_runtime,
                      downloader.find_ffmpeg, downloader.GenreLookup,
                      library.read_video_id, library.read_tags)
        downloader.YoutubeDL = FakeYDL
        downloader.find_js_runtime = lambda: "/fake/deno"
        downloader.find_ffmpeg = lambda configured="": "/fake/ffmpeg"
        downloader.GenreLookup = FakeLookup
        library.read_video_id = ler_id_do_dublê
        library.read_tags = lambda caminho: {}
        FakeLookup.resposta = None
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        FakeYDL.failures = {}
        FakeYDL.tags_gravadas = {}
        FakeYDL.tracks = {f"id{n:02d}": _faixa(n) for n in range(1, 6)}
        FakeYDL.playlists = {
            URL_A: ("Viagem", ["id01", "id02", "id03"]),
            URL_B: ("Academia", ["id02", "id03", "id04", "id05"]),
        }
        self.raiz = pathlib.Path(tempfile.mkdtemp())
        self.cfg = self._cfg(self.raiz)
        self.store = QueueStore(self.cfg.db_path())
        self.logs = []

    def tearDown(self):
        (downloader.YoutubeDL, downloader.find_js_runtime, downloader.find_ffmpeg,
         downloader.GenreLookup, library.read_video_id, library.read_tags) = self._real
        self.store.close()

    def _cfg(self, raiz, url=URL_A, **extra):
        cfg = AppConfig(playlist_url=url, output_dir=str(raiz), min_interval=0.0, jitter=0.0,
                        genre_source="playlist", embed_thumbnail=False, **extra)
        cfg.normalize()
        return cfg

    def _baixar(self, url=URL_A, store=None, cfg=None, **extra):
        cfg = cfg or self._cfg(self.raiz, url, **extra)
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        DownloadEngine(cfg, store or self.store,
                       EngineCallbacks(log=lambda n, m: self.logs.append((n, m)))).run()
        return dict(FakeYDL.calls)

    def _arquivos(self, raiz=None):
        raiz = raiz or self.raiz
        return sorted(p.relative_to(raiz).as_posix() for p in raiz.rglob("*.m4a"))

    def _arquivo_de(self, video_id, store=None):
        item = (store or self.store).get_item(video_id)
        return (store or self.store).location(item)


# O FakeYDL do test_engine devolve todas as faixas; aqui cada URL tem as suas.
_extrair_original = FakeYDL.extract_info


def _extrair_por_url(self, url, download=False):
    if self.params.get("extract_flat") and url in getattr(FakeYDL, "playlists", {}):
        FakeYDL.calls["playlist"] += 1
        titulo, ids = FakeYDL.playlists[url]
        return {"title": titulo, "entries": [
            {"id": i, "title": FakeYDL.tracks[i]["title"], "url": f"https://youtu.be/{i}"}
            for i in ids
        ]}
    return _extrair_original(self, url, download)


FakeYDL.extract_info = _extrair_por_url


class VariasPlaylistsTest(BibliotecaBase):
    def test_second_playlist_does_not_download_what_the_first_brought(self):
        primeira = self._baixar(URL_A)
        segunda = self._baixar(URL_B)
        self.assertEqual(primeira["download"], 3)
        self.assertEqual(segunda["download"], 2, "id02 e id03 já estavam na biblioteca")
        self.assertEqual(self.store.stats().done, 5)
        self.assertTrue(any("já estavam na biblioteca" in m for _, m in self.logs))

    def test_switching_playlist_keeps_the_previous_one(self):
        """Antes, uma URL nova apagava a fila inteira da anterior."""
        self._baixar(URL_A)
        self._baixar(URL_B)
        self.assertEqual({p["title"] for p in self.store.list_playlists()}, {"Viagem", "Academia"})
        for video_id in ("id01", "id02", "id03"):
            self.assertEqual(self.store.get_item(video_id).status, STATUS_DONE)

    def test_a_shared_track_belongs_to_both_playlists(self):
        self._baixar(URL_A)
        self._baixar(URL_B)
        self.assertEqual(self.store.get_item("id02").playlists, "Viagem, Academia")

    def test_genre_from_playlist_uses_the_playlist_that_brought_the_track(self):
        self._baixar(URL_A)
        self._baixar(URL_B)
        self.assertTrue(self._arquivo_de("id01").relative_to(self.raiz).parts[0] == "Viagem")
        self.assertTrue(self._arquivo_de("id05").relative_to(self.raiz).parts[0] == "Academia")

    def test_an_empty_url_completes_the_library(self):
        self._baixar(URL_A)
        self._arquivo_de("id02").unlink()
        calls = self._baixar(url="")
        self.assertEqual(calls["playlist"], 0)
        self.assertEqual(calls["download"], 1)

    def test_url_is_optional_only_when_the_library_has_work(self):
        cfg = AppConfig(playlist_url="", output_dir=str(self.raiz))
        self.assertIn("Informe a URL da playlist.", cfg.validate())
        self.assertNotIn("Informe a URL da playlist.", cfg.validate(library_has_work=True))


class TagsGravadasTest(BibliotecaBase):
    def test_file_carries_its_source_and_the_folder_genre(self):
        self._baixar(URL_A)
        tags = FakeYDL.tags_gravadas["id01"]
        self.assertIn("watch?v=id01", tags["meta_comment"])
        self.assertEqual(tags["meta_genre"], "Viagem", "gênero do arquivo = gênero da pasta")
        self.assertEqual(tags["meta_artist"], "Artista 1")
        self.assertEqual(tags["meta_track"], "1")

    def test_no_tags_when_metadata_is_off(self):
        self._baixar(URL_A, embed_metadata=False)
        self.assertEqual(FakeYDL.tags_gravadas["id01"], {})


class ArquivosMovidosTest(BibliotecaBase):
    def test_a_file_moved_by_hand_is_found_and_not_downloaded_again(self):
        self._baixar(URL_A)
        antigo = self._arquivo_de("id01")
        novo = self.raiz / "Favoritas" / "minha faixa.m4a"
        novo.parent.mkdir()
        shutil.move(antigo, novo)

        calls = self._baixar(URL_A)

        self.assertEqual(calls["download"], 0)
        item = self.store.get_item("id01")
        self.assertEqual(item.relpath, "Favoritas/minha faixa.m4a")
        self.assertTrue(item.pinned, "movida à mão: fica fixada")
        self.assertTrue(any("mudaram de pasta" in m for _, m in self.logs))

    def test_a_pinned_track_that_was_deleted_comes_back_to_the_same_place(self):
        self._baixar(URL_A)
        library.move_to_folder(self.store, self.raiz, ["id01"], "Minhas/Rock")
        self._arquivo_de("id01").unlink()

        calls = self._baixar(URL_A)

        self.assertEqual(calls["download"], 1)
        self.assertEqual(self._arquivo_de("id01").relative_to(self.raiz).parts[:2],
                         ("Minhas", "Rock"))
        self.assertTrue(self._arquivo_de("id01").exists())


class VerificacaoCompletaTest(BibliotecaBase):
    def test_files_put_there_by_hand_are_adopted(self):
        """Montar a biblioteca à mão: o programa aprende onde cada faixa está."""
        pasta = self.raiz / "Minha Estrutura" / "Rock"
        pasta.mkdir(parents=True)
        (pasta / "qualquer nome.m4a").write_text("PDMID:id09")

        relatorio = library.reconcile(self.store, self.raiz, full=True)

        self.assertEqual(relatorio.adopted, ["id09"])
        item = self.store.get_item("id09")
        self.assertEqual(item.status, STATUS_DONE)
        self.assertEqual(item.relpath, "Minha Estrutura/Rock/qualquer nome.m4a")
        self.assertTrue(item.pinned)
        self.assertIn("watch?v=id09", item.url, "a URL de download vem da tag")

    def test_a_pending_track_already_on_disk_is_not_downloaded(self):
        self.store.add_items([{"video_id": "id01", "url": "https://youtu.be/id01", "title": "x"}])
        (self.raiz / "x.m4a").write_text("PDMID:id01")
        library.reconcile(self.store, self.raiz, full=True)
        self.assertEqual(self.store.get_item("id01").status, STATUS_DONE)

    def test_audio_without_a_source_tag_is_reported(self):
        (self.raiz / "de outro lugar.mp3").write_text("sem marcador")
        relatorio = library.reconcile(self.store, self.raiz, full=True)
        self.assertEqual(relatorio.unknown, ["de outro lugar.mp3"])

    def test_the_catalog_folder_is_never_scanned(self):
        estado = self.raiz / ".pen-drive-maker"
        (estado / "lixo.mp3").write_text("PDMID:id99")
        relatorio = library.reconcile(self.store, self.raiz, full=True)
        self.assertNotIn("id99", relatorio.adopted)

    def test_quick_check_does_not_scan_when_nothing_is_missing(self):
        self._baixar(URL_A)
        relatorio = library.reconcile(self.store, self.raiz)
        self.assertFalse(relatorio.scanned, "varrer tudo sem motivo é lento num pen drive")


class MoverReorganizarTest(BibliotecaBase):
    def test_move_to_folder_pins_and_cleans_empty_folders(self):
        self._baixar(URL_A)
        antiga_pasta = self._arquivo_de("id01").parent
        outros = [p for p in antiga_pasta.iterdir() if p != self._arquivo_de("id01")]
        for outro in outros:
            outro.unlink()

        library.move_to_folder(self.store, self.raiz, ["id01"], "Carro/Favoritas")

        destino = self._arquivo_de("id01")
        self.assertEqual(destino.relative_to(self.raiz).parts[:2], ("Carro", "Favoritas"))
        self.assertTrue(destino.exists())
        self.assertTrue(self.store.get_item("id01").pinned)
        self.assertFalse(antiga_pasta.exists(), "pasta vazia deveria ter sido removida")

    def test_reorganize_moves_files_without_downloading(self):
        self._baixar(URL_A)
        antes = self._arquivos()
        relatorio = library.reorganize(self.store, self.raiz, "artist")
        self.assertEqual(relatorio.moved, 3)
        depois = self._arquivos()
        self.assertNotEqual(antes, depois)
        self.assertTrue(all(f.startswith("Artista ") for f in depois), depois)
        self.assertEqual(self._baixar(URL_A)["download"], 0, "nada a rebaixar")

    def test_reorganize_leaves_pinned_tracks_alone(self):
        self._baixar(URL_A)
        library.move_to_folder(self.store, self.raiz, ["id01"], "Fixa")
        relatorio = library.reorganize(self.store, self.raiz, "artist")
        self.assertEqual(relatorio.kept_pinned, 1)
        self.assertEqual(self._arquivo_de("id01").relative_to(self.raiz).parts[0], "Fixa")

    def test_unpinned_tracks_go_back_to_the_structure(self):
        self._baixar(URL_A)
        library.move_to_folder(self.store, self.raiz, ["id01"], "Fixa")
        self.store.set_pinned(["id01"], False)
        library.reorganize(self.store, self.raiz, "genre_artist_album")
        self.assertEqual(self._arquivo_de("id01").relative_to(self.raiz).parts[0], "Viagem")

    def test_reorganize_never_overwrites(self):
        self._baixar(URL_A)
        ocupado = self.raiz / "Artista 1" / "Musica 1.m4a"
        ocupado.parent.mkdir(parents=True, exist_ok=True)
        ocupado.write_text("arquivo de outra pessoa")
        library.reorganize(self.store, self.raiz, "artist")
        self.assertEqual(ocupado.read_text(), "arquivo de outra pessoa")


class RemoverTest(BibliotecaBase):
    def test_removed_tracks_do_not_come_back_with_the_playlist(self):
        self._baixar(URL_A)
        arquivo = self._arquivo_de("id01")
        library.remove_tracks(self.store, self.raiz, ["id01"], apagar_arquivos=True)
        self.assertFalse(arquivo.exists())
        self.assertEqual(self.store.get_item("id01").status, STATUS_EXCLUDED)

        cfg = self._cfg(self.raiz, URL_A)
        DownloadEngine(cfg, self.store, EngineCallbacks()).run()  # sem força: já está
        FakeYDL.calls = {"playlist": 0, "probe": 0, "download": 0}
        DownloadEngine(cfg, self.store, EngineCallbacks()).run(force_reextract=True)
        self.assertEqual(FakeYDL.calls["download"], 0, "a faixa removida voltou")
        self.assertEqual(self.store.get_item("id01").status, STATUS_EXCLUDED)

    def test_remove_can_keep_the_file(self):
        self._baixar(URL_A)
        arquivo = self._arquivo_de("id01")
        library.remove_tracks(self.store, self.raiz, ["id01"], apagar_arquivos=False)
        self.assertTrue(arquivo.exists())


class ReproduzirEmOutroPenDriveTest(BibliotecaBase):
    def _estrutura_manual(self):
        self._baixar(URL_A)
        library.move_to_folder(self.store, self.raiz, ["id01"], "Estrada/Classicos")
        library.move_to_folder(self.store, self.raiz, ["id02"], "Estrada")
        return {i.video_id: i.relpath for i in self.store.list_items(STATUS_DONE)}

    def test_export_then_import_reproduces_the_same_structure_by_downloading(self):
        locais = self._estrutura_manual()
        arquivo = self.raiz / "biblioteca.json"
        self.assertEqual(library.export_library(self.store, arquivo), 3)

        outro = pathlib.Path(tempfile.mkdtemp())
        destino = QueueStore(self._cfg(outro).db_path())
        try:
            novas, existentes = library.import_library(destino, arquivo)
            self.assertEqual((novas, existentes), (3, 0))
            calls = self._baixar(url="", store=destino, cfg=self._cfg(outro, url=""))
            self.assertEqual(calls["download"], 3)
            for video_id, relativo in locais.items():
                self.assertEqual(destino.get_item(video_id).relpath, relativo)
                self.assertTrue((outro / relativo).exists(), relativo)
        finally:
            destino.close()

    def test_copy_copies_present_files_and_queues_the_rest(self):
        locais = self._estrutura_manual()
        self._arquivo_de("id03").unlink()  # este não está aqui: terá de ser baixado
        outro = pathlib.Path(tempfile.mkdtemp())

        relatorio = library.copy_library(self.store, outro)

        self.assertEqual((relatorio.copied, relatorio.queued), (2, 1))
        for video_id in ("id01", "id02"):
            self.assertTrue((outro / locais[video_id]).exists())
        destino = QueueStore(self._cfg(outro).db_path())
        try:
            calls = self._baixar(url="", store=destino, cfg=self._cfg(outro, url=""))
            self.assertEqual(calls["download"], 1, "só o que faltava")
            self.assertEqual(destino.stats().done, 3)
        finally:
            destino.close()

    def test_copy_structure_only_downloads_everything(self):
        self._estrutura_manual()
        outro = pathlib.Path(tempfile.mkdtemp())
        relatorio = library.copy_library(self.store, outro, copiar_arquivos=False)
        self.assertEqual((relatorio.copied, relatorio.queued), (0, 3))

    def test_import_keeps_what_the_library_already_has(self):
        self._baixar(URL_A)
        dados = library.export_data(self.store)
        novas, existentes = library.import_data(self.store, dados)
        self.assertEqual((novas, existentes), (0, 3))
        self.assertEqual(self.store.stats().done, 3)

    def test_import_rejects_a_foreign_file(self):
        arquivo = self.raiz / "outro.json"
        arquivo.write_text(json.dumps({"qualquer": "coisa"}))
        with self.assertRaises(library.ImportError_):
            library.import_library(self.store, arquivo)

    def test_exported_paths_use_forward_slashes(self):
        """O arquivo exportado no Windows tem de valer no Linux, e vice-versa."""
        self._estrutura_manual()
        dados = library.export_data(self.store)
        for faixa in dados["faixas"]:
            self.assertNotIn("\\", faixa["local"])


def _ffmpeg():
    for candidato in (shutil.which("ffmpeg"),
                      os.environ.get("PDM_TEST_FFMPEG")):
        if candidato and os.path.isfile(candidato):
            return candidato
    return None


@unittest.skipIf(_ffmpeg() is None, "ffmpeg indisponível")
class TagsDeVerdadeTest(unittest.TestCase):
    """A ponte entre arquivo e biblioteca, com ffmpeg e mutagen reais."""

    def test_source_id_survives_in_every_format(self):
        pasta = pathlib.Path(tempfile.mkdtemp())
        for extensao in ("mp3", "m4a", "opus"):
            arquivo = pasta / f"faixa.{extensao}"
            subprocess.run(
                [_ffmpeg(), "-loglevel", "error", "-f", "lavfi",
                 "-i", "sine=frequency=440:duration=1",
                 "-metadata", "comment=https://www.youtube.com/watch?v=pIWaVJPl0-c",
                 "-metadata", "artist=Alan Walker", "-metadata", "title=Faded",
                 "-metadata", "genre=Eletronica", "-metadata", "track=3",
                 str(arquivo)],
                check=True,
            )
            with self.subTest(formato=extensao):
                self.assertEqual(library.read_video_id(arquivo), "pIWaVJPl0-c")
                tags = library.read_tags(arquivo)
                self.assertEqual(tags.get("artist"), "Alan Walker")
                self.assertEqual(tags.get("genre"), "Eletronica")

    def test_non_audio_and_untagged_files_return_none(self):
        pasta = pathlib.Path(tempfile.mkdtemp())
        (pasta / "texto.mp3").write_text("não sou áudio")
        self.assertIsNone(library.read_video_id(pasta / "texto.mp3"))


if __name__ == "__main__":
    unittest.main()
