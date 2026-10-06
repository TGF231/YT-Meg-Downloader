# -*- coding: utf-8 -*-
"""Tabela única das ações da janela: barra, menus e habilitação.

O padrão que esta tabela substitui: cada ação existindo em quatro lugares —
criada num método, conectada noutro, habilitada numa lista e desabilitada
noutra. Esquecer um deles derruba a janela na criação ou deixa a ação fora de
todos os menus sem ninguém notar.

Aqui uma ação é **uma linha**. A janela cria, conecta, põe no lugar certo e
habilita tudo a partir dela. Vale escrever um teste que confira que cada
linha aponta para um método que existe — é barato e pega o erro antes da
janela abrir.
"""

from dataclasses import dataclass

# quando a ação fica habilitada
SEMPRE = "sempre"          # independe do estado do aplicativo

# onde ela aparece
BARRA = "barra"            # barra de ferramentas, fora do menu
OCULTA = ""                # só atalho de teclado ou chamada interna


@dataclass(frozen=True)
class Acao:
    atributo: str               # nome do atributo na janela (self.act_...)
    rotulo: str
    metodo: str                 # método da janela chamado ao acionar
    quando: str = SEMPRE        # chave de estado exigida (ver estados())
    onde: str = BARRA           # BARRA, OCULTA ou a chave de um menu
    atalho: str = ""            # nome de QKeySequence.StandardKey, ou texto
    argumento: object = None    # passado ao método, se houver
    alternavel: bool = False    # botão liga/desliga (toggled)
    separador: bool = False     # separador ANTES dela


def habilitada(acao, estados):
    """`estados` é o conjunto devolvido por `JanelaBase.estados()`."""
    return acao.quando == SEMPRE or acao.quando in estados
