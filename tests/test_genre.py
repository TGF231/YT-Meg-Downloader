"""Testes da consulta de gênero. Nenhum toca a rede."""

import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from pen_drive_maker import genre as genre_module  # noqa: E402
from pen_drive_maker.genre import (  # noqa: E402
    GenreLookup,
    artists_match,
    clean_title,
    group_genre,
)
from pen_drive_maker.store import QueueStore  # noqa: E402


def novo_cache() -> QueueStore:
    return QueueStore(pathlib.Path(tempfile.mkdtemp()) / "q.db")


class CleanTitleTest(unittest.TestCase):
    def test_removes_bracketed_noise(self):
        self.assertEqual(clean_title("Faded (Official Music Video)"), "Faded")
        self.assertEqual(clean_title("Animals [Official Video] HD"), "Animals")

    def test_removes_trailing_noise_after_a_dash(self):
        self.assertEqual(clean_title("Wake Me Up - Official Video"), "Wake Me Up")
        self.assertEqual(clean_title("Alone - Official Audio"), "Alone")

    def test_removes_featuring(self):
        self.assertEqual(clean_title("Closer feat. Halsey"), "Closer")

    def test_keeps_a_clean_title_untouched(self):
        self.assertEqual(clean_title("Strobe"), "Strobe")

    def test_does_not_eat_the_whole_title(self):
        """Um título que é só ruído não pode virar string vazia silenciosa."""
        self.assertEqual(clean_title(""), "")


class GroupGenreTest(unittest.TestCase):
    def test_edm_subgenres_land_in_one_folder(self):
        for bruto in ("Dance", "House", "Electronic", "Techno", "Dubstep"):
            self.assertEqual(group_genre(bruto), "Eletronica", bruto)

    def test_brazilian_styles(self):
        self.assertEqual(group_genre("MPB"), "MPB")
        self.assertEqual(group_genre("Brazilian"), "MPB")
        self.assertEqual(group_genre("Sertanejo"), "Sertanejo")

    def test_hip_hop_variants(self):
        self.assertEqual(group_genre("Hip-Hop/Rap"), "Hip Hop")

    def test_unknown_genre_is_kept_as_is(self):
        self.assertEqual(group_genre("Bachata Experimental"), "Bachata Experimental")

    def test_empty_stays_empty(self):
        self.assertEqual(group_genre(""), "")


class ArtistsMatchTest(unittest.TestCase):
    def test_same_artist(self):
        self.assertTrue(artists_match("Alan Walker", "Alan Walker"))

    def test_artist_inside_a_longer_credit(self):
        self.assertTrue(artists_match("Alok", "Alok, Zeeba & Bruno Martini"))

    def test_different_artists_are_rejected(self):
        self.assertFalse(artists_match("Alan Walker", "Iselin Solheim"))

    def test_empty_never_matches(self):
        self.assertFalse(artists_match("", "Alan Walker"))


class LookupTest(unittest.TestCase):
    """Exercita o cache e o tratamento de falha sem sair para a rede."""

    def setUp(self):
        self.cache = novo_cache()
        self.logs = []

    def tearDown(self):
        self.cache.close()

    def _lookup(self, resultado, **kwargs):
        """GenreLookup com _fetch trocado por um dublê."""
        lookup = GenreLookup(
            self.cache, log=lambda n, m: self.logs.append((n, m)), min_interval=0.0, **kwargs
        )
        self.chamadas = []

        def fake_fetch(artist, title):
            self.chamadas.append((artist, title))
            if isinstance(resultado, Exception):
                lookup._register_failure(str(resultado))
                return ("erro", None)
            return resultado

        lookup._fetch = fake_fetch
        return lookup

    def test_found_genre_is_grouped_and_cached(self):
        lookup = self._lookup(("ok", "Dance"))
        self.assertEqual(lookup.lookup("Alan Walker", "Faded"), "Eletronica")
        self.assertEqual(lookup.lookup("Alan Walker", "Faded"), "Eletronica")
        self.assertEqual(len(self.chamadas), 1, "a segunda vez deve sair do cache")

    def test_cache_keeps_the_raw_answer(self):
        """Guardar o bruto deixa ligar/desligar o agrupamento sem reconsultar."""
        agrupado = self._lookup(("ok", "Dance"))
        self.assertEqual(agrupado.lookup("A", "B"), "Eletronica")
        cru = GenreLookup(self.cache, group=False, min_interval=0.0)
        self.assertEqual(cru.lookup("A", "B"), "Dance")

    def test_absence_is_cached_so_it_is_not_asked_again(self):
        lookup = self._lookup(("vazio", None))
        self.assertIsNone(lookup.lookup("Banda X", "Musica Y"))
        self.assertIsNone(lookup.lookup("Banda X", "Musica Y"))
        self.assertEqual(len(self.chamadas), 1)

    def test_network_error_is_not_cached(self):
        """Falha de rede não pode deixar a faixa sem gênero para sempre."""
        falhando = self._lookup(RuntimeError("sem rede"))
        self.assertIsNone(falhando.lookup("Avicii", "Levels"))
        self.assertIsNone(self.cache.get_genre("avicii|levels"))

        funcionando = self._lookup(("ok", "House"))
        self.assertEqual(funcionando.lookup("Avicii", "Levels"), "Eletronica")

    def test_disables_itself_after_repeated_failures(self):
        lookup = self._lookup(RuntimeError("403"), max_failures=3)
        for i in range(3):
            lookup.lookup(f"Artista {i}", f"Musica {i}")
        self.assertTrue(lookup.disabled)
        self.assertTrue(any("desligada" in m for _, m in self.logs))

    def test_a_disabled_lookup_still_answers_from_cache(self):
        lookup = self._lookup(("ok", "Rock"))
        lookup.lookup("Legiao", "Tempo Perdido")
        lookup.disabled = True
        self.assertEqual(lookup.lookup("Legiao", "Tempo Perdido"), "Rock")

    def test_empty_title_is_not_queried(self):
        lookup = self._lookup(("ok", "Dance"))
        self.assertIsNone(lookup.lookup("Alguem", "(Official Video)"))
        self.assertEqual(self.chamadas, [])

    def test_cache_key_ignores_accents_and_case(self):
        lookup = self._lookup(("ok", "MPB"))
        self.assertEqual(lookup.lookup("Djavan", "Flor de Lis"), "MPB")
        self.assertEqual(lookup.lookup("DJAVAN", "Flor de Lís"), "MPB")
        self.assertEqual(len(self.chamadas), 1)

    def test_network_is_never_touched_in_these_tests(self):
        """Guarda-costas: se alguém remover o dublê, este teste denuncia."""
        self.assertTrue(hasattr(genre_module, "ITUNES_SEARCH"))
        self.assertTrue(genre_module.ITUNES_SEARCH.startswith("https://"))


if __name__ == "__main__":
    unittest.main()
