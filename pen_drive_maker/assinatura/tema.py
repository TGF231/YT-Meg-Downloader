# -*- coding: utf-8 -*-
"""Paleta e folha de estilo (QSS) — assinatura visual dos projetos.

O acento é **#7d0e0e**, um vermelho-escuro de marca. Ele aparece em ações
primárias, foco, seleção e no topo da aba ativa; nunca em mensagem de erro.
Essa separação é deliberada: num sistema cujo acento já é vermelho, deixar o
erro no mesmo tom faz a pessoa parar de enxergar o alerta. Por isso o acento
é escuro e pouco luminoso, e `bad` é um vermelho vivo e claro — a diferença
que o olho lê primeiro é de luminosidade, não de matiz.

No tema escuro o acento **não** vira vermelho puro: sobe só até um tijolo
(#C4302A), o suficiente para ter contraste de borda sobre o fundo sem passar
a ser lido como alerta. O texto sobre o acento continua branco nos dois temas.

As cores semânticas (ok/aviso/erro/info) ficam separadas do acento de
propósito, para que "atenção" nunca se confunda com "isto é clicável".

Nenhuma cor deve aparecer literal no código da interface: item de tabela,
pintura em QPainter e destaque de linha puxam daqui, senão o tema escuro os
deixa ilegíveis.
"""

from dataclasses import dataclass

# ------------------------------------------------------------------ fontes
FONT_UI = '"Segoe UI", "Noto Sans", sans-serif'
FONT_MONO = '"Cascadia Mono", "Consolas", monospace'


@dataclass(frozen=True)
class Paleta:
    nome: str
    bg: str                # fundo da janela
    surface: str           # painéis, campos e fundo das tabelas
    surface_2: str         # faixas, cabeçalhos, barra de título e rodapé
    sunken: str            # fundo de listas e campos rebaixados
    linha_alt: str         # linha alternada da tabela
    somente_leitura: str   # célula derivada, não editável
    ink: str               # texto principal
    ink_2: str             # texto secundário
    ink_3: str             # dicas e rótulos apagados
    border: str            # linha de grade e divisórias
    border_2: str          # borda de campo e contorno de widget
    accent: str            # marca: ações primárias, foco e seleção
    accent_hover: str
    accent_soft: str       # fundo de seleção
    on_accent: str         # texto sobre o acento
    ok: str                # sucesso / confere
    warn: str              # atenção
    bad: str               # erro / divergência
    info: str              # informação estrutural, links


CLARO = Paleta(
    nome="claro",
    bg="#F4F6F8",
    surface="#FFFFFF",
    surface_2="#E9EDF1",
    sunken="#FBFCFD",
    linha_alt="#F7F9FB",
    somente_leitura="#EFF2F5",
    ink="#16202A",
    ink_2="#4A5A6A",
    ink_3="#74848F",
    border="#D2DAE2",
    border_2="#BAC5D0",
    accent="#7D0E0E",
    accent_hover="#5C0A0A",
    accent_soft="#F6E7E6",
    on_accent="#FFFFFF",
    ok="#15803D",
    warn="#A16207",
    bad="#D92D20",
    info="#1565C0",
)

ESCURO = Paleta(
    nome="escuro",
    bg="#0F1620",
    surface="#18212C",
    surface_2="#212C39",
    sunken="#131C25",
    linha_alt="#1C2632",
    somente_leitura="#222C38",
    ink="#E3E9EF",
    ink_2="#94A4B5",
    ink_3="#6E7F91",
    border="#2B3745",
    border_2="#3A4857",
    accent="#C4302A",
    accent_hover="#DC4A42",
    accent_soft="#2E1616",
    on_accent="#FFFFFF",
    ok="#4ADE80",
    warn="#FBBF24",
    bad="#FF8A8A",
    info="#60A5FA",
)

