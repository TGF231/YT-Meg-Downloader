# -*- coding: utf-8 -*-
"""Utilidades da interface: papéis visuais, preferências e coerção de bool."""

import os

from PySide6.QtCore import QSettings


def definir_papel(widget, papel):
    """Troca a propriedade dinâmica `role` e repinta o widget.

    Mudar uma propriedade usada em seletor de QSS **não** repinta sozinho: o
    Qt já resolveu o estilo daquele widget. Sem o unpolish/polish o rótulo
    continua com a cor antiga e parece que a folha de estilo está errada.
    """
    widget.setProperty("role", papel)
    estilo = widget.style()
    estilo.unpolish(widget)
    estilo.polish(widget)
    widget.update()
    return widget


def como_bool(valor, padrao=False):
    """Converte o que o QSettings devolve em bool.

    No PySide6, `QSettings.value()` não aceita o argumento `type=` que o PyQt
    aceita, e num arquivo .ini o valor volta como a **string** "true" — que é
    verdadeira em Python mesmo quando vale "false". Ler preferência de tema
    sem passar por aqui é a forma mais fácil de o tema escuro nunca desligar.
    """
    if valor is None:
        return padrao
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    return str(valor).strip().lower() in ("1", "true", "yes", "sim", "on")


def ajustes(nome_app, pasta_dados=None):
    """Preferências da interface (tema, última escolha, geometria).

    Normalmente no registro do Windows. Com uma pasta de dados informada — o
    que a suíte de testes deve fazer —, vão para um `.ini` dentro dela: um
    teste que alterna o tema não pode trocar a preferência de quem usa o
    programa.
    """
    if pasta_dados:
        return QSettings(os.path.join(pasta_dados, "preferencias.ini"),
                         QSettings.Format.IniFormat)
    return QSettings(nome_app, nome_app)
