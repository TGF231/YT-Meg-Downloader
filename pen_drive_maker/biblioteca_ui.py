"""Página da biblioteca: tabela de faixas, filtro e tarefas em segundo plano."""

from __future__ import annotations

import threading
from typing import Callable, List

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    QThread,
    Signal,
)
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .assinatura import tema
from .assinatura.comum import definir_papel
from .store import STATUS_DONE, STATUS_EXCLUDED, STATUS_FAILED, STATUS_PENDING, QueueItem

ROTULO_STATUS = {
    STATUS_DONE: "Baixada",
    STATUS_PENDING: "Na fila",
    STATUS_FAILED: "Falhou",
    STATUS_EXCLUDED: "Removida",
}
# Papel de cor de cada status, resolvido no tema a cada pintura.
COR_STATUS = {
    STATUS_DONE: "ok",
    STATUS_PENDING: "warn",
    STATUS_FAILED: "bad",
    STATUS_EXCLUDED: "ink_3",
}

COLUNAS = ("Status", "Título", "Artista", "Álbum", "Gênero", "Playlists", "Local", "Fixada")


class ModeloFaixas(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._faixas: List[QueueItem] = []

    def definir(self, faixas: List[QueueItem]) -> None:
        self.beginResetModel()
        self._faixas = list(faixas)
        self.endResetModel()

    def faixa(self, linha: int) -> QueueItem:
        return self._faixas[linha]

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._faixas)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(COLUNAS)

    def headerData(self, secao, orientacao, papel=Qt.ItemDataRole.DisplayRole):
        if orientacao == Qt.Orientation.Horizontal and papel == Qt.ItemDataRole.DisplayRole:
            return COLUNAS[secao]
        return None

    def data(self, indice, papel=Qt.ItemDataRole.DisplayRole):
        if not indice.isValid():
            return None
        item = self._faixas[indice.row()]
        coluna = indice.column()
        if papel == Qt.ItemDataRole.DisplayRole:
            return (
                ROTULO_STATUS.get(item.status, item.status),
                item.title,
                item.artist or "",
                item.album or "",
                item.genre or "",
                item.playlists,
                item.relpath or "",
                "sim" if item.pinned else "",
            )[coluna]
        if papel == Qt.ItemDataRole.ForegroundRole and coluna == 0:
            # Cor vinda do tema: troca junto com claro/escuro.
            return QColor(getattr(tema.atual(), COR_STATUS.get(item.status, "ink")))
        if papel == Qt.ItemDataRole.ToolTipRole:
            if item.status == STATUS_FAILED and item.last_error:
                return item.last_error
            if item.pinned:
                return "Local fixado: a faixa fica aqui mesmo que a estrutura mude."
            return item.url
        return None

    def repintar(self) -> None:
        if self._faixas:
            self.dataChanged.emit(
                self.index(0, 0), self.index(len(self._faixas) - 1, len(COLUNAS) - 1)
            )


class FiltroFaixas(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._texto = ""
        self._status = ""
        self.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)

    def definir_texto(self, texto: str) -> None:
        self._texto = texto.strip().lower()
        self.invalidateFilter()

    def definir_status(self, status: str) -> None:
        self._status = status
        self.invalidateFilter()

    def filterAcceptsRow(self, linha, pai) -> bool:
        item = self.sourceModel().faixa(linha)
        if self._status == "fixadas":
            if not item.pinned:
                return False
        elif self._status:
            if item.status != self._status:
                return False
        elif item.status == STATUS_EXCLUDED:
            return False  # removidas só aparecem quando pedidas
        if not self._texto:
            return True
        alvo = " ".join(
            (item.title, item.artist or "", item.album or "", item.genre or "",
             item.playlists, item.relpath or "")
        ).lower()
        return all(parte in alvo for parte in self._texto.split())


