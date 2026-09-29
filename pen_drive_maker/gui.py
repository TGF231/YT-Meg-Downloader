"""Interface gráfica em PySide6."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

from PySide6.QtCore import QObject, QSettings, QThread, Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
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

from .config import AUDIO_FORMATS, BROWSERS, AppConfig
from .downloader import DownloadEngine, EngineCallbacks
from .organizer import TrackTags, sanitize_component
from .store import QueueStats, QueueStore

ORG_NAME = "PenDriveMaker"
APP_NAME = "YouTube Music Pen Drive Maker"

LOG_COLORS = {
    "debug": "#8a8a8a",
    "info": "#d6d6d6",
    "ok": "#6fcf6f",
    "warn": "#e0b341",
    "error": "#ef6262",
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


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1080, 780)

        self.settings = QSettings(ORG_NAME, APP_NAME)
        self.store: Optional[QueueStore] = None
        self.engine: Optional[DownloadEngine] = None
        self.thread: Optional[EngineThread] = None
        self.bridge = EngineBridge()
        self._tree_nodes: Dict[tuple, QTreeWidgetItem] = {}

        self._build_ui()
        self._connect_bridge()
        self._load_settings()
        self._update_controls(running=False)
        self._check_ffmpeg()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)

        outer.addWidget(self._build_source_box())

        options_row = QHBoxLayout()
        options_row.addWidget(self._build_pace_box(), 1)
        options_row.addWidget(self._build_audio_box(), 1)
        outer.addLayout(options_row)

        outer.addLayout(self._build_button_row())
        outer.addWidget(self._build_progress_box())

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._build_tree_box())
        splitter.addWidget(self._build_log_box())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([280, 280])
        outer.addWidget(splitter, 1)

        self.setCentralWidget(central)
        self.statusBar().showMessage("Ocioso")

    def _build_source_box(self) -> QGroupBox:
        box = QGroupBox("Playlist e destino")
        form = QFormLayout(box)

        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText(
            "https://music.youtube.com/playlist?list=..."
        )
        form.addRow("URL da playlist:", self.url_edit)

        out_row = QHBoxLayout()
        self.output_edit = QLineEdit()
        browse = QPushButton("Escolher...")
        browse.clicked.connect(self._choose_output_dir)
        out_row.addWidget(self.output_edit, 1)
        out_row.addWidget(browse)
        form.addRow("Diretório de saída:", out_row)

        cookies_row = QHBoxLayout()
        self.cookies_mode = QComboBox()
        self.cookies_mode.addItem("Nenhum", "none")
        self.cookies_mode.addItem("Arquivo cookies.txt", "file")
        self.cookies_mode.addItem("Direto do navegador", "browser")
        self.cookies_mode.currentIndexChanged.connect(self._update_cookie_widgets)
        self.cookies_file = QLineEdit()
        self.cookies_file.setPlaceholderText("caminho do cookies.txt (formato Netscape)")
        self.cookies_browse = QPushButton("Procurar...")
        self.cookies_browse.clicked.connect(self._choose_cookies_file)
        self.cookies_browser = QComboBox()
        self.cookies_browser.addItems(BROWSERS)
        cookies_row.addWidget(self.cookies_mode)
        cookies_row.addWidget(self.cookies_file, 1)
        cookies_row.addWidget(self.cookies_browse)
        cookies_row.addWidget(self.cookies_browser)
        form.addRow("Cookies:", cookies_row)

        hint = QLabel(
            "Cookies de uma conta logada reduzem muito a chance de bloqueio em "
            "playlists longas."
        )
        hint.setStyleSheet("color: #8a8a8a;")
        form.addRow("", hint)
        return box

    def _build_pace_box(self) -> QGroupBox:
        box = QGroupBox("Ritmo e proteção contra bloqueio")
        form = QFormLayout(box)

        self.min_interval = QDoubleSpinBox()
        self.min_interval.setRange(0.0, 3600.0)
        self.min_interval.setDecimals(1)
        self.min_interval.setSuffix(" s")
        form.addRow("Intervalo mínimo entre downloads:", self.min_interval)

        self.jitter = QDoubleSpinBox()
        self.jitter.setRange(0.0, 3600.0)
        self.jitter.setDecimals(1)
        self.jitter.setSuffix(" s")
        form.addRow("Jitter aleatório (somado):", self.jitter)

        self.max_retries = QSpinBox()
        self.max_retries.setRange(1, 20)
        form.addRow("Tentativas por faixa:", self.max_retries)

        self.max_consecutive = QSpinBox()
        self.max_consecutive.setRange(1, 50)
        form.addRow("Falhas seguidas = bloqueio:", self.max_consecutive)

        self.block_backoff = QDoubleSpinBox()
        self.block_backoff.setRange(5.0, 7200.0)
        self.block_backoff.setDecimals(0)
        self.block_backoff.setSuffix(" s")
        form.addRow("Backoff inicial no bloqueio:", self.block_backoff)

        self.max_blocks = QSpinBox()
        self.max_blocks.setRange(1, 50)
        form.addRow("Bloqueios até encerrar:", self.max_blocks)
        return box

    def _build_audio_box(self) -> QGroupBox:
        box = QGroupBox("Áudio e organização de pastas")
        form = QFormLayout(box)

        self.audio_format = QComboBox()
        for key, label in AUDIO_FORMATS.items():
            self.audio_format.addItem(label, key)
        form.addRow("Formato de saída:", self.audio_format)

        self.embed_metadata = QCheckBox("Gravar metadados na tag do arquivo")
        self.embed_thumbnail = QCheckBox("Embutir capa do álbum")
        form.addRow("", self.embed_metadata)
        form.addRow("", self.embed_thumbnail)

        self.ascii_only = QCheckBox("Somente ASCII nos nomes (recomendado para FAT32)")
        form.addRow("", self.ascii_only)

        self.default_genre = QLineEdit()
        form.addRow("Pasta para gênero ausente:", self.default_genre)

        self.default_album = QLineEdit()
        form.addRow("Pasta para álbum ausente:", self.default_album)

        return box

    def _build_button_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        self.start_btn = QPushButton("Iniciar / Retomar")
        self.start_btn.clicked.connect(lambda: self._on_start(False))
        self.pause_btn = QPushButton("Pausar")
        self.pause_btn.clicked.connect(lambda: self._on_pause_toggle())
        self.stop_btn = QPushButton("Parar")
        self.stop_btn.clicked.connect(lambda: self._on_stop())
        self.requeue_btn = QPushButton("Reenfileirar falhas")
        self.requeue_btn.clicked.connect(lambda: self._on_requeue())
        self.reextract_btn = QPushButton("Reextrair playlist")
        self.reextract_btn.clicked.connect(lambda: self._on_start(force_reextract=True))
        for button in (
            self.start_btn,
            self.pause_btn,
            self.stop_btn,
            self.requeue_btn,
            self.reextract_btn,
        ):
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _build_progress_box(self) -> QGroupBox:
        box = QGroupBox("Progresso")
        layout = QVBoxLayout(box)

        self.counters = QLabel("Total: 0 | Baixadas: 0 | Falhas: 0 | Na fila: 0")
        font = QFont()
        font.setBold(True)
        self.counters.setFont(font)
        layout.addWidget(self.counters)

        self.playlist_bar = QProgressBar()
        self.playlist_bar.setFormat("Playlist: %p%")
        layout.addWidget(self.playlist_bar)

        self.file_bar = QProgressBar()
        self.file_bar.setFormat("Faixa atual: %p%")
        layout.addWidget(self.file_bar)

        self.status_label = QLabel("Ocioso")
        layout.addWidget(self.status_label)

        self.structure_label = QLabel()
        self.structure_label.setStyleSheet("color: #8a8a8a;")
        self.structure_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.structure_label)
        self.output_edit.textChanged.connect(self._refresh_structure_label)
        return box

    def _build_tree_box(self) -> QGroupBox:
        box = QGroupBox("Estrutura de pastas criada")
        layout = QVBoxLayout(box)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Gênero / Artista / Álbum", "Faixas"])
        self.tree.setColumnWidth(0, 520)
        layout.addWidget(self.tree)
        return box

    def _build_log_box(self) -> QGroupBox:
        box = QGroupBox("Log")
        layout = QVBoxLayout(box)

        controls = QHBoxLayout()
        self.verbose_log = QCheckBox("Mostrar mensagens de depuração")
        clear_btn = QPushButton("Limpar log")
        clear_btn.clicked.connect(lambda: self.log_view.clear())
        controls.addWidget(self.verbose_log)
        controls.addStretch(1)
        controls.addWidget(clear_btn)
        layout.addLayout(controls)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setStyleSheet(
            "background:#1d1f21; color:#d6d6d6; font-family: monospace;"
        )
        layout.addWidget(self.log_view)
        return box

    # ------------------------------------------------------------- settings

    def _load_settings(self) -> None:
        cfg = AppConfig.from_dict(
            {
                key: self.settings.value(key)
                for key in AppConfig().to_dict()
                if self.settings.contains(key)
            }
        )
        self.url_edit.setText(str(cfg.playlist_url))
        self.output_edit.setText(str(cfg.output_dir))
        self.min_interval.setValue(float(cfg.min_interval))
        self.jitter.setValue(float(cfg.jitter))
        self.max_retries.setValue(int(cfg.max_retries))
        self.max_consecutive.setValue(int(cfg.max_consecutive_failures))
        self.block_backoff.setValue(float(cfg.block_base_backoff))
        self.max_blocks.setValue(int(cfg.max_block_events))
        self._select_data(self.audio_format, cfg.audio_format)
        self.embed_metadata.setChecked(_as_bool(cfg.embed_metadata))
        self.embed_thumbnail.setChecked(_as_bool(cfg.embed_thumbnail))
        self.ascii_only.setChecked(_as_bool(cfg.ascii_only))
        self.default_genre.setText(str(cfg.default_genre))
        self.default_album.setText(str(cfg.default_album))
        self._select_data(self.cookies_mode, cfg.cookies_mode)
        self.cookies_file.setText(str(cfg.cookies_file))
        index = self.cookies_browser.findText(str(cfg.cookies_browser))
        self.cookies_browser.setCurrentIndex(max(0, index))
        self._update_cookie_widgets()
        self._refresh_structure_label()

    def _save_settings(self) -> None:
        for key, value in self.current_config().to_dict().items():
            self.settings.setValue(key, value)
        self.settings.sync()

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def current_config(self) -> AppConfig:
        cfg = AppConfig(
            playlist_url=self.url_edit.text().strip(),
            output_dir=self.output_edit.text().strip(),
            min_interval=self.min_interval.value(),
            jitter=self.jitter.value(),
            audio_format=self.audio_format.currentData(),
            embed_thumbnail=self.embed_thumbnail.isChecked(),
            embed_metadata=self.embed_metadata.isChecked(),
            cookies_mode=self.cookies_mode.currentData(),
            cookies_file=self.cookies_file.text().strip(),
            cookies_browser=self.cookies_browser.currentText(),
            default_genre=self.default_genre.text().strip() or "Sem Genero",
            default_album=self.default_album.text().strip() or "Singles",
            ascii_only=self.ascii_only.isChecked(),
            max_retries=self.max_retries.value(),
            max_consecutive_failures=self.max_consecutive.value(),
            block_base_backoff=self.block_backoff.value(),
            max_block_events=self.max_blocks.value(),
        )
        cfg.normalize()
        return cfg

    # --------------------------------------------------------------- ações

    def _choose_output_dir(self) -> None:
        start = self.output_edit.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Diretório de saída", start)
        if chosen:
            self.output_edit.setText(chosen)

    def _choose_cookies_file(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Arquivo de cookies", str(Path.home()), "Cookies (*.txt);;Todos (*)"
        )
        if chosen:
            self.cookies_file.setText(chosen)

    def _update_cookie_widgets(self) -> None:
        mode = self.cookies_mode.currentData()
        self.cookies_file.setVisible(mode == "file")
        self.cookies_browse.setVisible(mode == "file")
        self.cookies_browser.setVisible(mode == "browser")

    def _refresh_structure_label(self) -> None:
        root = self.output_edit.text().strip() or "<saída>"
        self.structure_label.setText(
            f"Estrutura de destino:  {root}/Gênero/Artista/Álbum/NN - Título.ext"
        )

    def _check_ffmpeg(self) -> None:
        if shutil.which("ffmpeg") is None:
            self._append_log(
                "warn",
                "ffmpeg não encontrado no PATH: conversão de formato, metadados e capa "
                "serão ignorados pelo yt-dlp. Instale o ffmpeg para o resultado completo.",
            )

    def _open_store(self, cfg: AppConfig) -> QueueStore:
        db_path = cfg.db_path()
        if self.store is not None and self.store.db_path == db_path:
            return self.store
        if self.store is not None:
            self.store.close()
        self.store = QueueStore(db_path)
        self._rebuild_tree()
        self._on_stats(self.store.stats())
        return self.store

    def _on_start(self, force_reextract: bool = False) -> None:
        if self.thread is not None and self.thread.isRunning():
            return
        cfg = self.current_config()
        problems = cfg.validate()
        if problems:
            QMessageBox.warning(self, "Configuração incompleta", "\n".join(problems))
            return

        try:
            Path(cfg.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
            store = self._open_store(cfg)
        except OSError as exc:
            QMessageBox.critical(self, "Erro", f"Não foi possível usar o diretório:\n{exc}")
            return

        self._save_settings()
        self.engine = DownloadEngine(cfg, store, self.bridge.callbacks())
        self.thread = EngineThread(self.engine, force_reextract)
        self.thread.finished.connect(self._on_thread_finished)
        self._append_log("info", f"Fila persistente: {cfg.db_path()}")
        self._update_controls(running=True)
        self.thread.start()

    def _on_pause_toggle(self) -> None:
        if self.engine is None:
            return
        if self.engine.is_paused:
            self.engine.resume()
            self.pause_btn.setText("Pausar")
        else:
            self.engine.pause()
            self.pause_btn.setText("Retomar")

    def _on_stop(self) -> None:
        if self.engine is not None:
            self.engine.stop()
            self.stop_btn.setEnabled(False)
            self._append_log("warn", "Parada solicitada; terminando a faixa atual...")

    def _on_requeue(self) -> None:
        if self.thread is not None and self.thread.isRunning():
            QMessageBox.information(
                self, "Em execução", "Pare o download antes de reenfileirar as falhas."
            )
            return
        cfg = self.current_config()
        if not cfg.output_dir.strip():
            QMessageBox.warning(self, "Configuração", "Escolha o diretório de saída.")
            return
        if not cfg.db_path().exists():
            QMessageBox.information(self, "Fila", "Nenhuma fila encontrada nesse diretório.")
            return
        store = self._open_store(cfg)
        count = store.requeue_failed()
        self._append_log("info", f"{count} faixa(s) com falha voltaram para a fila.")
        self._on_stats(store.stats())

    # --------------------------------------------------------------- sinais

    def _connect_bridge(self) -> None:
        self.bridge.log.connect(self._append_log)
        self.bridge.status.connect(self._on_status)
        self.bridge.stats.connect(self._on_stats)
        self.bridge.item_done.connect(self._on_item_done)
        self.bridge.file_progress.connect(self._on_file_progress)
        self.bridge.finished.connect(self._on_engine_finished)

    @Slot(str, str)
    def _append_log(self, level: str, message: str) -> None:
        if level == "debug" and not self.verbose_log.isChecked():
            return
        color = LOG_COLORS.get(level, "#d6d6d6")
        safe = (
            message.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )
        self.log_view.appendHtml(f'<span style="color:{color};">{safe}</span>')

    @Slot(str)
    def _on_status(self, text: str) -> None:
        self.status_label.setText(text)
        self.statusBar().showMessage(text)

    @Slot(object)
    def _on_stats(self, stats: QueueStats) -> None:
        self.counters.setText(
            f"Total: {stats.total} | Baixadas: {stats.done} | "
            f"Falhas: {stats.failed} | Na fila: {stats.pending}"
        )
        self.playlist_bar.setMaximum(max(1, stats.total))
        self.playlist_bar.setValue(stats.done + stats.failed)

    @Slot(float, str)
    def _on_file_progress(self, percent: float, text: str) -> None:
        self.file_bar.setValue(int(percent))
        self.file_bar.setFormat(f"Faixa atual: {text}")

    @Slot(str, object)
    def _on_item_done(self, path: str, tags: TrackTags) -> None:
        ascii_only = self.ascii_only.isChecked()
        parts = [sanitize_component(p, ascii_only) for p in tags.folder_parts()]
        node = None
        key: tuple = ()
        for part in parts:
            key = key + (part,)
            existing = self._tree_nodes.get(key)
            if existing is None:
                existing = QTreeWidgetItem([part, "0"])
                self._tree_nodes[key] = existing
                if node is None:
                    self.tree.addTopLevelItem(existing)
                else:
                    node.addChild(existing)
            node = existing
            node.setText(1, str(int(node.text(1) or 0) + 1))
        if self.tree.topLevelItemCount():
            self.tree.expandItem(self.tree.topLevelItem(0))

    @Slot(str)
    def _on_engine_finished(self, reason: str) -> None:
        # O motor já registrou o motivo no log; aqui só reiniciamos os indicadores.
        self.statusBar().showMessage(reason)
        self.file_bar.setValue(0)
        self.file_bar.setFormat("Faixa atual: %p%")

    def _on_thread_finished(self) -> None:
        self._update_controls(running=False)
        self.pause_btn.setText("Pausar")
        if self.store is not None:
            self._on_stats(self.store.stats())

    def _update_controls(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.reextract_btn.setEnabled(not running)
        self.requeue_btn.setEnabled(not running)
        self.pause_btn.setEnabled(running)
        self.stop_btn.setEnabled(running)
        for widget in (
            self.url_edit,
            self.output_edit,
            self.cookies_mode,
            self.cookies_file,
            self.cookies_browser,
            self.audio_format,
            self.ascii_only,
            self.default_genre,
            self.default_album,
        ):
            widget.setEnabled(not running)

    def _rebuild_tree(self) -> None:
        """Reconstrói a árvore de pastas a partir do que já está no banco."""
        self.tree.clear()
        self._tree_nodes.clear()
        if self.store is None:
            return
        for genre, artist, album, count in self.store.done_tree():
            node = None
            key: tuple = ()
            for part in (genre or "?", artist or "?", album or "?"):
                key = key + (part,)
                existing = self._tree_nodes.get(key)
                if existing is None:
                    existing = QTreeWidgetItem([part, "0"])
                    self._tree_nodes[key] = existing
                    if node is None:
                        self.tree.addTopLevelItem(existing)
                    else:
                        node.addChild(existing)
                node = existing
                node.setText(1, str(int(node.text(1) or 0) + count))

    # --------------------------------------------------------------- janela

    def closeEvent(self, event) -> None:
        if self.thread is not None and self.thread.isRunning():
            answer = QMessageBox.question(
                self,
                "Sair",
                "Um download está em andamento. Parar e sair?\n"
                "A fila fica salva e pode ser retomada depois.",
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            if self.engine is not None:
                self.engine.stop()
            self.thread.wait(30000)
        self._save_settings()
        if self.store is not None:
            self.store.close()
        event.accept()


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    window = MainWindow()
    window.show()
    return app.exec()
