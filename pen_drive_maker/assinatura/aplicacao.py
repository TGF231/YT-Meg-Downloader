# -*- coding: utf-8 -*-
"""Preparo do QApplication: idioma, fonte e folha de estilo.

Separado da janela porque tudo aqui é por processo, não por janela, e porque
um autoteste precisa montar o QApplication sem abrir tela nenhuma.
"""

import os

from PySide6.QtCore import QLibraryInfo, QLocale, QTranslator
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import tema


def instalar_traducao(app, pastas_extras=()):
    """Carrega a tradução pt-BR do próprio Qt.

    Sem isso, os botões padrão dos diálogos saem em inglês — "Save",
    "Discard", "Cancel" —, o que destoa de um programa cuja interface é toda
    em português. A tradução cobre tudo que o Qt desenha: caixas de mensagem,
    diálogos não-nativos, menus de contexto de campos de texto.

    Os tradutores precisam ficar vivos enquanto o aplicativo existir; guardá-
    los no próprio `app` evita que o coletor de lixo os leve embora e a
    interface volte ao inglês no meio do uso.
    """
    QLocale.setDefault(QLocale(QLocale.Language.Portuguese,
                               QLocale.Country.Brazil))
    pastas = list(pastas_extras) + [
        QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)]
    carregados = []
    app._tradutores = []
    for nome in ("qtbase_pt_BR", "qt_pt_BR"):
        for pasta in pastas:
            if not pasta or not os.path.isdir(pasta):
                continue
            t = QTranslator(app)
            if t.load(nome, pasta) and app.installTranslator(t):
                app._tradutores.append(t)
                carregados.append(nome)
                break
    return carregados


def preparar(app, nome_app, traducoes=(), fonte_menor=True):
    """Idioma, nome, fonte e folha de estilo inicial. Devolve o app."""
    app.setApplicationName(nome_app)
    app.setOrganizationName(nome_app)
    instalar_traducao(app, traducoes)
    if fonte_menor:
        # um ponto a menos deixa listas e tabelas mais densas
        f = QFont()
        f.setPointSize(max(8, f.pointSize() - 1))
        app.setFont(f)
    app.setStyleSheet(tema.folha_estilo())
    return app


def criar(nome_app, argv=None, traducoes=()):
    """Cria o QApplication já preparado. Use quando não houver um ainda."""
    app = QApplication.instance() or QApplication(list(argv or []))
    return preparar(app, nome_app, traducoes)
