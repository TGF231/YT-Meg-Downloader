# -*- coding: utf-8 -*-
"""`JanelaBase`: a estrutura de janela compartilhada pelos projetos.

O que ela resolve, para a subclasse não repetir:

* barra de título própria com os ganchos nativos, e a volta para a barra do
  Windows quando eles não estiverem disponíveis;
* barra de ferramentas, menus e habilitação montados a partir de uma tabela
  declarativa de ações;
* alternância de tema com a preferência gravada na máquina;
* pilha central de páginas, para trocar de tela sem inventar uma aba;
* dimensionamento inicial conforme a resolução, com densidade compacta em
  tela apertada.

A subclasse declara `APP_NOME`, `ACOES`, `MENUS` e implementa
`montar_conteudo()`. O resto é opcional.
"""

import os

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QMenu, QStackedWidget, QStatusBar, QToolBar,
    QToolButton, QWidget,
)

from . import acoes as tabela_acoes
from . import janela_nativa
from . import tema
from .barra_titulo import BarraTitulo
from .comum import ajustes, como_bool


class JanelaBase(QMainWindow):
    """Janela principal com a assinatura visual da família de ferramentas."""

    APP_NOME = "Aplicativo"
    APP_VERSAO = ""
    ICONE = ""                      # caminho de um .ico, se houver
    ACOES = ()                      # tupla de `acoes.Acao`
    MENUS = ()                      # ((chave, titulo), ...) do botão de menu
    ROTULO_MENUS = "Ferramentas"    # texto do botão que abre os menus
    TAMANHO_ALVO = (1600, 960)      # acima disso a janela não cresce
    TAMANHO_MINIMO = (1000, 640)
    PASTA_DADOS = None              # força as preferências para um .ini

    def __init__(self):
        super().__init__()
        if self.ICONE and os.path.exists(self.ICONE):
            self.setWindowIcon(QIcon(self.ICONE))
        self.resize(*self.TAMANHO_MINIMO)

        self._montar_acoes()
        self._montar_barra_ferramentas()
        self.setStatusBar(QStatusBar())

        self.pilha = QStackedWidget()
        self.setCentralWidget(self.pilha)
        self.montar_conteudo()

        titulo = self.APP_NOME
        if self.APP_VERSAO:
            titulo = "%s v%s" % (titulo, self.APP_VERSAO)
        self.setWindowTitle(titulo)
        self.atualizar_acoes()
        # por último: a barra própria precisa das ações e da toolbar prontas
        self._montar_barra_titulo()

    # ------------------------------------------------------------- ganchos
    def montar_conteudo(self):
        """Monte aqui as páginas da pilha central, com `adicionar_pagina`."""

    def estados(self):
        """Conjunto de chaves de estado ativas agora.

        A tabela de ações consulta isto para habilitar cada linha: uma ação
        com `quando="arquivo"` só fica ativa enquanto "arquivo" estiver neste
        conjunto. Devolver um conjunto vazio deixa ativas só as `SEMPRE`.
        """
        return set()

    def ao_trocar_tema(self):
        """Chamado depois de repintar, para o que a folha de estilo não cobre.

        Cor gravada em QTableWidgetItem, pintura em QPainter e ícone colorido
        são pixels já decididos em Python: a folha de estilo não os alcança.
        Remonte-os aqui, ou o tema escuro vai deixá-los ilegíveis.
        """

    def contexto_da_barra(self):
        """Texto de contexto da barra de título (o que está aberto)."""
        return ""

    def ha_alteracoes(self):
        """True enquanto houver alteração não salva."""
        return False

    # ------------------------------------------------------------- páginas
    def adicionar_pagina(self, widget):
        """Acrescenta uma página à pilha central e devolve o índice dela."""
        return self.pilha.addWidget(widget)

    def mostrar_pagina(self, indice_ou_widget):
        if isinstance(indice_ou_widget, QWidget):
            self.pilha.setCurrentWidget(indice_ou_widget)
        else:
            self.pilha.setCurrentIndex(indice_ou_widget)

    # --------------------------------------------------------------- ações
    def _montar_acoes(self):
        """Cria e conecta cada linha da tabela; acrescenta a ação de tema."""
        self.act_tema = QAction("Tema escuro", self)
        self.act_tema.triggered.connect(self.alternar_tema)

        for a in self.ACOES:
            acao = QAction(a.rotulo, self)
            if a.atalho:
                padrao = getattr(QKeySequence.StandardKey, a.atalho, None)
                acao.setShortcut(QKeySequence(padrao) if padrao is not None
                                 else QKeySequence(a.atalho))
            metodo = getattr(self, a.metodo)
            if a.alternavel:
                acao.setCheckable(True)
                acao.toggled.connect(metodo)
            elif a.argumento is not None:
                acao.triggered.connect(
                    lambda _m=False, f=metodo, arg=a.argumento: f(arg))
            else:
                acao.triggered.connect(lambda _m=False, f=metodo: f())
            setattr(self, a.atributo, acao)
        self._sincronizar_rotulo_tema()

    def _montar_barra_ferramentas(self):
        """Barra na ordem da tabela, com os menus agrupados num só botão."""
        barra = QToolBar("Principal")
        barra.setMovable(False)
        self.addToolBar(barra)
        self.toolbar = barra

        self._menus = {}
        menu_raiz = QMenu(self)
        for chave, titulo in self.MENUS:
            self._menus[chave] = menu_raiz.addMenu(titulo)

        for a in self.ACOES:
            acao = getattr(self, a.atributo)
            if a.onde == tabela_acoes.BARRA:
                if a.separador:
                    barra.addSeparator()
                barra.addAction(acao)
            elif a.onde in self._menus:
                sub = self._menus[a.onde]
                if a.separador and not sub.isEmpty():
                    sub.addSeparator()
                sub.addAction(acao)

        if self._menus:
            botao = QToolButton(self)
            # sem "▾" no texto: o Qt já desenha o indicador do menu, e o
            # caractere literal produz duas setas
            botao.setText(self.ROTULO_MENUS)
            botao.setMenu(menu_raiz)
            botao.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            botao.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            barra.addSeparator()
            barra.addWidget(botao)
            self._botao_menus = botao

        barra.addSeparator()
        barra.addAction(self.act_tema)

    def atualizar_acoes(self):
        """Liga e desliga cada ação conforme `estados()`. Chame ao mudar."""
        ativos = self.estados()
        for a in self.ACOES:
            getattr(self, a.atributo).setEnabled(
                tabela_acoes.habilitada(a, ativos))

    # ----------------------------------------------------------- dimensões
    def dimensionar_para_a_tela(self):
        """Tamanho inicial conforme a resolução disponível.

        Numa tela folgada a janela abre grande e centralizada; numa tela
        apertada ela **abre maximizada** e o tema entra em densidade compacta
        — alguns pixels de padding a menos por linha valem uma coluna inteira
        a mais visível.
        """
        tela = QApplication.primaryScreen()
        if tela is None:
            return
        disp = tela.availableGeometry()
        alvo_l, alvo_a = self.TAMANHO_ALVO
        if disp.width() < alvo_l or disp.height() < alvo_a:
            tema.definir_densidade("compacta")
            app = QApplication.instance()
            if app is not None:
                app.setStyleSheet(tema.folha_estilo())
            self.showMaximized()
            return
        largura = min(alvo_l, int(disp.width() * 0.88))
        altura = min(alvo_a, int(disp.height() * 0.88))
        self.resize(largura, altura)
        self.move(disp.left() + (disp.width() - largura) // 2,
                  disp.top() + (disp.height() - altura) // 2)

    # ---------------------------------------------------------------- tema
    def _preferencias(self):
        return ajustes(self.APP_NOME, self.PASTA_DADOS)

    def _sincronizar_rotulo_tema(self):
        if hasattr(self, "act_tema"):
            self.act_tema.setText(
                "Tema claro" if tema.e_escuro() else "Tema escuro")

    def alternar_tema(self):
        """Troca a paleta, repinta tudo e grava a preferência na máquina."""
        tema.definir_escuro(not tema.e_escuro())
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(tema.folha_estilo())
        self._sincronizar_rotulo_tema()
        self._preferencias().setValue("tema_escuro", tema.e_escuro())
        self.ao_trocar_tema()
        self.statusBar().showMessage("Tema %s aplicado." % tema.atual().nome,
                                     4000)

    def carregar_tema_salvo(self):
        """Aplica a preferência gravada. Chame antes de `show()`."""
        escuro = como_bool(self._preferencias().value("tema_escuro"), False)
        tema.definir_escuro(escuro)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(tema.folha_estilo())
        self._sincronizar_rotulo_tema()
        return escuro

    # ----------------------------------------------------- barra de título
    def _montar_barra_titulo(self):
        """Põe a barra própria no lugar da do Windows, quando der.

        `setMenuWidget` ocupa exatamente a faixa acima das barras de
        ferramentas. Se `janela_nativa.aplicar` falhar (não é Windows, ctypes
        bloqueado, a escotilha de ambiente ligada), nada é montado e o
        aplicativo segue com a barra nativa: enfeite não pode impedir de
        abrir.
        """
        self.barra_titulo = None
        self._barra_aplicada = False
        if not janela_nativa.disponivel():
            return
        # o widget pode ser montado agora (é só Qt); os ganchos nativos ficam
        # para o primeiro showEvent, quando a janela já existe de verdade
        self.barra_titulo = BarraTitulo(self, titulo=self.APP_NOME,
                                        icone=self.ICONE)
        self.setMenuWidget(self.barra_titulo)
        self.sincronizar_barra()

    def sincronizar_barra(self):
        """Atualiza contexto e indicador de alterações na barra própria."""
        if not getattr(self, "barra_titulo", None):
            return
        self.barra_titulo.sincronizar(contexto=self.contexto_da_barra(),
                                      alterado=bool(self.ha_alteracoes()))

    def showEvent(self, evento):
        """Aplica os ganchos nativos uma única vez, já com a janela criada.

        Fazer isso no `__init__` força `winId()` no meio da construção do
        QMainWindow, e o SetWindowPos(FRAMECHANGED) reentra na montagem — o
        processo morre sem traceback, por ser uma falha nativa.
        """
        super().showEvent(evento)
        if self.barra_titulo is None or self._barra_aplicada:
            return
        self._barra_aplicada = True
        if not janela_nativa.aplicar(self):
            # sem os ganchos, a barra própria conviveria com a nativa
            self.setMenuWidget(None)
            self.barra_titulo = None
            self.statusBar().showMessage(
                "Barra de título própria indisponível; usando a do Windows.",
                6000)

    def nativeEvent(self, tipo, mensagem):
        """Delega as mensagens de moldura para o módulo nativo.

        **Nunca chame `super().nativeEvent(...)` aqui.** Para "não tratei",
        devolva `(False, 0)`: o Qt segue com o tratamento padrão.
        """
        if bytes(tipo) != b"windows_generic_MSG":
            return False, 0
        # só interceptamos depois de aplicar() ter preparado a janela: tratar
        # WM_NCCALCSIZE durante a criação do HWND derruba o processo
        if (getattr(self, "barra_titulo", None) is not None
                and getattr(self, "_barra_aplicada", False)):
            tratado, resultado = janela_nativa.tratar(
                self, int(mensagem), BarraTitulo.ALTURA,
                self.barra_titulo.area_arrastavel)
            if tratado:
                return True, resultado
        return False, 0

    def changeEvent(self, evento):
        # trocar entre maximizada e restaurada muda o ícone do botão
        super().changeEvent(evento)
        if evento.type() == QEvent.Type.WindowStateChange:
            if getattr(self, "barra_titulo", None):
                self.barra_titulo.sincronizar()