class PaginaBiblioteca(QWidget):
    """Tudo que está no catálogo, com as ações sobre a seleção logo abaixo."""

    selecao_mudou = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)

        self.resumo = QLabel()
        definir_papel(self.resumo, "secao")
        layout.addWidget(self.resumo)

        filtros = QHBoxLayout()
        self.busca = QLineEdit()
        self.busca.setPlaceholderText("Buscar por título, artista, álbum, playlist ou pasta…")
        self.busca.setClearButtonEnabled(True)
        self.status = QComboBox()
        for rotulo, valor in (
            ("Todas", ""), ("Baixadas", STATUS_DONE), ("Na fila", STATUS_PENDING),
            ("Falharam", STATUS_FAILED), ("Fixadas", "fixadas"), ("Removidas", STATUS_EXCLUDED),
        ):
            self.status.addItem(rotulo, valor)
        filtros.addWidget(self.busca, 1)
        filtros.addWidget(self.status)
        layout.addLayout(filtros)

        self.modelo = ModeloFaixas(self)
        self.filtro = FiltroFaixas(self)
        self.filtro.setSourceModel(self.modelo)
        self.tabela = QTableView()
        self.tabela.setModel(self.filtro)
        self.tabela.setSortingEnabled(True)
        # Começa na ordem de chegada; o usuário ordena clicando no cabeçalho.
        self.tabela.sortByColumn(-1, Qt.SortOrder.AscendingOrder)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        cabecalho = self.tabela.horizontalHeader()
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        cabecalho.setStretchLastSection(False)
        cabecalho.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        for coluna, largura in enumerate((80, 220, 150, 150, 110, 140, 0, 60)):
            if largura:
                self.tabela.setColumnWidth(coluna, largura)
        layout.addWidget(self.tabela, 1)

        self.barra_selecao = QHBoxLayout()
        self.rotulo_selecao = QLabel("Selecione faixas para agir sobre elas.")
        definir_papel(self.rotulo_selecao, "dica")
        self.barra_selecao.addWidget(self.rotulo_selecao)
        self.barra_selecao.addStretch(1)
        layout.addLayout(self.barra_selecao)

        self.busca.textChanged.connect(self.filtro.definir_texto)
        self.status.currentIndexChanged.connect(
            lambda: self.filtro.definir_status(self.status.currentData())
        )
        self.tabela.selectionModel().selectionChanged.connect(self._ao_mudar_selecao)

    def acrescentar_botao(self, acao) -> QToolButton:
        """Botão ligado a uma ação da tabela de ações da janela."""
        botao = QToolButton()
        botao.setDefaultAction(acao)
        botao.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.barra_selecao.addWidget(botao)
        return botao

    def recarregar(self, faixas: List[QueueItem], playlists: int) -> None:
        self.modelo.definir(faixas)
        visiveis = [f for f in faixas if f.status != STATUS_EXCLUDED]
        contagem = {s: 0 for s in ROTULO_STATUS}
        for faixa in faixas:
            contagem[faixa.status] = contagem.get(faixa.status, 0) + 1
        fixadas = sum(1 for f in visiveis if f.pinned)
        self.resumo.setText(
            f"{len(visiveis)} faixa(s) de {playlists} playlist(s) — "
            f"{contagem[STATUS_DONE]} baixada(s), {contagem[STATUS_PENDING]} na fila, "
            f"{contagem[STATUS_FAILED]} com falha, {fixadas} fixada(s)"
        )
        self._ao_mudar_selecao()

    def selecionadas(self) -> List[str]:
        linhas = {self.filtro.mapToSource(i).row() for i in self.tabela.selectionModel().selectedRows()}
        return [self.modelo.faixa(linha).video_id for linha in sorted(linhas)]

    def _ao_mudar_selecao(self, *args) -> None:
        quantidade = len(self.selecionadas())
        self.rotulo_selecao.setText(
            f"{quantidade} faixa(s) selecionada(s)" if quantidade
            else "Selecione faixas para agir sobre elas."
        )
        self.selecao_mudou.emit()


class TarefaThread(QThread):
    """Roda uma tarefa longa de biblioteca sem congelar a janela.

    A função recebe `progresso(feito, total, texto)` e `stop` (Event).
    """

    progresso = Signal(int, int, str)
    terminou = Signal(object)
    falhou = Signal(str)

    def __init__(self, funcao: Callable, parent=None):
        super().__init__(parent)
        self._funcao = funcao
        self.stop = threading.Event()

    def run(self) -> None:
        try:
            resultado = self._funcao(
                lambda feito, total, texto: self.progresso.emit(feito, total, texto), self.stop
            )
        except Exception as erro:  # a janela mostra; a thread não pode morrer calada
            self.falhou.emit(f"{type(erro).__name__}: {erro}")
            return
        self.terminou.emit(resultado)

