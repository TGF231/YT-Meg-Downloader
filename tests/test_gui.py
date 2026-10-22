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
        from pen_drive_maker.organizer import FOLDER_STRUCTURES

        vistos = {}
        for estrutura in FOLDER_STRUCTURES:
            self.w._selecionar(self.w.folder_structure, estrutura)
            vistos[estrutura] = self.w.estrutura.text()
        self.assertEqual(
            len(set(vistos.values())), len(FOLDER_STRUCTURES),
            f"duas opções com o mesmo resultado: {vistos}",
        )
        self.assertIn("Album", vistos["genre_artist_album"])
        self.assertNotIn("Album", vistos["genre_artist"])
        self.assertIn("Artista - Titulo", vistos["genre"])
        self.assertIn("01 - Artista - Titulo", vistos["album"])

    def test_the_selector_offers_every_structure(self):
        from pen_drive_maker.organizer import FOLDER_STRUCTURES

        oferecidas = {
            self.w.folder_structure.itemData(i)
            for i in range(self.w.folder_structure.count())
        }
        self.assertEqual(oferecidas, set(FOLDER_STRUCTURES))

    def test_typing_the_output_folder_updates_the_preview(self):
        self.w.output_edit.setText("/media/MEU_PENDRIVE")
        self.assertIn("MEU_PENDRIVE", self.w.estrutura.text())
        self.w.output_edit.setText("/outra/pasta")
        self.assertIn("outra", self.w.estrutura.text())
        self.assertNotIn("MEU_PENDRIVE", self.w.estrutura.text())

    def test_folder_structure_reaches_the_config(self):
        from pen_drive_maker.organizer import FOLDER_STRUCTURES

        for estrutura in FOLDER_STRUCTURES:
            self.w._selecionar(self.w.folder_structure, estrutura)
            self.assertEqual(self.w.config_atual().folder_structure, estrutura)

    # ---- fila x disco ---------------------------------------------------

    def _fila_com_arquivos(self, quantidade=3):
        """Fila já baixada, com arquivos de verdade no disco."""
        from pen_drive_maker.store import QueueStore

        raiz = pathlib.Path(self.w.output_edit.text())
        cfg = self.w.config_atual()
        banco = QueueStore(cfg.db_path())
        arquivos = []
        for i in range(quantidade):
            caminho = raiz / "EDM" / "Artista" / f"m{i}.mp3"
            caminho.parent.mkdir(parents=True, exist_ok=True)
            caminho.write_bytes(b"x")
            arquivos.append(caminho)
            banco.add_items([{"video_id": f"v{i}", "url": f"https://x/v{i}", "title": f"M{i}"}])
            banco.mark_done(f"v{i}", str(caminho), "EDM", "Artista", "", f"M{i}")
        banco.set_playlist("https://x/y", "EDM")
        banco.close()
        return raiz, arquivos

    def test_verify_requeues_deleted_files_and_clears_the_tree(self):
        """O caso relatado: apagar as pastas e a árvore continuar mostrando-as."""
        import shutil

        raiz, _ = self._fila_com_arquivos()
        self.w._carregar_estado_anterior()
        self.assertEqual(self.w.tree.topLevelItemCount(), 1, "a árvore deveria mostrar EDM")

        shutil.rmtree(raiz / "EDM")
        self.w.verificar_arquivos()

        self.assertEqual(self.w.tree.topLevelItemCount(), 0, "a pasta apagada continua na árvore")
        self.assertEqual(self.w.store.stats().pending, 3)
        self.assertIn("Voltaram para a fila", self.w.log_view.toPlainText())

    def test_verify_reports_when_everything_is_in_place(self):
        self._fila_com_arquivos()
        self.w.verificar_arquivos()
        self.assertIn("estão no disco", self.w.log_view.toPlainText())

    def test_opening_the_program_shows_the_previous_queue_checked_against_disk(self):
        import shutil

        raiz, _ = self._fila_com_arquivos()
        shutil.rmtree(raiz / "EDM")
        self.w._carregar_estado_anterior()
        self.assertEqual(self.w.tree.topLevelItemCount(), 0)
        self.assertIn("Total: 3", self.w.contadores.text())
        self.assertIn("Na fila: 3", self.w.contadores.text())

    def test_reset_clears_queue_history_and_tree_but_keeps_the_files(self):
        raiz, arquivos = self._fila_com_arquivos()
        self.w._carregar_estado_anterior()
        self.w._perguntar = lambda *a, **k: True

        self.w.limpar_fila()

        self.assertEqual(self.w.store.stats().total, 0)
        self.assertEqual(self.w.tree.topLevelItemCount(), 0)
        self.assertIn("Total: 0", self.w.contadores.text())
        self.assertTrue(all(a.exists() for a in arquivos), "nenhum arquivo pode ser apagado")

    def test_reset_can_be_cancelled(self):
        self._fila_com_arquivos()
        self.w._carregar_estado_anterior()
        self.w._perguntar = lambda *a, **k: False
        self.w.limpar_fila()
        self.assertEqual(self.w.store.stats().done, 3)
        self.assertEqual(self.w.tree.topLevelItemCount(), 1)

    def test_reset_explains_that_files_are_kept(self):
        """A confirmação tem de dizer o que acontece, antes de o usuário aceitar."""
        self._fila_com_arquivos()
        mostrado = {}

        def espiao(titulo, texto, detalhe, seguir):
            mostrado.update(titulo=titulo, texto=texto, detalhe=detalhe)
            return False

        self.w._perguntar = espiao
        self.w.limpar_fila()
        self.assertIn("Nenhum arquivo é apagado", mostrado["detalhe"])
        self.assertIn("baixadas outra vez", mostrado["detalhe"])

    def test_every_queue_action_explains_itself(self):
        for atributo in ("act_verificar", "act_requeue", "act_reextrair", "act_limpar_fila"):
            acao = getattr(self.w, atributo)
            self.assertTrue(len(acao.toolTip()) > 30, f"{atributo} sem explicação")

    def test_a_store_deleted_behind_our_back_is_reopened(self):
        self._fila_com_arquivos()
        cfg = self.w.config_atual()
        self.w._abrir_store(cfg)
        self.w.store.close()
        cfg.db_path().unlink()
        for extra in cfg.db_path().parent.glob("queue.db-*"):
            extra.unlink()
        novo = self.w._abrir_store(cfg)
        self.assertEqual(novo.stats().total, 0)

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
