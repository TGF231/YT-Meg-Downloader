"""Interface gráfica em PySide6, sobre a assinatura visual da família."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .assinatura import tema
from .assinatura.acoes import BARRA, OCULTA, Acao
from .assinatura.comum import ajustes, como_bool, definir_papel
from .assinatura.janela import JanelaBase
from .config import AUDIO_FORMATS, BROWSERS, AppConfig
from .downloader import DownloadEngine, EngineCallbacks, check_cookies
from .organizer import TrackTags, preview_path, sanitize_component
from .store import QueueStats, QueueStore
from .tools import (
    describe_missing_ejs,
    describe_missing_ffmpeg,
    describe_missing_js_runtime,
    ejs_package_version,
    find_ffmpeg,
    find_js_runtime,
)

APP_NOME = "Pen Drive Maker"

# Papéis de log, resolvidos em cores do tema a cada repintura.
NIVEIS_LOG = {
    "debug": "ink_2",
    "info": "ink",
    "ok": "ok",
    "warn": "warn",
    "error": "bad",
}


class EngineBridge(QObject):
    """Converte os callbacks do motor (thread de trabalho) em sinais Qt."""

    log = Signal(str, str)
    status = Signal(str)
    stats = Signal(object)
    item_done = Signal(str, object)
    file_progress = Signal(float, str)
    finished = Signal(str)

    def callbacks(self) -> EngineCallbacks:
        return EngineCallbacks(
            log=lambda level, message: self.log.emit(level, message),
            status=lambda text: self.status.emit(text),
            stats=lambda stats: self.stats.emit(stats),
            item_done=lambda path, tags: self.item_done.emit(path, tags),
            file_progress=lambda percent, text: self.file_progress.emit(percent, text),
            finished=lambda reason: self.finished.emit(reason),
        )


class EngineThread(QThread):
    def __init__(self, engine: DownloadEngine, force_reextract: bool, parent=None):
        super().__init__(parent)
        self._engine = engine
        self._force = force_reextract

    def run(self) -> None:  # executado na thread de trabalho
        self._engine.run(force_reextract=self._force)


class OpcoesAvancadas(QDialog):
    """O que raramente muda sai da tela principal e vem parar aqui."""

    def __init__(self, cfg: AppConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Opções avançadas")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)

        ritmo = QGroupBox("Proteção contra bloqueio")
        form = QFormLayout(ritmo)
        self.jitter = QDoubleSpinBox()
        self.jitter.setRange(0.0, 3600.0)
        self.jitter.setSuffix(" s")
        self.jitter.setValue(cfg.jitter)
        form.addRow("Jitter aleatório:", self.jitter)
        self.max_retries = QSpinBox()
        self.max_retries.setRange(1, 20)
        self.max_retries.setValue(cfg.max_retries)
        form.addRow("Tentativas por faixa:", self.max_retries)
        self.max_consecutive = QSpinBox()
        self.max_consecutive.setRange(1, 50)
        self.max_consecutive.setValue(cfg.max_consecutive_failures)
        form.addRow("Falhas seguidas = bloqueio:", self.max_consecutive)
        self.block_backoff = QDoubleSpinBox()
        self.block_backoff.setRange(5.0, 7200.0)
        self.block_backoff.setDecimals(0)
        self.block_backoff.setSuffix(" s")
        self.block_backoff.setValue(cfg.block_base_backoff)
        form.addRow("Backoff inicial:", self.block_backoff)
        self.max_blocks = QSpinBox()
        self.max_blocks.setRange(1, 50)
        self.max_blocks.setValue(cfg.max_block_events)
        form.addRow("Bloqueios até encerrar:", self.max_blocks)
        layout.addWidget(ritmo)

        arquivos = QGroupBox("Arquivos e metadados")
        form = QFormLayout(arquivos)
        self.embed_metadata = QCheckBox("Gravar metadados na tag")
        self.embed_metadata.setChecked(cfg.embed_metadata)
        form.addRow("", self.embed_metadata)
        self.embed_thumbnail = QCheckBox("Embutir capa do álbum")
        self.embed_thumbnail.setChecked(cfg.embed_thumbnail)
        form.addRow("", self.embed_thumbnail)
        self.ascii_only = QCheckBox("Somente ASCII nos nomes (FAT32)")
        self.ascii_only.setChecked(cfg.ascii_only)
        form.addRow("", self.ascii_only)
        self.group_subgenres = QCheckBox("Agrupar subgêneros (Dance/House → Eletronica)")
        self.group_subgenres.setChecked(cfg.group_subgenres)
        form.addRow("", self.group_subgenres)
        self.default_genre = QLineEdit(cfg.default_genre)
        form.addRow("Gênero de reserva:", self.default_genre)
        self.default_album = QLineEdit(cfg.default_album)
        form.addRow("Álbum ausente:", self.default_album)
        layout.addWidget(arquivos)

        ferramentas = QGroupBox("Ferramentas externas")
        form = QFormLayout(ferramentas)
        linha = QHBoxLayout()
        self.ffmpeg_path = QLineEdit(cfg.ffmpeg_path)
        self.ffmpeg_path.setPlaceholderText("vazio = detectar automaticamente")
        procurar = QPushButton("Procurar...")
        procurar.clicked.connect(self._escolher_ffmpeg)
        linha.addWidget(self.ffmpeg_path, 1)
        linha.addWidget(procurar)
        form.addRow("ffmpeg:", linha)
        self.allow_remote_components = QCheckBox(
            "Permitir baixar componentes do yt-dlp quando faltarem"
        )
        self.allow_remote_components.setChecked(cfg.allow_remote_components)
        form.addRow("", self.allow_remote_components)
        layout.addWidget(ferramentas)

        botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        botoes.accepted.connect(self.accept)
        botoes.rejected.connect(self.reject)
        layout.addWidget(botoes)

    def _escolher_ffmpeg(self) -> None:
        escolhido, _ = QFileDialog.getOpenFileName(
            self, "Executável do ffmpeg", self.ffmpeg_path.text().strip() or str(Path.home())
        )
        if escolhido:
            self.ffmpeg_path.setText(escolhido)

    def aplicar(self, cfg: AppConfig) -> AppConfig:
        cfg.jitter = self.jitter.value()
        cfg.max_retries = self.max_retries.value()
        cfg.max_consecutive_failures = self.max_consecutive.value()
        cfg.block_base_backoff = self.block_backoff.value()
        cfg.max_block_events = self.max_blocks.value()
        cfg.embed_metadata = self.embed_metadata.isChecked()
        cfg.embed_thumbnail = self.embed_thumbnail.isChecked()
        cfg.ascii_only = self.ascii_only.isChecked()
        cfg.group_subgenres = self.group_subgenres.isChecked()
        cfg.default_genre = self.default_genre.text().strip() or "Sem Genero"
        cfg.default_album = self.default_album.text().strip() or "Singles"
        cfg.ffmpeg_path = self.ffmpeg_path.text().strip()
        cfg.allow_remote_components = self.allow_remote_components.isChecked()
        return cfg


class MainWindow(JanelaBase):
    APP_NOME = APP_NOME
    APP_VERSAO = __version__
    ROTULO_MENUS = "Ferramentas"
    MENUS = (("ferramentas", "Ferramentas"),)
    ACOES = (
        Acao("act_iniciar", "Iniciar / Retomar", "iniciar", quando="parado", onde=BARRA),
        Acao("act_pausar", "Pausar", "alternar_pausa", quando="rodando", onde=BARRA),
        Acao("act_parar", "Parar", "parar", quando="rodando", onde=BARRA),
        Acao("act_requeue", "Reenfileirar falhas", "reenfileirar", quando="parado",
             onde=BARRA, separador=True),
        Acao("act_reextrair", "Reextrair playlist", "reextrair", quando="parado", onde=BARRA),
        Acao("act_opcoes", "Opções avançadas…", "abrir_opcoes", quando="parado",
             onde="ferramentas"),
        Acao("act_testar", "Testar ferramentas externas", "testar_ferramentas",
             onde="ferramentas"),
        Acao("act_limpar_generos", "Limpar cache de gêneros", "limpar_cache_generos",
             quando="parado", onde="ferramentas"),
        Acao("act_limpar_log", "Limpar log", "limpar_log", onde=OCULTA),
    )

    def __init__(self):
        self.cfg = AppConfig()
        self.store: Optional[QueueStore] = None
        self.engine: Optional[DownloadEngine] = None
        self.thread: Optional[EngineThread] = None
        self.bridge = EngineBridge()
        self._tree_nodes: Dict[tuple, QTreeWidgetItem] = {}
        self._rodando = False
        super().__init__()
        self._conectar_bridge()
        self._carregar_preferencias()
        self.carregar_tema_salvo()
        self.testar_ferramentas()

    # ----------------------------------------------------------- conteúdo

    def montar_conteudo(self) -> None:
        pagina = QWidget()
        fora = QVBoxLayout(pagina)
        fora.addWidget(self._caixa_playlist())
        fora.addWidget(self._caixa_organizacao())
        fora.addWidget(self._caixa_progresso())

        divisor = QSplitter(Qt.Orientation.Vertical)
        divisor.addWidget(self._caixa_arvore())
        divisor.addWidget(self._caixa_log())
        divisor.setSizes([260, 260])
        fora.addWidget(divisor, 1)
        self.adicionar_pagina(pagina)

    def _caixa_playlist(self) -> QGroupBox:
        caixa = QGroupBox("Playlist e destino")
        form = QFormLayout(caixa)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://music.youtube.com/playlist?list=...")
        form.addRow("URL da playlist:", self.url_edit)

        linha = QHBoxLayout()
        self.output_edit = QLineEdit()
        procurar = QPushButton("Escolher...")
        procurar.clicked.connect(self._escolher_saida)
        linha.addWidget(self.output_edit, 1)
        linha.addWidget(procurar)
        form.addRow("Diretório de saída:", linha)

        linha = QHBoxLayout()
        self.cookies_mode = QComboBox()
        self.cookies_mode.addItem("Sem cookies", "none")
        self.cookies_mode.addItem("Arquivo cookies.txt", "file")
        self.cookies_mode.addItem("Direto do navegador", "browser")
        self.cookies_mode.currentIndexChanged.connect(self._atualizar_cookies)
        self.cookies_file = QLineEdit()
        self.cookies_file.setPlaceholderText("caminho do cookies.txt")
        self.cookies_browse = QPushButton("Procurar...")
        self.cookies_browse.clicked.connect(self._escolher_cookies)
        self.cookies_browser = QComboBox()
        self.cookies_browser.addItems(BROWSERS)
        for w in (self.cookies_mode, self.cookies_file, self.cookies_browse, self.cookies_browser):
            linha.addWidget(w, 1 if w is self.cookies_file else 0)
        form.addRow("Cookies:", linha)
        return caixa

    def _caixa_organizacao(self) -> QGroupBox:
        caixa = QGroupBox("Organização e ritmo")
        linha = QHBoxLayout(caixa)

        esquerda = QFormLayout()
        self.genre_source = QComboBox()
        self.genre_source.addItem("Consultar na internet", "online")
        self.genre_source.addItem("Nome da playlist", "playlist")
        self.genre_source.addItem("Metadados da faixa", "metadata")
        self.genre_source.addItem("Sempre o gênero de reserva", "fixed")
        self.genre_source.setToolTip(
            "O YouTube Music não informa o gênero. Consultar na internet busca "
            "numa base pública; o nome da playlist usa a sua própria classificação."
        )
        self.genre_source.currentIndexChanged.connect(self._atualizar_estrutura)
        esquerda.addRow("Pasta de gênero:", self.genre_source)

        self.audio_format = QComboBox()
        for chave, rotulo in AUDIO_FORMATS.items():
            self.audio_format.addItem(rotulo, chave)
        self.audio_format.currentIndexChanged.connect(self._atualizar_estrutura)
        esquerda.addRow("Formato de saída:", self.audio_format)
        linha.addLayout(esquerda, 1)

        direita = QFormLayout()
        self.min_interval = QDoubleSpinBox()
        self.min_interval.setRange(0.0, 3600.0)
        self.min_interval.setDecimals(0)
        self.min_interval.setSuffix(" s")
        self.min_interval.setMaximumWidth(160)
        self.min_interval.setToolTip(
            "Espera mínima entre downloads. Valores baixos aumentam o risco de bloqueio."
        )
        direita.addRow("Intervalo entre downloads:", self.min_interval)
        self.estrutura = QLabel()
        definir_papel(self.estrutura, "mono")
        self.estrutura.setWordWrap(True)
        direita.addRow("Destino:", self.estrutura)
        linha.addLayout(direita, 1)
        return caixa

    def _caixa_progresso(self) -> QGroupBox:
        caixa = QGroupBox("Progresso")
        layout = QVBoxLayout(caixa)
        self.contadores = QLabel("Total: 0 | Baixadas: 0 | Falhas: 0 | Na fila: 0")
        definir_papel(self.contadores, "secao")
        layout.addWidget(self.contadores)
        self.barra_playlist = QProgressBar()
        self.barra_playlist.setFormat("Playlist: %p%")
        layout.addWidget(self.barra_playlist)
        self.barra_faixa = QProgressBar()
        self.barra_faixa.setFormat("Faixa atual: %p%")
        layout.addWidget(self.barra_faixa)
        self.rotulo_status = QLabel("Ocioso")
        layout.addWidget(self.rotulo_status)
        return caixa

    def _caixa_arvore(self) -> QGroupBox:
        caixa = QGroupBox("Estrutura criada")
        layout = QVBoxLayout(caixa)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Gênero / Artista / Álbum", "Faixas"])
        self.tree.setColumnWidth(0, 520)
        layout.addWidget(self.tree)
        return caixa

    def _caixa_log(self) -> QGroupBox:
        caixa = QGroupBox("Log")
        layout = QVBoxLayout(caixa)
        topo = QHBoxLayout()
        self.verbose_log = QCheckBox("Mostrar depuração")
        limpar = QPushButton("Limpar")
        limpar.clicked.connect(self.limpar_log)
        topo.addWidget(self.verbose_log)
        topo.addStretch(1)
        topo.addWidget(limpar)
        layout.addLayout(topo)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        # O QPlainTextEdit do log não tem papel próprio no tema; o fundo vem
        # da folha global e a cor de cada linha é aplicada no HTML.
        fonte = self.log_view.font()
        fonte.setFamily("monospace")
        self.log_view.setFont(fonte)
        layout.addWidget(self.log_view)
        return caixa

    # -------------------------------------------------- ganchos JanelaBase

    def estados(self) -> set:
        return {"rodando"} if self._rodando else {"parado"}

    def contexto_da_barra(self) -> str:
        return self.url_edit.text().strip() if hasattr(self, "url_edit") else ""

    def ao_trocar_tema(self) -> None:
        """O log guarda cor por linha: a folha de estilo não alcança."""
        self._repintar_log()

    # ------------------------------------------------------- preferências

    def _preferencias_app(self):
        return ajustes(APP_NOME, self.PASTA_DADOS)

    def _carregar_preferencias(self) -> None:
        prefs = self._preferencias_app()
        dados = {k: prefs.value(k) for k in AppConfig().to_dict() if prefs.contains(k)}
        self.cfg = AppConfig.from_dict(dados)
        self.url_edit.setText(str(self.cfg.playlist_url))
        self.output_edit.setText(str(self.cfg.output_dir))
        self.min_interval.setValue(float(self.cfg.min_interval))
        self._selecionar(self.audio_format, self.cfg.audio_format)
        self._selecionar(self.genre_source, self.cfg.genre_source)
        self._selecionar(self.cookies_mode, self.cfg.cookies_mode)
        self.cookies_file.setText(str(self.cfg.cookies_file))
        indice = self.cookies_browser.findText(str(self.cfg.cookies_browser))
        self.cookies_browser.setCurrentIndex(max(0, indice))
        self.verbose_log.setChecked(como_bool(prefs.value("verbose_log"), False))
        self._atualizar_cookies()
        self._atualizar_estrutura()

    def _salvar_preferencias(self) -> None:
        prefs = self._preferencias_app()
        for chave, valor in self.config_atual().to_dict().items():
            prefs.setValue(chave, valor)
        prefs.setValue("verbose_log", self.verbose_log.isChecked())
        prefs.sync()

    @staticmethod
    def _selecionar(combo: QComboBox, valor) -> None:
        indice = combo.findData(valor)
        combo.setCurrentIndex(indice if indice >= 0 else 0)

    def config_atual(self) -> AppConfig:
        """A tela principal manda; o resto vem do que ficou nas opções."""
        self.cfg.playlist_url = self.url_edit.text().strip()
        self.cfg.output_dir = self.output_edit.text().strip()
        self.cfg.min_interval = self.min_interval.value()
        self.cfg.audio_format = self.audio_format.currentData()
        self.cfg.genre_source = self.genre_source.currentData()
        self.cfg.cookies_mode = self.cookies_mode.currentData()
        self.cfg.cookies_file = self.cookies_file.text().strip()
        self.cfg.cookies_browser = self.cookies_browser.currentText()
        self.cfg.normalize()
        return self.cfg

    # ------------------------------------------------------------- ações

    def iniciar(self, force_reextract: bool = False) -> None:
        if self._rodando:
            return
        cfg = self.config_atual()
        problemas = cfg.validate()
        if problemas:
            QMessageBox.warning(self, "Configuração incompleta", "\n".join(problemas))
            return
        try:
            Path(cfg.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
            store = self._abrir_store(cfg)
        except OSError as erro:
            QMessageBox.critical(self, "Erro", f"Não foi possível usar o diretório:\n{erro}")
            return

        self._salvar_preferencias()
        permitir_sem_runtime = self._confirmar_runtime()
        if permitir_sem_runtime is None or not self._confirmar_ffmpeg(cfg):
            return
        if not self._confirmar_cookies(cfg):
            return

        cfg = self.config_atual()
        self.engine = DownloadEngine(
            cfg, store, self.bridge.callbacks(),
            allow_missing_js_runtime=permitir_sem_runtime,
        )
        self.thread = EngineThread(self.engine, force_reextract)
        self.thread.finished.connect(self._ao_terminar_thread)
        self.registrar("info", f"Fila persistente: {cfg.db_path()}")
        self._definir_rodando(True)
        self.thread.start()

    def reextrair(self) -> None:
        self.iniciar(force_reextract=True)

    def alternar_pausa(self) -> None:
        if self.engine is None:
            return
        if self.engine.is_paused:
            self.engine.resume()
            self.act_pausar.setText("Pausar")
        else:
            self.engine.pause()
            self.act_pausar.setText("Retomar")

    def parar(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.act_parar.setEnabled(False)
            self.registrar("warn", "Parada solicitada; terminando a faixa atual...")

    def reenfileirar(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        quantidade = store.requeue_failed()
        self.registrar("info", f"{quantidade} faixa(s) com falha voltaram para a fila.")
        self._ao_stats(store.stats())

    def limpar_cache_generos(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        quantidade = store.clear_genre_cache()
        self.registrar("info", f"{quantidade} gênero(s) em cache foram esquecidos.")

    def limpar_log(self) -> None:
        self.log_view.clear()
        self._linhas_log = []

    def abrir_opcoes(self) -> None:
        dialogo = OpcoesAvancadas(self.config_atual(), self)
        if dialogo.exec() == QDialog.DialogCode.Accepted:
            self.cfg = dialogo.aplicar(self.cfg)
            self.cfg.normalize()
            self._salvar_preferencias()
            self._atualizar_estrutura()
            self.registrar("info", "Opções avançadas atualizadas.")

    def testar_ferramentas(self) -> None:
        """Diz, em três linhas, se o programa tem o que precisa para baixar."""
        runtime = find_js_runtime()
        if runtime:
            self.registrar("info", f"Runtime JavaScript: {runtime}")
        else:
            for linha in describe_missing_js_runtime().splitlines():
                self.registrar("warn", linha)
        solver = ejs_package_version()
        if solver:
            self.registrar("info", f"Solucionador de desafios: {solver}")
        else:
            for linha in describe_missing_ejs().splitlines():
                self.registrar("warn", linha)
        encontrado = find_ffmpeg(self.cfg.ffmpeg_path)
        if encontrado:
            self.registrar("info", f"ffmpeg: {encontrado}")
        else:
            for linha in describe_missing_ffmpeg(self.cfg.ffmpeg_path).splitlines():
                self.registrar("warn", linha)

    # --------------------------------------------------------- diálogos

    def _perguntar(self, titulo: str, texto: str, detalhe: str, seguir: str) -> bool:
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Icon.Warning)
        caixa.setWindowTitle(titulo)
        caixa.setText(texto)
        caixa.setInformativeText(detalhe)
        prosseguir = caixa.addButton(seguir, QMessageBox.ButtonRole.AcceptRole)
        caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caixa.setDefaultButton(prosseguir)
        caixa.exec()
        return caixa.clickedButton() is prosseguir

    def _confirmar_runtime(self) -> Optional[bool]:
        """None = cancelar. False = runtime presente. True = seguir sem ele."""
        if find_js_runtime() is not None:
            return False
        detalhe = describe_missing_js_runtime()
        for linha in detalhe.splitlines():
            self.registrar("error", linha)
        if self._perguntar(
            "Runtime JavaScript ausente",
            "O YouTube exige um runtime JavaScript para liberar os links de áudio.",
            detalhe,
            "Tentar mesmo assim",
        ):
            return True
        self.registrar("warn", "Início cancelado: instale o Deno e tente de novo.")
        return None

    def _confirmar_ffmpeg(self, cfg: AppConfig) -> bool:
        precisa = cfg.audio_format != "best" or cfg.embed_metadata or cfg.embed_thumbnail
        if not precisa or find_ffmpeg(cfg.ffmpeg_path):
            return True
        detalhe = describe_missing_ffmpeg(cfg.ffmpeg_path)
        for linha in detalhe.splitlines():
            self.registrar("warn", linha)
        if self._perguntar(
            "ffmpeg não encontrado", "O ffmpeg não está disponível.", detalhe,
            "Baixar sem converter",
        ):
            return True
        self.registrar("warn", "Início cancelado: instale o ffmpeg e tente de novo.")
        return False

    def _confirmar_cookies(self, cfg: AppConfig) -> bool:
        problema = check_cookies(cfg)
        if problema is None:
            return True
        for linha in problema.splitlines():
            self.registrar("error", linha)
        if not self._perguntar(
            "Problema com os cookies",
            "Não foi possível carregar os cookies configurados.",
            problema,
            "Continuar sem cookies",
        ):
            self.registrar("warn", "Início cancelado por causa dos cookies.")
            return False
        self._selecionar(self.cookies_mode, "none")
        self._atualizar_cookies()
        self.registrar("warn", "Seguindo sem cookies nesta execução.")
        return True

    # ------------------------------------------------------------ apoio

    def _escolher_saida(self) -> None:
        inicio = self.output_edit.text().strip() or str(Path.home())
        escolhido = QFileDialog.getExistingDirectory(self, "Diretório de saída", inicio)
        if escolhido:
            self.output_edit.setText(escolhido)
            self._atualizar_estrutura()

    def _escolher_cookies(self) -> None:
        escolhido, _ = QFileDialog.getOpenFileName(
            self, "Arquivo de cookies", str(Path.home()), "Cookies (*.txt);;Todos (*)"
        )
        if escolhido:
            self.cookies_file.setText(escolhido)

    def _atualizar_cookies(self) -> None:
        modo = self.cookies_mode.currentData()
        self.cookies_file.setVisible(modo == "file")
        self.cookies_browse.setVisible(modo == "file")
        self.cookies_browser.setVisible(modo == "browser")

    def _genero_de_exemplo(self) -> str:
        fonte = self.genre_source.currentData()
        if fonte == "fixed":
            return self.cfg.default_genre
        if fonte == "online":
            return "Gênero consultado"
        if fonte == "playlist":
            linha = self.store.get_playlist() if self.store is not None else None
            return ((linha["title"] if linha else "") or "") or "Nome da playlist"
        return "Gênero"

    def _atualizar_estrutura(self) -> None:
        formato = self.audio_format.currentData()
        self.estrutura.setText(
            preview_path(
                self.output_edit.text(),
                ascii_only=self.cfg.ascii_only,
                extension="ext" if formato == "best" else formato,
                genre=self._genero_de_exemplo(),
            )
        )

    def _abrir_store(self, cfg: AppConfig) -> QueueStore:
        caminho = cfg.db_path()
        if self.store is not None and self.store.db_path == caminho:
            return self.store
        if self.store is not None:
            self.store.close()
        self.store = QueueStore(caminho)
        self._remontar_arvore()
        self._ao_stats(self.store.stats())
        self._atualizar_estrutura()
        return self.store

    def _store_para_leitura(self) -> Optional[QueueStore]:
        cfg = self.config_atual()
        if not cfg.output_dir.strip():
            QMessageBox.warning(self, "Configuração", "Escolha o diretório de saída.")
            return None
        if not cfg.db_path().exists():
            QMessageBox.information(self, "Fila", "Nenhuma fila encontrada nesse diretório.")
            return None
        return self._abrir_store(cfg)

    def _definir_rodando(self, rodando: bool) -> None:
        self._rodando = rodando
        self.atualizar_acoes()
        for widget in (
            self.url_edit, self.output_edit, self.cookies_mode, self.cookies_file,
            self.cookies_browser, self.audio_format, self.genre_source,
        ):
            widget.setEnabled(not rodando)

    # ----------------------------------------------------------- sinais

    def _conectar_bridge(self) -> None:
        self._linhas_log = []
        self.bridge.log.connect(self.registrar)
        self.bridge.status.connect(self._ao_status)
        self.bridge.stats.connect(self._ao_stats)
        self.bridge.item_done.connect(self._ao_item_pronto)
        self.bridge.file_progress.connect(self._ao_progresso)
        self.bridge.finished.connect(self._ao_terminar_motor)

    @Slot(str, str)
    def registrar(self, nivel: str, mensagem: str) -> None:
        if nivel == "debug" and not self.verbose_log.isChecked():
            return
        self._linhas_log.append((nivel, mensagem))
        del self._linhas_log[:-5000]
        self.log_view.appendHtml(self._linha_html(nivel, mensagem))

    @staticmethod
    def _escapar(texto: str) -> str:
        return texto.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _linha_html(self, nivel: str, mensagem: str) -> str:
        cor = getattr(tema.atual(), NIVEIS_LOG.get(nivel, "ink"))
        return f'<span style="color:{cor};">{self._escapar(mensagem)}</span>'

    def _repintar_log(self) -> None:
        self.log_view.clear()
        for nivel, mensagem in self._linhas_log:
            self.log_view.appendHtml(self._linha_html(nivel, mensagem))

    @Slot(str)
    def _ao_status(self, texto: str) -> None:
        self.rotulo_status.setText(texto)
        self.statusBar().showMessage(texto)

    @Slot(object)
    def _ao_stats(self, stats: QueueStats) -> None:
        self.contadores.setText(
            f"Total: {stats.total} | Baixadas: {stats.done} | "
            f"Falhas: {stats.failed} | Na fila: {stats.pending}"
        )
        self.barra_playlist.setMaximum(max(1, stats.total))
        self.barra_playlist.setValue(stats.done + stats.failed)

    @Slot(float, str)
    def _ao_progresso(self, percentual: float, texto: str) -> None:
        self.barra_faixa.setValue(int(percentual))
        self.barra_faixa.setFormat(f"Faixa atual: {texto}")

    @Slot(str, object)
    def _ao_item_pronto(self, caminho: str, tags: TrackTags) -> None:
        partes = [sanitize_component(p, self.cfg.ascii_only) for p in tags.folder_parts()]
        self._acrescentar_na_arvore(partes, 1)
        if self.tree.topLevelItemCount():
            self.tree.expandItem(self.tree.topLevelItem(0))

    def _acrescentar_na_arvore(self, partes, quantidade: int) -> None:
        no = None
        chave: tuple = ()
        for parte in partes:
            chave = chave + (parte,)
            existente = self._tree_nodes.get(chave)
            if existente is None:
                existente = QTreeWidgetItem([parte, "0"])
                self._tree_nodes[chave] = existente
                if no is None:
                    self.tree.addTopLevelItem(existente)
                else:
                    no.addChild(existente)
            no = existente
            no.setText(1, str(int(no.text(1) or 0) + quantidade))

    def _remontar_arvore(self) -> None:
        self.tree.clear()
        self._tree_nodes.clear()
        if self.store is None:
            return
        for genero, artista, album, quantidade in self.store.done_tree():
            self._acrescentar_na_arvore(
                [genero or "?", artista or "?", album or "?"], quantidade
            )

    @Slot(str)
    def _ao_terminar_motor(self, motivo: str) -> None:
        self.statusBar().showMessage(motivo)
        self.barra_faixa.setValue(0)
        self.barra_faixa.setFormat("Faixa atual: %p%")

    @Slot()
    def _ao_terminar_thread(self) -> None:
        self._definir_rodando(False)
        self.act_pausar.setText("Pausar")
        if self.store is not None:
            self._ao_stats(self.store.stats())
            self._atualizar_estrutura()

    # ----------------------------------------------------------- janela

    def closeEvent(self, evento) -> None:
        if self.thread is not None and self.thread.isRunning():
            resposta = QMessageBox.question(
                self, "Sair",
                "Um download está em andamento. Parar e sair?\n"
                "A fila fica salva e pode ser retomada depois.",
            )
            if resposta != QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
            if self.engine is not None:
                self.engine.stop()
            self.thread.wait(30000)
        self._salvar_preferencias()
        if self.store is not None:
            self.store.close()
        super().closeEvent(evento)


def main() -> int:
    from .assinatura import aplicacao, icones, janela_nativa

    app = aplicacao.criar(APP_NOME, sys.argv)
    janela_nativa.definir_escotilha("PENDRIVEMAKER_BARRA_NATIVA")
    icones.definir_prefixo("pendrivemaker_icones")
    janela = MainWindow()
    janela.dimensionar_para_a_tela()
    janela.show()
    return app.exec()
