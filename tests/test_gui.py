"""Testes da interface. Rodam sem tela, com a plataforma offscreen do Qt."""

import os
import pathlib
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

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
        import pen_drive_maker.gui as gui
        from pen_drive_maker import library
        from pen_drive_maker.gui import MainWindow
        from test_engine import ler_id_do_dublê

        self.app = type(self).app
        # Sem rodar deno/ffmpeg de verdade a cada janela: rápido e igual em
        # qualquer máquina. Os arquivos de teste se identificam por marcador.
        self._reais = (gui.find_js_runtime, gui.find_ffmpeg, gui.ejs_package_version,
                       library.read_video_id, library.read_tags)
        gui.find_js_runtime = lambda: "/fake/deno"
        gui.find_ffmpeg = lambda configured="": "/fake/ffmpeg"
        gui.ejs_package_version = lambda: "0.8.0"
        library.read_video_id = ler_id_do_dublê
        library.read_tags = lambda caminho: {}

        MainWindow.PASTA_DADOS = tempfile.mkdtemp()
        self.w = MainWindow()
        self.w.output_edit.setText(tempfile.mkdtemp())

    def tearDown(self):
        import pen_drive_maker.gui as gui
        from pen_drive_maker import library

        if self.w._tarefa is not None:
            self.w._tarefa.wait(20000)
        if self.w.store is not None:
            self.w.store.close()
        # deleteLater só age quando o laço de eventos roda, e aqui ele não roda:
        # sem forçar, as janelas se acumulam e cada troca de tema repinta todas.
        from PySide6.QtCore import QCoreApplication, QEvent

        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        (gui.find_js_runtime, gui.find_ffmpeg, gui.ejs_package_version,
         library.read_video_id, library.read_tags) = self._reais

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

    def _aguardar_tarefa(self):
        """As tarefas de biblioteca rodam numa thread; o teste espera o fim."""
        if self.w._tarefa is not None:
            self.assertTrue(self.w._tarefa.wait(20000), "tarefa não terminou")
        for _ in range(5):
            self.app.processEvents()

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
            caminho.write_text(f"PDMID:v{i}")  # o "arquivo" sabe de onde veio
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
        self._aguardar_tarefa()

        self.assertEqual(self.w.tree.topLevelItemCount(), 0, "a pasta apagada continua na árvore")
        self.assertEqual(self.w.store.stats().pending, 3)
        self.assertIn("voltaram para a fila", self.w.log_view.toPlainText().lower())

    def test_verify_reports_when_everything_is_in_place(self):
        self._fila_com_arquivos()
        self.w.verificar_arquivos()
        self._aguardar_tarefa()
        self.assertIn("tudo no lugar", self.w.log_view.toPlainText())

    # ---- página da biblioteca ---------------------------------------------

    def _abrir_biblioteca(self):
        self._fila_com_arquivos()
        self.w.mostrar_biblioteca()
        return self.w.pagina_biblioteca

    def _selecionar_linhas(self, pagina, linhas):
        from PySide6.QtCore import QItemSelectionModel

        selecao = pagina.tabela.selectionModel()
        selecao.clearSelection()
        for linha in linhas:
            selecao.select(
                pagina.filtro.index(linha, 0),
                QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
            )

    def test_page_buttons_show_which_page_is_visible(self):
        self.assertTrue(self.w.act_pg_baixar.isChecked())
        self.w.mostrar_biblioteca()
        self.assertTrue(self.w.act_pg_biblioteca.isChecked())
        self.assertFalse(self.w.act_pg_baixar.isChecked())
        self.assertIs(self.w.pilha.currentWidget(), self.w.pagina_biblioteca)
        self.w.act_pg_baixar.trigger()  # como um clique
        self.assertIs(self.w.pilha.currentWidget(), self.w.pagina_baixar)
        self.assertFalse(self.w.act_pg_biblioteca.isChecked())

    def test_table_starts_in_arrival_order(self):
        pagina = self._abrir_biblioteca()
        titulos = [pagina.filtro.index(i, 1).data() for i in range(pagina.filtro.rowCount())]
        self.assertEqual(titulos, ["M0", "M1", "M2"])

    def test_library_page_lists_every_track(self):
        pagina = self._abrir_biblioteca()
        self.assertIs(self.w.pilha.currentWidget(), pagina)
        self.assertEqual(pagina.filtro.rowCount(), 3)
        self.assertIn("3 faixa(s)", pagina.resumo.text())

    def test_search_filters_the_table(self):
        pagina = self._abrir_biblioteca()
        pagina.busca.setText("M1")
        self.assertEqual(pagina.filtro.rowCount(), 1)

    def test_selection_actions_need_a_selection(self):
        pagina = self._abrir_biblioteca()
        self.assertFalse(self.w.act_sel_mover.isEnabled())
        self._selecionar_linhas(pagina, [0])
        self.assertTrue(self.w.act_sel_mover.isEnabled())
        self.assertIn("1 faixa(s) selecionada", pagina.rotulo_selecao.text())

    def test_move_selection_into_a_folder_pins_it(self):
        pagina = self._abrir_biblioteca()
        raiz = pathlib.Path(self.w.output_edit.text())
        self._selecionar_linhas(pagina, [0])
        self.w._pedir_pasta = lambda titulo, inicio: str(raiz / "Carro")
        (raiz / "Carro").mkdir()
        self.w.mover_selecao()
        item = self.w.store.get_item("v0")
        self.assertEqual(item.relpath, "Carro/m0.mp3")
        self.assertTrue(item.pinned)
        self.assertTrue((raiz / "Carro" / "m0.mp3").exists())

    def test_moving_outside_the_library_is_refused(self):
        pagina = self._abrir_biblioteca()
        self._selecionar_linhas(pagina, [0])
        self.w._pedir_pasta = lambda titulo, inicio: tempfile.mkdtemp()
        avisos = []
        from PySide6.QtWidgets import QMessageBox

        original = QMessageBox.warning
        QMessageBox.warning = lambda *a, **k: avisos.append(a)
        try:
            self.w.mover_selecao()
        finally:
            QMessageBox.warning = original
        self.assertTrue(avisos)
        self.assertFalse(self.w.store.get_item("v0").pinned)

    def test_remove_selection_can_delete_the_files(self):
        pagina = self._abrir_biblioteca()
        raiz = pathlib.Path(self.w.output_edit.text())
        self._selecionar_linhas(pagina, [0])
        self.w._confirmar_remocao = lambda quantidade: True
        self.w.remover_selecao()
        self.assertFalse((raiz / "EDM" / "Artista" / "m0.mp3").exists())
        self.assertEqual(pagina.filtro.rowCount(), 2, "removidas somem da lista padrão")

    def test_removing_can_be_cancelled(self):
        pagina = self._abrir_biblioteca()
        self._selecionar_linhas(pagina, [0])
        self.w._confirmar_remocao = lambda quantidade: None
        self.w.remover_selecao()
        self.assertEqual(self.w.store.stats().done, 3)

    def test_export_and_import_through_the_window(self):
        self._abrir_biblioteca()
        arquivo = pathlib.Path(tempfile.mkdtemp()) / "bib.json"
        self.w._pedir_arquivo = lambda salvar: str(arquivo)
        self.w.exportar()
        self.assertTrue(arquivo.exists())

        self.w.output_edit.setText(tempfile.mkdtemp())
        self.w.importar()
        self.assertEqual(self.w.store.stats().pending, 3)
        self.assertIn("Clique em Iniciar", self.w.log_view.toPlainText())

    def test_copy_to_another_drive(self):
        self._abrir_biblioteca()
        destino = pathlib.Path(tempfile.mkdtemp())
        self.w._pedir_pasta = lambda titulo, inicio: str(destino)
        self.w._escolher_modo_copia = lambda: True
        self.w._perguntar = lambda *a, **k: True  # usar o destino depois
        self.w.copiar_para()
        self._aguardar_tarefa()
        self.assertTrue((destino / "EDM" / "Artista" / "m0.mp3").exists())
        self.assertEqual(pathlib.Path(self.w.output_edit.text()).resolve(), destino.resolve())
        self.assertEqual(self.w.store.stats().done, 3)

    def test_copy_into_the_library_itself_is_refused(self):
        self._abrir_biblioteca()
        raiz = pathlib.Path(self.w.output_edit.text())
        self.w._pedir_pasta = lambda titulo, inicio: str(raiz / "EDM")
        chamado = []
        self.w._escolher_modo_copia = lambda: chamado.append(1)
        from PySide6.QtWidgets import QMessageBox

        original = QMessageBox.warning
        QMessageBox.warning = lambda *a, **k: None
        try:
            self.w.copiar_para()
        finally:
            QMessageBox.warning = original
        self.assertEqual(chamado, [], "nem deveria perguntar o modo")

    def test_actions_are_disabled_while_a_library_task_runs(self):
        self._abrir_biblioteca()
        self.w._ocupado = True
        self.w.atualizar_acoes()
        self.assertFalse(self.w.act_iniciar.isEnabled())
        self.assertFalse(self.w.act_exportar.isEnabled())
        self.w._ocupado = False
        self.w.atualizar_acoes()
        self.assertTrue(self.w.act_iniciar.isEnabled())

    def test_status_column_follows_the_theme(self):
        from PySide6.QtCore import Qt

        pagina = self._abrir_biblioteca()
        indice = pagina.modelo.index(0, 0)
        antes = pagina.modelo.data(indice, Qt.ItemDataRole.ForegroundRole).name()
        self.w.alternar_tema()
        depois = pagina.modelo.data(indice, Qt.ItemDataRole.ForegroundRole).name()
        self.w.alternar_tema()
        self.assertNotEqual(antes, depois)

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
        self.assertIn("Verificar", mostrado["detalhe"], "deve indicar como evitar rebaixar")
        self.assertIn("baixadas outra vez", mostrado["detalhe"])

    def test_every_queue_action_explains_itself(self):
        for atributo in ("act_verificar", "act_requeue", "act_reextrair", "act_limpar_fila",
                         "act_reorganizar", "act_exportar", "act_importar", "act_copiar",
                         "act_sel_mover", "act_sel_remover"):
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
