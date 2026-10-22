"""Testes da interface. Rodam sem tela, com a plataforma offscreen do Qt."""

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

try:
    from PySide6.QtWidgets import QApplication
except ImportError:  # pragma: no cover - ambiente sem Qt
    QApplication = None

SEM_QT = QApplication is None


def _app():
    from pen_drive_maker.assinatura import aplicacao

    return aplicacao.criar("Pen Drive Maker teste", [])


@unittest.skipIf(SEM_QT, "PySide6 não instalado")
class TabelaDeAcoesTest(unittest.TestCase):
    """A tabela de ações só vale se cada linha apontar para algo que existe."""

    @classmethod
    def setUpClass(cls):
        cls.app = _app()
        from pen_drive_maker.gui import MainWindow

        cls.MainWindow = MainWindow

    def test_every_action_points_to_an_existing_method(self):
        for acao in self.MainWindow.ACOES:
            self.assertTrue(
                callable(getattr(self.MainWindow, acao.metodo, None)),
                f"{acao.atributo} aponta para o método inexistente {acao.metodo}()",
            )

    def test_every_action_lands_in_a_declared_place(self):
        from pen_drive_maker.assinatura.acoes import BARRA, OCULTA

        lugares = {BARRA, OCULTA} | {chave for chave, _ in self.MainWindow.MENUS}
        for acao in self.MainWindow.ACOES:
            self.assertIn(acao.onde, lugares, f"{acao.atributo} em um lugar inexistente")

    def test_action_attributes_are_unique(self):
        atributos = [a.atributo for a in self.MainWindow.ACOES]
        self.assertEqual(len(atributos), len(set(atributos)))


@unittest.skipIf(SEM_QT, "PySide6 não instalado")
class JanelaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        from pen_drive_maker.gui import MainWindow

        MainWindow.PASTA_DADOS = tempfile.mkdtemp()
        self.w = MainWindow()
        self.w.output_edit.setText(tempfile.mkdtemp())

    def tearDown(self):
        if self.w.store is not None:
            self.w.store.close()
        self.w.deleteLater()

    def test_actions_were_created_on_the_window(self):
        for acao in self.w.ACOES:
            self.assertTrue(hasattr(self.w, acao.atributo), acao.atributo)

    def test_running_state_swaps_which_actions_are_enabled(self):
        self.w._definir_rodando(False)
        self.assertTrue(self.w.act_iniciar.isEnabled())
        self.assertFalse(self.w.act_parar.isEnabled())
        self.w._definir_rodando(True)
        self.assertFalse(self.w.act_iniciar.isEnabled())
        self.assertTrue(self.w.act_parar.isEnabled())
        self.assertFalse(self.w.url_edit.isEnabled(), "não editar a URL rodando")
        self.w._definir_rodando(False)

    def test_only_roles_the_theme_defines(self):
        """Papel inventado não pinta nada e passa despercebido."""
        import re

        from pen_drive_maker.assinatura import tema

        fonte = pathlib.Path("pen_drive_maker/assinatura/tema.py").read_text(encoding="utf-8")
        conhecidos = set(re.findall(r'role="([a-zA-Z]+)"', fonte))
        self.assertTrue(conhecidos)
        for widget in self.w.findChildren(object):
            papel = getattr(widget, "property", None) and widget.property("role")
            if papel:
                self.assertIn(papel, conhecidos, f"papel desconhecido: {papel}")
        self.assertTrue(tema.folha_estilo())

    def test_genre_preview_follows_the_chosen_source(self):
        esperados = {
            "online": "Gênero consultado",
            "playlist": "Nome da playlist",
            "metadata": "Gênero",
            "fixed": self.w.cfg.default_genre,
        }
        for fonte, esperado in esperados.items():
            self.w._selecionar(self.w.genre_source, fonte)
            self.assertEqual(self.w._genero_de_exemplo(), esperado, fonte)

    def test_folder_structure_selector_changes_the_preview(self):
        vistos = {}
        for estrutura in ("genre_artist_album", "genre_artist", "genre"):
            self.w._selecionar(self.w.folder_structure, estrutura)
            vistos[estrutura] = self.w.estrutura.text()
        self.assertEqual(len(set(vistos.values())), 3, vistos)
        self.assertIn("Album", vistos["genre_artist_album"])
        self.assertNotIn("Album", vistos["genre_artist"])
        self.assertIn("Artista - Titulo", vistos["genre"])

    def test_folder_structure_reaches_the_config(self):
        for estrutura in ("genre_artist_album", "genre_artist", "genre"):
            self.w._selecionar(self.w.folder_structure, estrutura)
            self.assertEqual(self.w.config_atual().folder_structure, estrutura)

    def test_log_is_repainted_when_the_theme_changes(self):
        from pen_drive_maker.assinatura import tema

        self.w.registrar("error", "falha de teste")
        antes = tema.atual().bad
        self.w.alternar_tema()
        depois = tema.atual().bad
        self.assertNotEqual(antes, depois, "a paleta deveria ter mudado")
        self.assertIn("falha de teste", self.w.log_view.toPlainText())
        self.w.alternar_tema()

    def test_log_escapes_html(self):
        self.w.registrar("info", "<b>não</b> é marcação & tal")
        self.assertIn("<b>não</b> é marcação & tal", self.w.log_view.toPlainText())

    def test_debug_lines_are_hidden_unless_asked(self):
        self.w.verbose_log.setChecked(False)
        self.w.registrar("debug", "detalhe interno")
        self.assertNotIn("detalhe interno", self.w.log_view.toPlainText())
        self.w.verbose_log.setChecked(True)
        self.w.registrar("debug", "detalhe interno")
        self.assertIn("detalhe interno", self.w.log_view.toPlainText())

    def test_advanced_options_round_trip(self):
        from pen_drive_maker.gui import OpcoesAvancadas

        cfg = self.w.config_atual()
        cfg.jitter = 7.0
        dialogo = OpcoesAvancadas(cfg, self.w)
        dialogo.jitter.setValue(42.0)
        dialogo.group_subgenres.setChecked(False)
        aplicado = dialogo.aplicar(cfg)
        self.assertEqual(aplicado.jitter, 42.0)
        self.assertFalse(aplicado.group_subgenres)


if __name__ == "__main__":
    unittest.main()