# ------------------------------------------------------------- densidade
# Numa tela apertada, alguns pixels de padding a menos por linha valem uma
# coluna inteira a mais visível. A densidade só mexe em espaçamento e corpo
# de fonte — cor e borda seguem iguais nas duas.
DENSIDADES = {
    "normal": {"fonte": "9pt", "celula": "2px 4px", "aba": "6px 14px",
               "botao": "5px 13px", "campo": "4px 7px", "cabecalho": "5px 6px"},
    "compacta": {"fonte": "8pt", "celula": "1px 3px", "aba": "4px 10px",
                 "botao": "4px 10px", "campo": "3px 5px",
                 "cabecalho": "3px 5px"},
}
_densidade = "normal"


def definir_densidade(nome):
    """'normal' ou 'compacta'. Devolve a densidade ativa."""
    global _densidade
    if nome in DENSIDADES:
        _densidade = nome
    return _densidade


def densidade():
    return _densidade


_ativa = CLARO


def atual():
    return _ativa


def definir_escuro(escuro):
    """Troca a paleta ativa e devolve a nova."""
    global _ativa
    _ativa = ESCURO if escuro else CLARO
    return _ativa


def e_escuro():
    return _ativa is ESCURO


def folha_estilo():
    """Folha de estilo completa, com os ícones desenhados em runtime."""
    from . import icones
    p = _ativa
    d = DENSIDADES[_densidade]
    art = icones.caminhos(p)
    check = art["check"]
    chevron = art["chevron"]
    chevron_off = art["chevron_off"]

    return f"""
QWidget {{
    background: {p.bg};
    color: {p.ink};
    font-family: {FONT_UI};
    font-size: {d['fonte']};
}}
QMainWindow, QDialog {{ background: {p.bg}; }}

/* ------------------------------------------------- barra de título
   A janela continua nativa (ver janela_nativa): a faixa abaixo só desenha.
   Os botões precisam de fundo próprio no hover para dar retorno de clique,
   já que o resto da barra é área de arrasto do Windows. */
QWidget#barraTitulo {{
    background: {p.surface_2};
    border-bottom: 1px solid {p.border};
}}
QWidget#barraTitulo QLabel {{ background: transparent; }}
QLabel[role="tituloApp"] {{ color: {p.ink}; font-weight: 700; }}
QLabel[role="tituloCtx"] {{ color: {p.ink_2}; }}
QWidget#barraTitulo QToolButton {{
    background: transparent;
    color: {p.ink_2};
    border: none;
    border-radius: 0;
    padding: 0;
    font-size: 11pt;
}}
/* glifos da fonte de ícones do Windows: mesmo peso e tamanho nos três */
QWidget#barraTitulo QToolButton[iconfont="mdl2"] {{
    font-family: "Segoe MDL2 Assets";
    font-size: 10px;
}}
QWidget#barraTitulo QToolButton:hover {{
    background: {p.border}; color: {p.ink};
}}
QWidget#barraTitulo QToolButton#btn_fechar:hover {{
    background: {p.bad}; color: #FFFFFF;
}}

/* ------------------------------------------------------ barra de ações */
QToolBar {{
    background: {p.surface_2};
    border: none;
    border-bottom: 1px solid {p.border};
    padding: 3px 4px;
    spacing: 2px;
}}
QToolBar::separator {{
    background: {p.border_2};
    width: 1px;
    margin: 4px 6px;
}}
QToolButton {{
    background: transparent;
    color: {p.ink_2};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 5px 11px;
}}
QToolButton:hover {{
    background: {p.surface}; color: {p.ink}; border-color: {p.border_2};
}}
QToolButton:pressed {{ background: {p.border}; }}
QToolButton:disabled {{ color: {p.ink_3}; }}
/* botão-interruptor: moldura de acento enquanto o painel está aberto, para
   ficar claro que é um estado, não um clique que passou */
QToolBar QToolButton:checked {{
    background: {p.accent_soft};
    color: {p.ink};
    border: 1px solid {p.accent};
    font-weight: 600;
}}
QToolBar QToolButton:checked:hover {{ background: {p.accent_soft}; }}

QMenu {{
    background: {p.surface};
    color: {p.ink};
    border: 1px solid {p.border_2};
    padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 14px; border-radius: 3px; }}
QMenu::item:selected {{ background: {p.accent_soft}; color: {p.ink}; }}
QMenu::item:disabled {{ color: {p.ink_3}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}

QStatusBar {{
    background: {p.surface_2};
    color: {p.ink_2};
    border-top: 1px solid {p.border};
}}
QStatusBar::item {{ border: none; }}

/* ---------------------------------------------------------------- abas */
QTabWidget::pane {{
    background: {p.surface};
    border: 1px solid {p.border};
    border-radius: 4px;
    top: -1px;
}}
QTabBar::tab {{
    background: {p.sunken};
    color: {p.ink_3};
    border: 1px solid {p.border};
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    padding: {d['aba']};
    margin-right: 2px;
}}
QTabBar::tab:selected {{
    background: {p.surface};
    color: {p.ink};
    font-weight: 600;
    border-top: 2px solid {p.accent};
    padding-top: 5px;
}}
QTabBar::tab:hover:!selected {{ color: {p.ink_2}; background: {p.surface_2}; }}

/* ------------------------------------------------------------- tabelas
   Tabela é lida em varredura vertical: a linha divisória precisa ser
   discreta para não competir com o dado, e a célula selecionada tem de se
   destacar sem escondê-lo — daí o acento suave com o texto preservado, em
   vez de fundo sólido. */
QTableWidget, QTableView {{
    background: {p.surface};
    alternate-background-color: {p.linha_alt};
    color: {p.ink};
    gridline-color: {p.border};
    border: 1px solid {p.border_2};
    border-radius: 4px;
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
    outline: none;
}}
QTableWidget::item, QTableView::item {{ padding: {d['celula']}; }}
QTableWidget::item:selected, QTableView::item:selected {{
    background: {p.accent_soft};
    color: {p.ink};
}}
QTableWidget QLineEdit {{
    border: 1px solid {p.accent};
    border-radius: 0;
    padding: 1px 3px;
}}

QHeaderView::section {{
    background: {p.surface_2};
    color: {p.ink_2};
    border: none;
    border-right: 1px solid {p.border};
    border-bottom: 1px solid {p.border_2};
    padding: {d['cabecalho']};
    font-weight: 600;
}}
QHeaderView::section:hover {{ background: {p.border}; color: {p.ink}; }}
QHeaderView::section:vertical {{
    border-right: 1px solid {p.border_2};
    color: {p.ink_3};
    font-weight: 400;
}}
QTableCornerButton::section {{
    background: {p.surface_2};
    border: none;
    border-bottom: 1px solid {p.border_2};
}}

QTreeWidget, QTreeView, QListWidget, QListView {{
    background: {p.sunken};
    color: {p.ink};
    border: 1px solid {p.border_2};
    border-radius: 4px;
    outline: none;
}}
QTreeWidget::item:selected, QTreeView::item:selected,
QListWidget::item:selected, QListView::item:selected {{
    background: {p.accent_soft}; color: {p.ink};
}}

/* ------------------------------------------------------------- rótulos
   Os papéis visuais vêm de uma propriedade dinâmica `role`: no Qt Designer o
   objectName precisa ser único por widget, e vários rótulos compartilham o
   mesmo estilo. Trocar a propriedade em runtime exige unpolish/polish — use
   `assinatura.comum.definir_papel`. */
QLabel {{ background: transparent; color: {p.ink}; }}
QLabel[role="secao"] {{ font-weight: 700; letter-spacing: 1px; }}
QLabel[role="info"] {{ color: {p.ink_2}; }}
QLabel[role="dica"] {{ color: {p.ink_3}; }}
QLabel[role="mono"] {{ font-family: {FONT_MONO}; color: {p.ink_2}; }}
QLabel[role="ok"] {{ color: {p.ok}; font-weight: bold; }}
QLabel[role="aviso"] {{ color: {p.warn}; }}
QLabel[role="erro"] {{ color: {p.bad}; font-weight: bold; }}
QLabel[role="tituloInicial"] {{
    color: {p.ink}; font-size: 15pt; font-weight: 700;
}}
QLabel[role="solte"] {{
    color: {p.ink_3};
    border: 2px dashed {p.border_2};
    border-radius: 6px;
    padding: 10px;
}}

/* ------------------------------------------------------------- botões */
QPushButton {{
    background: {p.surface};
    color: {p.ink_2};
    border: 1px solid {p.border_2};
    border-radius: 4px;
    padding: {d['botao']};
    min-height: 15px;
}}
QPushButton:hover {{ background: {p.surface_2}; color: {p.ink}; }}
QPushButton:pressed {{ background: {p.border}; }}
QPushButton:disabled {{
    background: {p.bg}; color: {p.ink_3}; border-color: {p.border};
}}
QPushButton[role="accent"] {{
    background: {p.accent};
    color: {p.on_accent};
    border: 1px solid {p.accent};
    font-weight: 600;
    padding: 6px 15px;
}}
QPushButton[role="accent"]:hover {{
    background: {p.accent_hover}; border-color: {p.accent_hover};
}}
QPushButton[role="accent"]:disabled {{
    background: {p.border_2}; border-color: {p.border_2}; color: {p.bg};
}}
QPushButton[role="perigo"] {{
    background: transparent; color: {p.bad}; border: 1px solid {p.bad};
}}
QPushButton[role="perigo"]:hover {{ background: {p.bad}; color: #FFFFFF; }}

/* ------------------------------------------------------------- campos */
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QPlainTextEdit, QTextEdit {{
    background: {p.sunken};
    color: {p.ink};
    border: 1px solid {p.border_2};
    border-radius: 4px;
    padding: {d['campo']};
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p.accent}; }}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    background: {p.bg}; color: {p.ink_3}; border-color: {p.border};
}}
QLineEdit[mono="true"] {{ font-family: {FONT_MONO}; }}

QComboBox {{
    background: {p.surface};
    color: {p.ink};
    border: 1px solid {p.border_2};
    border-radius: 4px;
    padding: {d['campo']};
}}
QComboBox:focus {{ border-color: {p.accent}; }}
QComboBox:disabled {{
    background: {p.bg}; color: {p.ink_3}; border-color: {p.border};
}}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url("{chevron_off}"); }}
QComboBox QAbstractItemView {{
    background: {p.surface};
    color: {p.ink};
    border: 1px solid {p.border_2};
    selection-background-color: {p.accent_soft};
    selection-color: {p.ink};
    outline: none;
    padding: 2px;
}}

QCheckBox, QRadioButton {{
    background: transparent; color: {p.ink_2}; spacing: 7px;
}}
QCheckBox:disabled, QRadioButton:disabled {{ color: {p.ink_3}; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {p.border_2};
    border-radius: 3px;
    background: {p.surface};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{
    background: {p.accent};
    border-color: {p.accent};
    image: url("{check}");
}}
QCheckBox::indicator:disabled {{
    background: {p.bg}; border-color: {p.border};
}}

QGroupBox {{
    background: transparent;
    border: 1px solid {p.border};
    border-radius: 4px;
    margin-top: 10px;
    padding-top: 8px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 9px;
    padding: 0 5px;
    color: {p.ink_2};
    font-weight: 600;
}}

/* -------------------------------------------------------------- barras */
QProgressBar {{
    background: {p.surface_2};
    border: 1px solid {p.border};
    border-radius: 7px;
    height: 14px;
    text-align: center;
    color: {p.ink_2};
}}
QProgressBar::chunk {{ background: {p.accent}; border-radius: 6px; }}

QScrollBar:vertical {{ background: {p.bg}; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {p.border_2}; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {p.ink_3}; }}
QScrollBar:horizontal {{ background: {p.bg}; height: 11px; margin: 0; }}
QScrollBar::handle:horizontal {{
    background: {p.border_2}; border-radius: 5px; min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{ background: {p.ink_3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {p.border}; }}
QSplitter::handle:hover {{ background: {p.accent}; }}

/* ------------------------------------------------------------ diversos */
QFrame[role="divisor"] {{
    background: {p.border}; max-height: 1px; border: none;
}}
QToolTip {{
    background: {p.ink};
    color: {p.surface};
    border: none;
    padding: 5px 7px;
}}
QMessageBox {{ background: {p.bg}; }}
QMessageBox QLabel {{ color: {p.ink}; }}
"""
