# -*- coding: utf-8 -*-
"""Ícones desenhados em runtime para a folha de estilo.

O Qt Style Sheets não aceita data URI, e substituir o fundo do indicador de um
QCheckBox descarta o desenho nativo do "✓". Em vez de arrastar arquivos .png
pelo projeto, desenhamos os poucos ícones necessários e gravamos numa pasta
temporária, referenciada pelo QSS.

Os arquivos são nomeados pela paleta, para que trocar de tema não reaproveite
um ícone com a cor do tema anterior.
"""

import os
import tempfile

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF

# Pasta temporária por aplicativo: dois programas desta família rodando lado a
# lado não podem sobrescrever o ícone um do outro com a paleta do vizinho.
PREFIXO = "assinatura_icones"

_cache = {}


def definir_prefixo(nome):
    """Define o nome da pasta temporária. Chame uma vez, antes da 1ª pintura."""
    global PREFIXO
    PREFIXO = nome or PREFIXO
    _cache.clear()
    return PREFIXO


def _pasta():
    caminho = os.path.join(tempfile.gettempdir(), PREFIXO)
    os.makedirs(caminho, exist_ok=True)
    return caminho


def _gravar(nome, pixmap):
    """Devolve o caminho no formato que o QSS entende (barras normais)."""
    caminho = os.path.join(_pasta(), nome)
    pixmap.save(caminho, "PNG")
    return caminho.replace("\\", "/")


def _check(tam, cor):
    pixmap = QPixmap(tam, tam)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    caneta = QPen(QColor(cor))
    caneta.setWidthF(tam * 0.16)
    caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
    caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(caneta)
    # traço do "✓" em coordenadas proporcionais ao tamanho
    p.drawPolyline(QPolygonF([
        QPointF(tam * 0.22, tam * 0.52),
        QPointF(tam * 0.42, tam * 0.72),
        QPointF(tam * 0.78, tam * 0.28),
    ]))
    p.end()
    return pixmap


def _chevron(larg, alt, cor):
    pixmap = QPixmap(larg, alt)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    caneta = QPen(QColor(cor))
    caneta.setWidthF(1.6)
    caneta.setCapStyle(Qt.PenCapStyle.RoundCap)
    caneta.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(caneta)
    p.drawPolyline(QPolygonF([
        QPointF(larg * 0.22, alt * 0.38),
        QPointF(larg * 0.50, alt * 0.66),
        QPointF(larg * 0.78, alt * 0.38),
    ]))
    p.end()
    return pixmap


def caminhos(paleta):
    """Gera (uma vez por paleta) e devolve os caminhos dos ícones."""
    guardado = _cache.get(paleta.nome)
    if guardado is not None:
        return guardado

    suf = paleta.nome
    art = {
        "check": _gravar("check_%s.png" % suf, _check(14, paleta.on_accent)),
        "chevron": _gravar("chevron_%s.png" % suf,
                           _chevron(12, 12, paleta.ink_2)),
        "chevron_off": _gravar("chevron_off_%s.png" % suf,
                               _chevron(12, 12, paleta.ink_3)),
    }
    _cache[paleta.nome] = art
    return art
