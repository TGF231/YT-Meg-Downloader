# -*- coding: utf-8 -*-
"""Assinatura visual compartilhada dos projetos PySide6.

Acento #7d0e0e, tema claro/escuro, barra de título própria sobre janela
nativa e janela base montada a partir de uma tabela de ações.

    from assinatura import aplicacao, tema
    from assinatura.acoes import Acao, BARRA
    from assinatura.janela import JanelaBase
"""

from .acoes import Acao, habilitada
from .comum import como_bool, definir_papel
from .janela import JanelaBase

__all__ = ["Acao", "JanelaBase", "como_bool", "definir_papel", "habilitada"]
