"""Reconhecimento de arquivos soltos. Nenhum teste toca a rede."""

import pathlib
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pen_drive_maker import identify as ident  # noqa: E402
from pen_drive_maker import library  # noqa: E402
from pen_drive_maker.store import STATUS_DONE, QueueStore  # noqa: E402


def _resultado(vid, titulo, canal="Canal", duracao=None):
    return {"id": vid, "title": titulo, "channel": canal, "duration": duracao,
            "url": f"https://www.youtube.com/watch?v={vid}"}


class PalpiteTest(unittest.TestCase):
    def _palpite(self, nome):
        return ident.palpite_do_arquivo(pathlib.Path("/inexistente") / nome)

    def test_name_with_dash_gives_artist_and_title(self):
        p = self._palpite("Daft Punk - One More Time.mp3")
        self.assertEqual((p.artista, p.titulo), ("Daft Punk", "One More Time"))

    def test_track_number_and_noise_are_dropped(self):
        p = self._palpite("07. Daft Punk - One More Time (Official Video).mp3")
        self.assertEqual((p.artista, p.titulo), ("Daft Punk", "One More Time"))

    def test_name_without_dash_is_only_a_title(self):
        p = self._palpite("musica_sem_nada.mp3")
        self.assertEqual((p.artista, p.titulo), ("", "musica sem nada"))


class PontuacaoTest(unittest.TestCase):
    def setUp(self):
        self.p = ident.Palpite("Daft Punk", "One More Time", 320.0)

    def test_exact_result_scores_high(self):
        c = _resultado("a", "Daft Punk - One More Time (Official Video)", "Daft Punk", 321)
        self.assertGreaterEqual(ident.pontuar(self.p, c), ident.LIMIAR_AUTO)

    def test_other_song_scores_low(self):
        c = _resultado("b", "Daft Punk - Get Lucky", "Daft Punk", 369)
        self.assertLess(ident.pontuar(self.p, c), ident.LIMIAR_DUVIDA)

    def test_a_much_longer_version_is_not_automatic(self):
        c = _resultado("c", "Daft Punk - One More Time", "Daft Punk", 900)
        self.assertLess(ident.pontuar(self.p, c), ident.LIMIAR_AUTO)

    def test_accents_and_case_do_not_matter(self):
        p = ident.Palpite("Beyoncé", "Halo")
        self.assertGreaterEqual(ident.pontuar(p, _resultado("d", "BEYONCE - HALO")), 0.95)


class IdentificarTest(unittest.TestCase):
    def setUp(self):
        self.raiz = pathlib.Path(tempfile.mkdtemp())
        self.store = QueueStore(self.raiz / ".pen-drive-maker" / "queue.db")
        self._real = library.read_tags
        library.read_tags = lambda caminho: {}

    def tearDown(self):
        library.read_tags = self._real
        self.store.close()

    def _solto(self, nome, pasta=""):
        caminho = self.raiz / pasta / nome
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(b"x")
        return (pathlib.Path(pasta) / nome).as_posix()

    def _rodar(self, relpaths, respostas):
        return ident.identify(self.store, self.raiz, relpaths,
                              busca=lambda consulta: respostas.get(consulta, []), pausa=0)

    def test_a_good_match_is_linked_pinned_where_it_is(self):
        rel = self._solto("Daft Punk - One More Time.mp3")
        r = self._rodar([rel], {"Daft Punk One More Time": [
            _resultado("abc", "Daft Punk - One More Time", "Daft Punk")]})
        self.assertEqual(len(r.linked), 1)
        item = self.store.get_item("abc")
        self.assertEqual(item.status, STATUS_DONE)
        self.assertEqual(item.relpath, rel)
        self.assertTrue(item.pinned)
        self.assertEqual(item.url, "https://www.youtube.com/watch?v=abc")
        self.assertTrue((self.raiz / rel).exists(), "o arquivo não pode ser tocado")

    def test_a_doubtful_match_is_not_linked_by_itself(self):
        rel = self._solto("Daft Punk - One More Time.mp3")
        r = self._rodar([rel], {"Daft Punk One More Time": [
            _resultado("abc", "One More Time (cover)", "Fulano")]})
        self.assertEqual((len(r.linked), len(r.doubtful)), (0, 1))
        self.assertIsNone(self.store.get_item("abc"))

    def test_a_confirmed_doubt_can_be_linked(self):
        rel = self._solto("Daft Punk - One More Time.mp3")
        r = self._rodar([rel], {"Daft Punk One More Time": [
            _resultado("abc", "One More Time (cover)", "Fulano")]})
        ident.vincular(self.store, self.raiz, r.doubtful[0])
        self.assertEqual(self.store.get_item("abc").relpath, rel)

    def test_no_result_is_reported_not_linked(self):
        rel = self._solto("Banda Ninguem - Faixa Inexistente.mp3")
        r = self._rodar([rel], {})
        self.assertEqual(r.not_found, [rel])
        self.assertEqual(self.store.stats().done, 0)

    def test_a_song_already_in_the_library_is_not_linked_twice(self):
        self.store.adopt("abc", "https://x", "Rock/a.mp3", title="One More Time")
        rel = self._solto("Daft Punk - One More Time.mp3")
        r = self._rodar([rel], {"Daft Punk One More Time": [
            _resultado("abc", "Daft Punk - One More Time", "Daft Punk")]})
        self.assertEqual((len(r.linked), len(r.duplicates)), (0, 1))
        self.assertEqual(self.store.get_item("abc").relpath, "Rock/a.mp3")

    def test_repeated_search_failures_stop_the_run(self):
        rels = [self._solto(f"A - B{i}.mp3") for i in range(6)]
        chamadas = []

        def quebrada(consulta):
            chamadas.append(consulta)
            raise RuntimeError("HTTP 429")

        r = ident.identify(self.store, self.raiz, rels, busca=quebrada, pausa=0)
        self.assertTrue(r.interrupted)
        self.assertEqual(len(chamadas), 3, "insistir num bloqueio só piora")

    def test_stop_event_interrupts(self):
        rels = [self._solto(f"A - B{i}.mp3") for i in range(4)]
        parar = threading.Event()
        parar.set()
        r = ident.identify(self.store, self.raiz, rels, busca=lambda c: [], stop=parar, pausa=0)
        self.assertTrue(r.interrupted)

    def test_find_loose_skips_files_the_catalog_already_owns(self):
        livre = self._solto("Qualquer - Coisa.mp3")
        ja = self._solto("Outra - Musica.mp3", "Rock")
        self.store.adopt("zzz", "https://x", ja, title="Musica")
        real = library.read_video_id
        library.read_video_id = lambda caminho: None
        try:
            self.assertEqual(ident.find_loose(self.store, self.raiz), [livre])
        finally:
            library.read_video_id = real

    def test_verify_does_not_list_manually_linked_files_as_unknown(self):
        ja = self._solto("Outra - Musica.mp3", "Rock")
        self.store.adopt("zzz", "https://x", ja, title="Musica")
        real = library.read_video_id
        library.read_video_id = lambda caminho: None
        try:
            relatorio = library.reconcile(self.store, self.raiz, full=True)
        finally:
            library.read_video_id = real
        self.assertEqual(relatorio.unknown, [])
        self.assertEqual(self.store.get_item("zzz").status, STATUS_DONE)


if __name__ == "__main__":
    unittest.main()
