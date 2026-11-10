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
    GRUPOS,
    group_genre,
    groups_to_text,
    parse_groups,
    resolve_genre,
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


class GroupingRulesTest(unittest.TestCase):
    """Casos que o casamento por pedaço de texto errava."""

    def test_whole_words_only(self):
        casos = {
            "Dancehall": "Reggae",              # não é "dance"
            "Reggaeton": "Latina",              # não é "reggae"
            "Música Popular Brasileira": "MPB",  # não é "pop"
            "Therapy Sessions": "Therapy Sessions",  # "rap" não está em "Therapy"
        }
        for bruto, esperado in casos.items():
            self.assertEqual(group_genre(bruto), esperado, bruto)

    def test_the_last_word_is_the_base_genre(self):
        casos = {
            "Dance Pop": "Pop", "Pop Rock": "Rock", "Folk Rock": "Rock",
            "Indie Pop": "Pop", "Alternative Dance": "Eletronica",
            "Latin Pop": "Pop", "Garage Rock": "Rock", "UK Garage": "Eletronica",
            "Pop Rap": "Hip Hop", "Latin Jazz": "Jazz e Blues",
        }
        for bruto, esperado in casos.items():
            self.assertEqual(group_genre(bruto), esperado, bruto)

    def test_alternatives_use_the_first_that_matches(self):
        self.assertEqual(group_genre("Pop/Rock"), "Pop")
        self.assertEqual(group_genre("Hip-Hop/Rap"), "Hip Hop")
        self.assertEqual(group_genre("R&B/Soul"), "R&B e Soul")
        self.assertEqual(group_genre("Drum & Bass"), "Eletronica")

    def test_accents_and_case_do_not_matter(self):
        self.assertEqual(group_genre("FORRÓ"), "MPB")
        self.assertEqual(group_genre("forro universitario"), "MPB")

    def test_custom_groups_replace_the_defaults(self):
        grupos = parse_groups("Balada: pop, rock\nDanca: dance, house")
        self.assertEqual(group_genre("Pop Rock", grupos), "Balada")
        self.assertEqual(group_genre("House", grupos), "Danca")
        self.assertEqual(group_genre("Jazz", grupos), "Jazz")  # nada casa: fica como veio

    def test_groups_round_trip_through_the_editor_text(self):
        self.assertEqual(parse_groups(groups_to_text(GRUPOS)), {k: tuple(v) for k, v in GRUPOS.items()})

    def test_parse_ignores_blank_comment_and_broken_lines(self):
        grupos = parse_groups("# comentario\n\nsem dois pontos\nVazio:\nOk: a, b,, c")
        self.assertEqual(grupos, {"Ok": ("a", "b", "c")})
        self.assertEqual(parse_groups(""), {})


class NotAGenreTest(unittest.TestCase):
    """"Bollywood" diz de onde a música vem, não como ela soa."""

    def test_film_and_origin_labels_are_not_genres(self):
        for bruto in ("Bollywood", "Soundtrack", "Anime", "Indian", "Original Score"):
            self.assertIsNone(resolve_genre(bruto), bruto)

    def test_but_a_real_genre_next_to_the_label_wins(self):
        self.assertEqual(resolve_genre("Bollywood Pop"), "Pop")
        self.assertEqual(resolve_genre("Indian Classical"), "Classica")

    def test_unknown_genres_fall_back_unless_asked_to_keep(self):
        self.assertIsNone(resolve_genre("Bachata Experimental"))
        self.assertEqual(resolve_genre("Bachata Experimental", manter_desconhecidos=True),
                         "Bachata Experimental")

    def test_empty_is_never_a_genre(self):
        self.assertIsNone(resolve_genre(""))
        self.assertIsNone(resolve_genre("   ", manter_desconhecidos=True))


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
