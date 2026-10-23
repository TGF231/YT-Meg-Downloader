"""Interface gráfica em PySide6, sobre a assinatura visual da família."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QActionGroup
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
    QTableWidget,
    QTableWidgetItem,
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
from .biblioteca_ui import PaginaBiblioteca, TarefaThread
from .config import AUDIO_FORMATS, BROWSERS, AppConfig
from .downloader import DownloadEngine, EngineCallbacks, check_cookies
from .identify import find_loose, identify, vincular
from .library import (
    ImportError_,
    copy_library,
    export_library,
    import_library,
    move_to_folder,
    reconcile,
    remove_tracks,
    reorganize,
)
from .organizer import TrackTags, preview_path
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
    ROTULO_MENUS = "Menu"
    MENUS = (("fila", "Fila"), ("biblioteca", "Biblioteca"), ("ferramentas", "Ferramentas"))
    ACOES = (
        Acao("act_pg_baixar", "Baixar", "mostrar_baixar", onde=BARRA, alternavel=True),
        Acao("act_pg_biblioteca", "Biblioteca", "mostrar_biblioteca", onde=BARRA,
             alternavel=True),
        Acao("act_iniciar", "Iniciar / Retomar", "iniciar", quando="parado", onde=BARRA,
             separador=True),
        Acao("act_pausar", "Pausar", "alternar_pausa", quando="rodando", onde=BARRA),
        Acao("act_parar", "Parar", "parar", quando="rodando", onde=BARRA),
        Acao("act_cancelar_tarefa", "Cancelar tarefa", "cancelar_tarefa", quando="ocupado",
             onde=BARRA),
        Acao("act_verificar", "Verificar biblioteca", "verificar_arquivos", quando="parado",
             onde=BARRA, separador=True),
        Acao("act_requeue", "Tentar de novo as que falharam", "reenfileirar",
             quando="parado", onde="fila"),
        Acao("act_reextrair", "Atualizar lista da playlist", "reextrair", quando="parado",
             onde="fila"),
        Acao("act_reorganizar", "Aplicar estrutura atual aos arquivos…", "reorganizar",
             quando="parado", onde="biblioteca"),
        Acao("act_identificar", "Identificar arquivos soltos…", "identificar_soltos",
             quando="parado", onde="biblioteca"),
        Acao("act_exportar", "Exportar biblioteca…", "exportar", quando="parado",
             onde="biblioteca", separador=True),
        Acao("act_importar", "Importar biblioteca…", "importar", quando="parado",
             onde="biblioteca"),
        Acao("act_copiar", "Copiar para outro pen drive…", "copiar_para", quando="parado",
             onde="biblioteca"),
        Acao("act_limpar_fila", "Esquecer a biblioteca…", "limpar_fila", quando="parado",
             onde="biblioteca", separador=True),
        Acao("act_sel_mover", "Mover para pasta…", "mover_selecao", quando="selecao",
             onde=OCULTA),
        Acao("act_sel_liberar", "Voltar à estrutura automática", "liberar_selecao",
             quando="selecao", onde=OCULTA),
        Acao("act_sel_rebaixar", "Baixar de novo", "rebaixar_selecao", quando="selecao",
             onde=OCULTA),
        Acao("act_sel_remover", "Remover da biblioteca…", "remover_selecao",
             quando="selecao", onde=OCULTA),
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
        self._ocupado = False
        self._tarefa: Optional[TarefaThread] = None
        super().__init__()
        # Par exclusivo: o botão marcado diz qual página está à vista.
        self._grupo_paginas = QActionGroup(self)
        self._grupo_paginas.setExclusive(True)
        self._grupo_paginas.addAction(self.act_pg_baixar)
        self._grupo_paginas.addAction(self.act_pg_biblioteca)
        self.act_pg_baixar.setChecked(True)
        self._conectar_bridge()
        self._explicar_acoes()
        self._carregar_preferencias()
        self.carregar_tema_salvo()
        self.testar_ferramentas()
        self._carregar_estado_anterior()

    DICAS = {
        "act_iniciar": "Começa ou continua a fila. Faixas já baixadas são puladas.",
        "act_verificar": (
            "Compara a biblioteca com o disco: reconhece arquivos que você moveu, "
            "aprende arquivos postos à mão e devolve à fila o que foi apagado."
        ),
        "act_reorganizar": (
            "Move os arquivos já baixados para a estrutura de pastas escolhida, sem "
            "baixar de novo. Faixas fixadas ficam onde estão."
        ),
        "act_cancelar_tarefa": "Interrompe a tarefa de biblioteca em andamento.",
        "act_identificar": (
            "Procura no YouTube os arquivos sem origem conhecida (pelo nome 'Artista - "
            "Título' ou pelas tags) e vincula a URL. O que for incerto, você confirma."
        ),
        "act_exportar": (
            "Grava a biblioteca — cada música e o local dela — num arquivo, para "
            "reproduzi-la em outro computador ou pen drive."
        ),
        "act_importar": (
            "Acrescenta uma biblioteca exportada. As faixas ficam na fila, cada uma "
            "fixada no mesmo local que tinha."
        ),
        "act_copiar": (
            "Reproduz esta biblioteca em outra pasta, com a mesma estrutura: copia o "
            "que existe aqui e deixa na fila o que faltar."
        ),
        "act_sel_mover": "Move as faixas selecionadas para uma pasta e as fixa lá.",
        "act_sel_liberar": (
            "Tira a fixação: a próxima reorganização devolve as faixas à estrutura "
            "de pastas automática."
        ),
        "act_sel_rebaixar": "Põe as faixas selecionadas de volta na fila.",
        "act_sel_remover": (
            "Tira as faixas da biblioteca. Uma atualização da playlist não as traz "
            "de volta."
        ),
        "act_requeue": (
            "Devolve à fila as faixas que falharam todas as tentativas, para "
            "tentar baixá-las de novo."
        ),
        "act_reextrair": (
            "Busca a playlist de novo no YouTube e acrescenta as músicas novas. "
            "Não apaga nada e não rebaixa o que já está pronto."
        ),
        "act_limpar_fila": (
            "Esquece playlists, fila e histórico desta pasta, para recomeçar do zero. "
            "Não apaga nenhum arquivo do disco."
        ),
    }

    def _explicar_acoes(self) -> None:
        for atributo, texto in self.DICAS.items():
            acao = getattr(self, atributo)
            acao.setToolTip(texto)
            acao.setStatusTip(texto)

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
        self.pagina_baixar = pagina
        self.adicionar_pagina(pagina)

        self.pagina_biblioteca = PaginaBiblioteca()
        for atributo in ("act_sel_mover", "act_sel_liberar", "act_sel_rebaixar",
                         "act_sel_remover"):
            self.pagina_biblioteca.acrescentar_botao(getattr(self, atributo))
        self.pagina_biblioteca.selecao_mudou.connect(self.atualizar_acoes)
        self.adicionar_pagina(self.pagina_biblioteca)

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

        self.folder_structure = QComboBox()
        self.folder_structure.addItem("Gênero / Artista / Álbum", "genre_artist_album")
        self.folder_structure.addItem("Gênero / Artista", "genre_artist")
        self.folder_structure.addItem("Somente gênero", "genre")
        self.folder_structure.addItem("Somente artista", "artist")
        self.folder_structure.addItem("Somente álbum", "album")
        self.folder_structure.setToolTip(
            "Artista e álbum continuam gravados na tag do arquivo em qualquer "
            "opção. O que sai da pasta entra no nome: sem a pasta de álbum o "
            "número da faixa sai, e sem a de artista o nome dele entra."
        )
        self.folder_structure.currentIndexChanged.connect(self._atualizar_estrutura)
        esquerda.addRow("Estrutura de pastas:", self.folder_structure)

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
        # Digitar ou colar a pasta também precisa atualizar o exemplo, não só o
        # botão "Escolher...".
        self.output_edit.textChanged.connect(self._atualizar_estrutura)
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
        self.tree.setHeaderLabels(["Pastas criadas", "Faixas"])
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
        if self._rodando:
            return {"rodando"}
        if self._ocupado:
            return {"ocupado"}  # tarefa de biblioteca em andamento
        estados = {"parado"}
        pagina = getattr(self, "pagina_biblioteca", None)
        if pagina is not None and pagina.selecionadas():
            estados.add("selecao")
        return estados

    def contexto_da_barra(self) -> str:
        return self.url_edit.text().strip() if hasattr(self, "url_edit") else ""

    def ao_trocar_tema(self) -> None:
        """Log e coluna de status pintam cor em Python: a folha de estilo não alcança."""
        self._repintar_log()
        self.pagina_biblioteca.modelo.repintar()

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
        self._selecionar(self.folder_structure, self.cfg.folder_structure)
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
        self.cfg.folder_structure = self.folder_structure.currentData()
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
        tem_trabalho = False
        if cfg.output_dir.strip() and cfg.db_path().exists():
            tem_trabalho = self._abrir_store(cfg).stats().pending > 0
        problemas = cfg.validate(library_has_work=tem_trabalho)
        if problemas:
            QMessageBox.warning(self, "Configuração incompleta", "\n".join(problemas))
            return
        raiz = Path(cfg.output_dir).expanduser()
        # Anotar antes do mkdir: criar a pasta de uma unidade ausente "cura" a
        # ausência e esconderia que o pen drive não está conectado.
        raiz_existia = raiz.is_dir()
        try:
            raiz.mkdir(parents=True, exist_ok=True)
            store = self._abrir_store(cfg)
        except OSError as erro:
            QMessageBox.critical(self, "Erro", f"Não foi possível usar o diretório:\n{erro}")
            return
        if self._conferir_disco(store, cfg, raiz_existia) < 0 and store.stats().done:
            self.registrar(
                "warn",
                "A pasta de saída não existia: não conferi os arquivos já baixados.",
            )

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

    # ------------------------------------------------------- biblioteca

    def mostrar_baixar(self, ligado: bool = True) -> None:
        if not ligado:
            return  # o par exclusivo desmarcou este botão ao marcar o outro
        if not self.act_pg_baixar.isChecked():
            # Chamado direto (não pelo clique): marcar dispara toggled e volta aqui.
            self.act_pg_baixar.setChecked(True)
            return
        self.mostrar_pagina(self.pagina_baixar)

    def mostrar_biblioteca(self, ligado: bool = True) -> None:
        if not ligado:
            return
        if not self.act_pg_biblioteca.isChecked():
            self.act_pg_biblioteca.setChecked(True)
            return
        cfg = self.config_atual()
        if cfg.output_dir.strip() and cfg.db_path().exists():
            self._abrir_store(cfg)
        self._recarregar_biblioteca()
        self.mostrar_pagina(self.pagina_biblioteca)

    def _recarregar_biblioteca(self) -> None:
        if self.store is None:
            self.pagina_biblioteca.recarregar([], 0)
        else:
            self.pagina_biblioteca.recarregar(
                self.store.list_items(), len(self.store.list_playlists())
            )
        self.atualizar_acoes()

    def _raiz(self) -> Path:
        return Path(self.config_atual().output_dir).expanduser()

    def _rodar_tarefa(self, titulo: str, funcao, ao_terminar) -> None:
        """Tarefa longa de biblioteca numa thread, com progresso na janela."""
        self._ocupado = True
        self.atualizar_acoes()
        self.registrar("info", f"{titulo}...")
        tarefa = TarefaThread(funcao, self)
        tarefa.progresso.connect(self._ao_progresso_tarefa)
        tarefa.terminou.connect(lambda resultado: self._fim_tarefa(ao_terminar, resultado))
        tarefa.falhou.connect(lambda erro: self._falha_tarefa(titulo, erro))
        self._tarefa = tarefa
        tarefa.start()

    @Slot(int, int, str)
    def _ao_progresso_tarefa(self, feito: int, total: int, texto: str) -> None:
        self.barra_faixa.setMaximum(max(1, total))
        self.barra_faixa.setValue(feito)
        self.barra_faixa.setFormat(f"{feito}/{total}")
        self.statusBar().showMessage(texto)

    def _encerrar_tarefa(self) -> None:
        self._ocupado = False
        self.barra_faixa.setMaximum(100)
        self.barra_faixa.setValue(0)
        self.barra_faixa.setFormat("Faixa atual: %p%")
        if self.store is not None:
            self._remontar_arvore()
            self._ao_stats(self.store.stats())
        self._recarregar_biblioteca()

    def _fim_tarefa(self, ao_terminar, resultado) -> None:
        self._encerrar_tarefa()
        ao_terminar(resultado)

    def _falha_tarefa(self, titulo: str, erro: str) -> None:
        self._encerrar_tarefa()
        self.registrar("error", f"{titulo} falhou: {erro}")
        QMessageBox.critical(self, titulo, erro)

    def verificar_arquivos(self) -> None:
        """Conferência completa: movidas, apagadas e postas à mão."""
        store = self._store_para_leitura()
        if store is None:
            return
        raiz = self._raiz()
        if not raiz.is_dir():
            QMessageBox.warning(
                self, "Pasta da biblioteca não encontrada",
                "A pasta da biblioteca não existe agora, então não dá para conferir os "
                "arquivos. Se ela fica num pen drive, conecte-o e tente de novo.",
            )
            return
        self._rodar_tarefa(
            "Verificando a biblioteca",
            lambda progresso, stop: reconcile(store, raiz, full=True, progresso=progresso,
                                              stop=stop),
            self._relatar_verificacao,
        )

    def _relatar_verificacao(self, relatorio) -> None:
        if relatorio.moved:
            self.registrar("info", f"{len(relatorio.moved)} faixa(s) mudaram de pasta; "
                                   "a biblioteca agora aponta para onde estão.")
        if relatorio.adopted:
            self.registrar("ok", f"{len(relatorio.adopted)} arquivo(s) postos à mão "
                                 "entraram na biblioteca.")
        if relatorio.missing:
            self.registrar("warn", f"{len(relatorio.missing)} faixa(s) sumiram do disco "
                                   "e voltaram para a fila.")
        if relatorio.unknown:
            self.registrar(
                "warn",
                f"{len(relatorio.unknown)} arquivo(s) de áudio sem origem conhecida "
                "(não foram baixados por este programa, ou foram sem metadados):",
            )
            for caminho in relatorio.unknown[:10]:
                self.registrar("warn", f"  {caminho}")
        for video_id, caminhos in list(relatorio.duplicates.items())[:10]:
            self.registrar("warn", f"Mesma música em mais de um lugar: {', '.join(caminhos)}")
        if not (relatorio.changed or relatorio.unknown or relatorio.duplicates):
            self.registrar("ok", "Biblioteca conferida: tudo no lugar.")

    def cancelar_tarefa(self) -> None:
        if self._tarefa is not None and self._tarefa.isRunning():
            self._tarefa.stop.set()
            self.registrar("warn", "Cancelamento pedido; terminando o item atual...")

    def identificar_soltos(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        raiz = self._raiz()
        if not raiz.is_dir():
            QMessageBox.warning(
                self, "Pasta da biblioteca não encontrada",
                "A pasta da biblioteca não existe agora. Se ela fica num pen drive, "
                "conecte-o e tente de novo.",
            )
            return

        def tarefa(progresso, stop):
            soltos = find_loose(store, raiz, progresso, stop)
            if stop.is_set() or not soltos:
                return soltos, None
            return soltos, identify(store, raiz, soltos, progresso=progresso, stop=stop)

        self._rodar_tarefa("Identificando arquivos soltos", tarefa, self._relatar_identificacao)

    def _relatar_identificacao(self, resultado) -> None:
        soltos, relatorio = resultado
        if not soltos:
            self.registrar("ok", "Nenhum arquivo solto: tudo na biblioteca tem origem conhecida.")
            return
        if relatorio is None:
            self.registrar("warn", "Identificação cancelada.")
            return
        self.registrar("ok" if relatorio.linked else "info",
                       f"{len(relatorio.linked)} de {len(soltos)} arquivo(s) vinculados a uma URL.")
        for achado in relatorio.duplicates[:10]:
            self.registrar("warn", f"Já na biblioteca, não vinculado: {achado.relpath} "
                                   f"(é '{achado.titulo_achado}')")
        if relatorio.not_found:
            self.registrar("warn", f"{len(relatorio.not_found)} arquivo(s) sem correspondência:")
            for caminho in relatorio.not_found[:10]:
                self.registrar("warn", f"  {caminho}")
        if relatorio.failed:
            self.registrar("error", f"{relatorio.failed} busca(s) falharam (rede ou bloqueio).")
        if relatorio.interrupted:
            self.registrar("warn", "Identificação interrompida antes do fim; "
                                   "rode de novo para continuar do que falta.")
        if relatorio.doubtful:
            aceitas = self._confirmar_duvidas(relatorio.doubtful)
            raiz = self._raiz()
            for achado in aceitas:
                vincular(self.store, raiz, achado)
            self.registrar("info", f"{len(aceitas)} de {len(relatorio.doubtful)} "
                                   "correspondência(s) incertas confirmadas.")
        self._recarregar_biblioteca()

    def _confirmar_duvidas(self, duvidas) -> list:
        """Mostra arquivo × resultado achado; devolve só o que o usuário marcou."""
        dialogo = QDialog(self)
        dialogo.setWindowTitle("Confirmar correspondências")
        layout = QVBoxLayout(dialogo)
        layout.addWidget(QLabel(
            "Estas correspondências são incertas. Marque só as que estiverem certas."
        ))
        tabela = QTableWidget(len(duvidas), 4)
        tabela.setHorizontalHeaderLabels(["Arquivo", "Encontrado no YouTube", "Canal", "Confiança"])
        for linha, achado in enumerate(duvidas):
            marca = QTableWidgetItem(achado.relpath)
            marca.setFlags(marca.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            marca.setCheckState(Qt.CheckState.Unchecked)
            tabela.setItem(linha, 0, marca)
            tabela.setItem(linha, 1, QTableWidgetItem(achado.titulo_achado))
            tabela.setItem(linha, 2, QTableWidgetItem(achado.canal))
            tabela.setItem(linha, 3, QTableWidgetItem(f"{achado.pontos:.0%}"))
        tabela.horizontalHeader().setStretchLastSection(True)
        tabela.resizeColumnsToContents()
        layout.addWidget(tabela)
        botoes = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        botoes.accepted.connect(dialogo.accept)
        botoes.rejected.connect(dialogo.reject)
        layout.addWidget(botoes)
        dialogo.resize(900, 420)
        if dialogo.exec() != QDialog.DialogCode.Accepted:
            return []
        return [
            achado for linha, achado in enumerate(duvidas)
            if tabela.item(linha, 0).checkState() == Qt.CheckState.Checked
        ]

    def reorganizar(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        cfg = self.config_atual()
        if not self._perguntar(
            "Aplicar estrutura atual",
            "Mover os arquivos já baixados para a estrutura de pastas escolhida?",
            "Nada é baixado de novo: os arquivos só mudam de pasta. Faixas fixadas "
            "(postas à mão) ficam onde estão. Pastas que ficarem vazias são removidas.",
            "Mover arquivos",
        ):
            return
        self._rodar_tarefa(
            "Reorganizando a biblioteca",
            lambda progresso, stop: reorganize(
                store, self._raiz(), cfg.folder_structure, cfg.ascii_only,
                cfg.max_component_len, cfg.default_genre, cfg.default_album,
                progresso, stop,
            ),
            lambda r: self.registrar(
                "ok", f"{r.moved} arquivo(s) movidos; {r.kept_pinned} fixado(s) mantidos "
                      "no lugar." + (f" {r.missing} não estavam no disco." if r.missing else "")
            ),
        )

    def _pedir_arquivo(self, salvar: bool) -> str:
        filtro = "Biblioteca do Pen Drive Maker (*.json);;Todos (*)"
        if salvar:
            sugestao = str(self._raiz() / "biblioteca.pdmlib.json")
            caminho, _ = QFileDialog.getSaveFileName(self, "Exportar biblioteca", sugestao, filtro)
        else:
            caminho, _ = QFileDialog.getOpenFileName(self, "Importar biblioteca",
                                                     str(Path.home()), filtro)
        return caminho

    def exportar(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        caminho = self._pedir_arquivo(salvar=True)
        if not caminho:
            return
        try:
            quantidade = export_library(store, Path(caminho))
        except OSError as erro:
            QMessageBox.critical(self, "Exportar", f"Não foi possível gravar:\n{erro}")
            return
        self.registrar("ok", f"{quantidade} faixa(s) exportadas para {caminho}")

    def importar(self) -> None:
        cfg = self.config_atual()
        if not cfg.output_dir.strip():
            QMessageBox.warning(self, "Importar", "Escolha antes a pasta da biblioteca.")
            return
        caminho = self._pedir_arquivo(salvar=False)
        if not caminho:
            return
        self._raiz().mkdir(parents=True, exist_ok=True)
        store = self._abrir_store(cfg)
        try:
            novas, existentes = import_library(store, Path(caminho))
        except ImportError_ as erro:
            QMessageBox.warning(self, "Importar", str(erro))
            return
        self.registrar(
            "ok", f"{novas} faixa(s) importadas"
                  + (f", {existentes} já estavam aqui" if existentes else "")
                  + ". Clique em Iniciar para baixá-las nos mesmos locais."
        )
        self._ao_stats(store.stats())
        self._recarregar_biblioteca()

    def _pedir_pasta(self, titulo: str, inicio: str) -> str:
        return QFileDialog.getExistingDirectory(self, titulo, inicio)

    def _escolher_modo_copia(self) -> Optional[bool]:
        """True = copiar arquivos; False = só a estrutura; None = cancelar."""
        caixa = QMessageBox(self)
        caixa.setWindowTitle("Copiar para outro pen drive")
        caixa.setText("Como reproduzir a biblioteca no destino?")
        caixa.setInformativeText(
            "Copiar os arquivos é muito mais rápido que baixar de novo. Use 'só a "
            "estrutura' quando estes arquivos não estiverem à mão: tudo será baixado "
            "no destino, nos mesmos locais."
        )
        copiar = caixa.addButton("Copiar arquivos", QMessageBox.ButtonRole.AcceptRole)
        estrutura = caixa.addButton("Só a estrutura", QMessageBox.ButtonRole.ActionRole)
        caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caixa.setDefaultButton(copiar)
        caixa.exec()
        if caixa.clickedButton() is copiar:
            return True
        if caixa.clickedButton() is estrutura:
            return False
        return None

    def copiar_para(self) -> None:
        store = self._store_para_leitura()
        if store is None:
            return
        destino = self._pedir_pasta("Pasta de destino (outro pen drive)", str(Path.home()))
        if not destino:
            return
        destino_p = Path(destino).resolve()
        raiz = self._raiz().resolve()
        if destino_p == raiz or raiz in destino_p.parents:
            QMessageBox.warning(self, "Copiar", "O destino não pode ser esta biblioteca "
                                                "nem uma pasta dentro dela.")
            return
        modo = self._escolher_modo_copia()
        if modo is None:
            return

        def ao_terminar(relatorio) -> None:
            self.registrar(
                "ok", f"Biblioteca reproduzida em {destino_p}: {relatorio.copied} arquivo(s) "
                      f"copiados, {relatorio.queued} na fila para baixar."
            )
            for falha in relatorio.failed[:10]:
                self.registrar("error", f"  não copiado: {falha}")
            if self._perguntar(
                "Usar o novo destino?",
                "Trocar a pasta da biblioteca para o destino agora?",
                "Assim, ao clicar em Iniciar, o que faltar é baixado direto lá.",
                "Usar o destino",
            ):
                self.output_edit.setText(str(destino_p))
                self._abrir_store(self.config_atual())
                self._recarregar_biblioteca()

        self._rodar_tarefa(
            "Copiando a biblioteca",
            lambda progresso, stop: copy_library(store, destino_p, modo, progresso, stop),
            ao_terminar,
        )

    def mover_selecao(self) -> None:
        ids = self.pagina_biblioteca.selecionadas()
        if not ids or self.store is None:
            return
        raiz = self._raiz().resolve()
        escolhida = self._pedir_pasta("Mover para qual pasta da biblioteca?", str(raiz))
        if not escolhida:
            return
        pasta = Path(escolhida).resolve()
        if pasta != raiz and raiz not in pasta.parents:
            QMessageBox.warning(self, "Mover", "Escolha uma pasta dentro da biblioteca.")
            return
        relativa = "" if pasta == raiz else pasta.relative_to(raiz).as_posix()
        movidas = move_to_folder(self.store, raiz, ids, relativa)
        self.registrar("ok", f"{movidas} arquivo(s) movidos para '{relativa or '(raiz)'}' "
                             f"e {len(ids)} faixa(s) fixadas lá.")
        self._remontar_arvore()
        self._recarregar_biblioteca()

    def liberar_selecao(self) -> None:
        ids = self.pagina_biblioteca.selecionadas()
        if not ids or self.store is None:
            return
        self.store.set_pinned(ids, False)
        self.registrar("info", f"{len(ids)} faixa(s) liberadas. 'Aplicar estrutura atual' "
                               "as devolve às pastas automáticas.")
        self._recarregar_biblioteca()

    def rebaixar_selecao(self) -> None:
        ids = self.pagina_biblioteca.selecionadas()
        if not ids or self.store is None:
            return
        # Mantém o local de quem está fixado: a faixa volta exatamente para lá.
        self.store.requeue(ids, forget_location=True)
        self.registrar("info", f"{len(ids)} faixa(s) de volta na fila.")
        self._ao_stats(self.store.stats())
        self._recarregar_biblioteca()

    def _confirmar_remocao(self, quantidade: int) -> Optional[bool]:
        """True = apagar arquivos também; False = só tirar da biblioteca; None = cancelar."""
        caixa = QMessageBox(self)
        caixa.setIcon(QMessageBox.Icon.Warning)
        caixa.setWindowTitle("Remover da biblioteca")
        caixa.setText(f"Remover {quantidade} faixa(s) da biblioteca?")
        caixa.setInformativeText(
            "Elas não voltam quando a playlist for atualizada. Se o arquivo continuar "
            "dentro da pasta da biblioteca, a próxima verificação o reconhece de novo."
        )
        apagar = QCheckBox("Apagar também os arquivos do disco")
        apagar.setChecked(True)
        caixa.setCheckBox(apagar)
        remover = caixa.addButton("Remover", QMessageBox.ButtonRole.DestructiveRole)
        caixa.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
        caixa.exec()
        if caixa.clickedButton() is not remover:
            return None
        return apagar.isChecked()

    def remover_selecao(self) -> None:
        ids = self.pagina_biblioteca.selecionadas()
        if not ids or self.store is None:
            return
        apagar = self._confirmar_remocao(len(ids))
        if apagar is None:
            return
        remove_tracks(self.store, self._raiz(), ids, apagar_arquivos=apagar)
        self.registrar("info", f"{len(ids)} faixa(s) removidas da biblioteca"
                               + (" e do disco." if apagar else "."))
        self._remontar_arvore()
        self._ao_stats(self.store.stats())
        self._recarregar_biblioteca()

    def limpar_fila(self) -> None:
        """Recomeça do zero: esquece a playlist, a fila e o histórico."""
        cfg = self.config_atual()
        if not cfg.output_dir.strip() or not cfg.db_path().exists():
            QMessageBox.information(self, "Biblioteca", "Não há biblioteca para esquecer.")
            return
        if not self._perguntar(
            "Esquecer a biblioteca",
            "Esquecer playlists, fila e histórico desta pasta?",
            "Nenhum arquivo é apagado do disco.\n\n"
            "Na próxima vez que clicar em Iniciar, a playlist é buscada de novo e "
            "todas as faixas são baixadas outra vez, já com a estrutura de pastas "
            "escolhida agora. Os arquivos antigos continuam onde estão: apague as "
            "pastas antigas à mão para não ficar com duplicatas — ou use 'Verificar "
            "biblioteca' para que ela os reconheça em vez de baixar de novo.",
            "Esquecer a biblioteca",
        ):
            return
        store = self._abrir_store(cfg)
        store.clear_all()
        self._remontar_arvore()
        self._ao_stats(store.stats())
        self._atualizar_estrutura()
        self.registrar("info", "Biblioteca esquecida. Clique em Iniciar para recomeçar.")
        self._recarregar_biblioteca()

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
                structure=self.folder_structure.currentData(),
            )
        )

    def _conferir_disco(self, store: QueueStore, cfg: AppConfig, raiz_existe: bool) -> int:
        """Devolve à fila o que sumiu do disco. -1 se a pasta de saída não existe.

        Sem esta conferência o banco continua dizendo "baixada" para o que foi
        apagado, e a árvore, lida do banco, mostra pastas que já não existem.
        """
        if not raiz_existe:
            return -1
        # Rápida: só varre as tags se algo sumiu (para achar o que foi movido).
        relatorio = reconcile(store, self._raiz())
        if relatorio.moved:
            self.registrar("info", f"{len(relatorio.moved)} faixa(s) mudaram de pasta; "
                                   "a biblioteca agora aponta para onde estão.")
        if relatorio.missing:
            self.registrar(
                "warn",
                f"{len(relatorio.missing)} faixa(s) estavam marcadas como baixadas, mas o "
                "arquivo não está mais no disco. Voltaram para a fila.",
            )
        self._remontar_arvore()
        self._ao_stats(store.stats())
        return len(relatorio.missing)

    def _carregar_estado_anterior(self) -> None:
        """Ao abrir, mostra a fila da sessão anterior já conferida com o disco."""
        cfg = self.config_atual()
        if not cfg.output_dir.strip() or not cfg.db_path().exists():
            return
        try:
            store = self._abrir_store(cfg)
        except OSError:
            return
        raiz = Path(cfg.output_dir).expanduser()
        self._conferir_disco(store, cfg, raiz.is_dir())

    def _abrir_store(self, cfg: AppConfig) -> QueueStore:
        caminho = cfg.db_path()
        # O arquivo pode ter sido apagado por fora com o banco ainda aberto aqui.
        if self.store is not None and self.store.db_path == caminho and caminho.exists():
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
            QMessageBox.information(self, "Biblioteca", "Nenhuma biblioteca nesta pasta ainda.")
            return None
        return self._abrir_store(cfg)

    def _definir_rodando(self, rodando: bool) -> None:
        self._rodando = rodando
        self.atualizar_acoes()
        for widget in (
            self.url_edit, self.output_edit, self.cookies_mode, self.cookies_file,
            self.cookies_browser, self.audio_format, self.genre_source,
            self.folder_structure,
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
        # A árvore mostra as pastas reais, inclusive as de faixas fixadas.
        try:
            partes = list(Path(caminho).resolve().relative_to(self._raiz().resolve()).parts[:-1])
        except (ValueError, OSError):
            partes = []
        self._acrescentar_na_arvore(partes or ["(raiz)"], 1)
        if self.tree.topLevelItemCount():
            self.tree.expandItem(self.tree.topLevelItem(0))

    def _acrescentar_na_arvore(self, partes, quantidade: int) -> None:
        no = None
        chave: tuple = ()
        for parte in [p for p in partes if p]:
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
        for pastas, quantidade in self.store.folder_tree():
            self._acrescentar_na_arvore(list(pastas) or ["(raiz)"], quantidade)

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
        self._recarregar_biblioteca()

    # ----------------------------------------------------------- janela

    def closeEvent(self, evento) -> None:
        if self._tarefa is not None and self._tarefa.isRunning():
            self._tarefa.stop.set()
            self._tarefa.wait(30000)
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
