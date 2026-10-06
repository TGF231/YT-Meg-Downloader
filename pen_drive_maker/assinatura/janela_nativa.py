# -*- coding: utf-8 -*-
"""Barra de título própria **sem** abrir mão do comportamento nativo.

O caminho fácil seria `Qt.FramelessWindowHint`: a moldura some e pronto. O
problema é que, para o Windows, a janela deixa de ser uma janela — vão junto o
**Aero Snap** (arrastar para a borda e encaixar), o redimensionamento pelas
bordas, o duplo clique para maximizar, a sombra e o Snap Layouts do Win11.
Numa ferramenta de trabalho isso pesa: ela quase sempre é usada lado a lado
com outra janela.

A abordagem aqui é a mesma do VS Code, do Chrome e do Discord: **manter** os
estilos nativos da janela (WS_THICKFRAME, WS_CAPTION) e apenas interceptar
quatro mensagens:

* **WM_NCCALCSIZE** — devolve a área de cliente ocupando a janela inteira, de
  modo que o Qt desenhe por cima de onde ficava a legenda. O Windows continua
  achando que existe moldura, e é por isso que o Snap sobrevive.
* **WM_NCHITTEST** — como a área não-cliente sumiu, somos nós que dizemos o
  que é borda, canto e faixa de arrasto. Devolver HTCAPTION na faixa da barra
  dá de graça o arrastar e o duplo clique para maximizar.
* **WM_NCRBUTTONUP** — o menu de sistema no clique direito da barra **não**
  vem de graça: o Windows o abriria ao tratar o `WM_CONTEXTMENU`, e o Qt
  intercepta essa mensagem para os menus de contexto dele.
* **WM_GETMINMAXINFO** — maximizar uma janela sem moldura cobriria a barra de
  tarefas; aqui o tamanho é limitado à área de trabalho do monitor **daquela**
  janela (importante com dois monitores de resoluções diferentes).

Fora do Windows — ou se qualquer passo falhar — `aplicar()` devolve False e o
programa segue com a barra nativa. Uma barra de título é enfeite: nunca pode
impedir o aplicativo de abrir.

Ligada por padrão. Para voltar à barra do Windows, defina a variável de
ambiente configurada em `definir_escotilha()` (padrão: `UI_BARRA_NATIVA=1`).

## Três armadilhas que já derrubaram um programa desta família

1. **As estruturas ctypes são criadas uma única vez, no import.** Criá-las
   dentro de uma função produz, a cada chamada, um *tipo novo* com o mesmo
   layout — e o ctypes compara tipos por identidade: passar `byref(RECT_A)`
   onde `argtypes` espera `POINTER(RECT_B)` falha dentro do despacho de
   mensagens do Windows, onde não há quem mostre o erro. Como WM_NCHITTEST
   chega a cada movimento do mouse, o sintoma era "o console abre e fecha".
2. **`argtypes` sempre declarados.** Sem eles o ctypes converte inteiros para
   C `int` (32 bits) e o HWND de um Windows 64-bit é truncado.
3. **Nunca chamar `super().nativeEvent(...)`** de dentro do override. Para
   "não tratei", devolva `(False, 0)` — o Qt segue com o tratamento padrão.
"""

import os
import sys

# ------------------------------------------------------- mensagens e códigos
WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
WM_GETMINMAXINFO = 0x0024
WM_NCRBUTTONUP = 0x00A5
WM_SYSCOMMAND = 0x0112

# menu de sistema
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
MF_BYCOMMAND = 0x0000
MF_ENABLED = 0x0000
MF_GRAYED = 0x0001
SC_SIZE = 0xF000
SC_MOVE = 0xF010
SC_MINIMIZE = 0xF020
SC_MAXIMIZE = 0xF030
SC_CLOSE = 0xF060
SC_RESTORE = 0xF120

HTCLIENT = 1
HTCAPTION = 2
HTLEFT = 10
HTRIGHT = 11
HTTOP = 12
HTTOPLEFT = 13
HTTOPRIGHT = 14
HTBOTTOM = 15
HTBOTTOMLEFT = 16
HTBOTTOMRIGHT = 17

SM_CXSIZEFRAME = 32
SM_CYSIZEFRAME = 33
SM_CXPADDEDBORDER = 92

MONITOR_DEFAULTTONEAREST = 2

# espessura da faixa sensível ao redimensionamento, em pixels lógicos
BORDA = 6

# Escotilha para voltar à barra do Windows sem mexer no código.
VARIAVEL_DESLIGAR = "UI_BARRA_NATIVA"


def definir_escotilha(nome):
    """Nome da variável de ambiente que desliga a barra própria."""
    global VARIAVEL_DESLIGAR
    VARIAVEL_DESLIGAR = nome or VARIAVEL_DESLIGAR
    return VARIAVEL_DESLIGAR


# --------------------------------------------------------------- estruturas
# Definidas **uma vez**, no import (ver armadilha 1 no topo do módulo).
_WIN = sys.platform == "win32"
_pronto = False
_dpi_disponivel = False

if _WIN:
    try:
        import ctypes
        from ctypes import wintypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        class MINMAXINFO(ctypes.Structure):
            _fields_ = [("ptReserved", POINT), ("ptMaxSize", POINT),
                        ("ptMaxPosition", POINT), ("ptMinTrackSize", POINT),
                        ("ptMaxTrackSize", POINT)]

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                        ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]

        class NCCALCSIZE_PARAMS(ctypes.Structure):
            _fields_ = [("rgrc", RECT * 3), ("lppos", ctypes.c_void_p)]

        class MARGINS(ctypes.Structure):
            _fields_ = [("cxLeftWidth", ctypes.c_int),
                        ("cxRightWidth", ctypes.c_int),
                        ("cyTopHeight", ctypes.c_int),
                        ("cyBottomHeight", ctypes.c_int)]

        class MSG(ctypes.Structure):
            _fields_ = [("hWnd", wintypes.HWND), ("message", wintypes.UINT),
                        ("wParam", ctypes.c_void_p),
                        ("lParam", ctypes.c_void_p),
                        ("time", wintypes.DWORD), ("pt", POINT)]

        _user32 = ctypes.windll.user32
        _dwmapi = ctypes.windll.dwmapi

        _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        _user32.GetWindowRect.restype = wintypes.BOOL
        _user32.IsZoomed.argtypes = [wintypes.HWND]
        _user32.IsZoomed.restype = wintypes.BOOL
        _user32.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        _user32.SetWindowPos.restype = wintypes.BOOL
        _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        _user32.MonitorFromWindow.restype = wintypes.HANDLE
        _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE,
                                            ctypes.POINTER(MONITORINFO)]
        _user32.GetMonitorInfoW.restype = wintypes.BOOL
        _user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        _user32.GetSystemMetrics.restype = ctypes.c_int
        # por DPI: existem a partir do Windows 10 1607
        try:
            _user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            _user32.GetDpiForWindow.restype = ctypes.c_uint
            _user32.GetSystemMetricsForDpi.argtypes = [ctypes.c_int,
                                                       ctypes.c_uint]
            _user32.GetSystemMetricsForDpi.restype = ctypes.c_int
            _dpi_disponivel = True
        except AttributeError:
            _dpi_disponivel = False
        _user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
        _user32.GetSystemMenu.restype = wintypes.HMENU
        _user32.EnableMenuItem.argtypes = [wintypes.HMENU, wintypes.UINT,
                                           wintypes.UINT]
        _user32.EnableMenuItem.restype = wintypes.BOOL
        # com TPM_RETURNCMD devolve o comando escolhido (0 = nenhum)
        _user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        _user32.TrackPopupMenu.restype = ctypes.c_int
        _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                         wintypes.WPARAM, wintypes.LPARAM]
        _user32.PostMessageW.restype = wintypes.BOOL
        _dwmapi.DwmExtendFrameIntoClientArea.argtypes = [
            wintypes.HWND, ctypes.POINTER(MARGINS)]
        _dwmapi.DwmExtendFrameIntoClientArea.restype = ctypes.c_long

        _pronto = True
    except Exception:  # noqa: BLE001
        _pronto = False


def disponivel():
    """Windows, estruturas preparadas e barra própria não desligada."""
    if not (_WIN and _pronto):
        return False
    return os.environ.get(VARIAVEL_DESLIGAR) != "1"


def aplicar(janela):
    """Prepara a janela para a barra própria. Devolve True se conseguiu.

    Deve ser chamada **depois** de a janela existir de fato (primeiro
    `showEvent`): forçar `winId()` no meio da construção do QMainWindow cria a
    janela nativa cedo demais.

    Não altera window flags: os estilos nativos precisam continuar lá, senão o
    Snap e o redimensionamento vão embora junto.
    """
    if not disponivel():
        return False
    try:
        hwnd = wintypes.HWND(int(janela.winId()))
        # sombra: sem isso a janela fica "chapada" contra o fundo
        margens = MARGINS(1, 1, 1, 1)
        _dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margens))
        # SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED
        _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0,
                             0x0002 | 0x0001 | 0x0004 | 0x0020)
        return True
    except Exception:  # noqa: BLE001
        return False


def _margem_moldura(hwnd=None):
    """Espessura da moldura que o Windows usa quando a janela é maximizada.

    Sensível ao **DPI do monitor onde a janela está**: `GetSystemMetrics`
    devolve sempre a medida de 96 DPI, e num monitor a 125% ou 150% a margem
    sairia pequena demais — maximizada, a janela transbordaria a área útil e
    parte do conteúdo ficaria fora da tela. Num arranjo de dois monitores com
    escalas diferentes, o erro aparece só depois de arrastar a janela.
    """
    if hwnd is not None and _dpi_disponivel:
        try:
            dpi = _user32.GetDpiForWindow(hwnd)
            if dpi:
                larg = (_user32.GetSystemMetricsForDpi(SM_CXSIZEFRAME, dpi)
                        + _user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER,
                                                         dpi))
                alt = (_user32.GetSystemMetricsForDpi(SM_CYSIZEFRAME, dpi)
                       + _user32.GetSystemMetricsForDpi(SM_CXPADDEDBORDER,
                                                        dpi))
                return larg, alt
        except Exception:  # noqa: BLE001
            pass
    larg = (_user32.GetSystemMetrics(SM_CXSIZEFRAME)
            + _user32.GetSystemMetrics(SM_CXPADDEDBORDER))
    alt = (_user32.GetSystemMetrics(SM_CYSIZEFRAME)
           + _user32.GetSystemMetrics(SM_CXPADDEDBORDER))
    return larg, alt


def itens_do_menu(maximizada):
    """`{comando: habilitado}` do menu de sistema, como o Windows faria:
    maximizada, não se move nem redimensiona e só dá para restaurar."""
    return {SC_RESTORE: maximizada, SC_MOVE: not maximizada,
            SC_SIZE: not maximizada, SC_MINIMIZE: True,
            SC_MAXIMIZE: not maximizada, SC_CLOSE: True}


def _menu_de_sistema(hwnd, x, y):
    """Abre o menu de sistema em (x, y) de tela e executa o escolhido."""
    menu = _user32.GetSystemMenu(hwnd, False)
    if not menu:
        return
    for comando, ligado in itens_do_menu(
            bool(_user32.IsZoomed(hwnd))).items():
        _user32.EnableMenuItem(menu, comando, MF_BYCOMMAND | (
            MF_ENABLED if ligado else MF_GRAYED))
    escolhido = _user32.TrackPopupMenu(
        menu, TPM_RETURNCMD | TPM_RIGHTBUTTON, x, y, 0, hwnd, None)
    if escolhido:
        _user32.PostMessageW(hwnd, WM_SYSCOMMAND, escolhido, 0)


def tratar(janela, mensagem_ptr, altura_barra, ponto_arrastavel=None):
    """Trata as mensagens nativas. Devolve (tratado, resultado).

    `ponto_arrastavel(x, y)` recebe coordenadas **locais** da janela e informa
    se ali a barra pode ser arrastada — falso sobre os botões, senão ninguém
    conseguiria clicar em fechar.

    O corpo inteiro é defensivo: uma exceção aqui acontece dentro do despacho
    de mensagens do Windows, onde não há quem a mostre — e devolver "não
    tratei" é sempre seguro, porque o Windows aplica o comportamento padrão.
    """
    if not disponivel():
        return False, 0
    try:
        msg = ctypes.cast(int(mensagem_ptr), ctypes.POINTER(MSG)).contents
        codigo = msg.message
        if codigo not in (WM_NCCALCSIZE, WM_NCHITTEST, WM_GETMINMAXINFO,
                          WM_NCRBUTTONUP):
            return False, 0
        hwnd = msg.hWnd

        # ------------------------------------------------- WM_NCRBUTTONUP
        if codigo == WM_NCRBUTTONUP:
            if int(msg.wParam or 0) != HTCAPTION:
                return False, 0
            lp = int(msg.lParam or 0)
            _menu_de_sistema(hwnd, ctypes.c_short(lp & 0xFFFF).value,
                             ctypes.c_short((lp >> 16) & 0xFFFF).value)
            return True, 0

        # -------------------------------------------------- WM_NCCALCSIZE
        if codigo == WM_NCCALCSIZE:
            if not msg.wParam:
                return False, 0
            if _user32.IsZoomed(hwnd):
                # maximizada, a janela nativa extrapola a área útil; sem
                # encolher, as bordas ficariam fora da tela
                p = ctypes.cast(msg.lParam,
                                ctypes.POINTER(NCCALCSIZE_PARAMS)).contents
                larg, alt = _margem_moldura(hwnd)
                p.rgrc[0].left += larg
                p.rgrc[0].right -= larg
                p.rgrc[0].top += alt
                p.rgrc[0].bottom -= alt
            # 0 = a área de cliente ocupa o retângulo proposto (sem legenda)
            return True, 0

        # --------------------------------------------------- WM_NCHITTEST
        if codigo == WM_NCHITTEST:
            lp = int(msg.lParam or 0)
            # coordenadas de tela vêm empacotadas em 16 bits com sinal
            x = ctypes.c_short(lp & 0xFFFF).value
            y = ctypes.c_short((lp >> 16) & 0xFFFF).value
            r = RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(r))
            escala = janela.devicePixelRatioF() or 1.0
            borda = max(1, int(BORDA * escala))

            if not _user32.IsZoomed(hwnd):
                esq = x < r.left + borda
                dir_ = x >= r.right - borda
                topo = y < r.top + borda
                base = y >= r.bottom - borda
                if topo and esq:
                    return True, HTTOPLEFT
                if topo and dir_:
                    return True, HTTOPRIGHT
                if base and esq:
                    return True, HTBOTTOMLEFT
                if base and dir_:
                    return True, HTBOTTOMRIGHT
                if esq:
                    return True, HTLEFT
                if dir_:
                    return True, HTRIGHT
                if topo:
                    return True, HTTOP
                if base:
                    return True, HTBOTTOM

            # faixa da barra: arrastar, duplo clique e menu de sistema saem de
            # graça do HTCAPTION
            local_x = int((x - r.left) / escala)
            local_y = int((y - r.top) / escala)
            if 0 <= local_y < altura_barra:
                if (ponto_arrastavel is None
                        or ponto_arrastavel(local_x, local_y)):
                    return True, HTCAPTION
            return True, HTCLIENT

        # ------------------------------------------------ WM_GETMINMAXINFO
        monitor = _user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        _user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        trabalho, tela = info.rcWork, info.rcMonitor
        mmi = ctypes.cast(msg.lParam, ctypes.POINTER(MINMAXINFO)).contents
        # "maximizada" limitada à área de trabalho, para a barra de tarefas
        # continuar visível
        mmi.ptMaxSize.x = trabalho.right - trabalho.left
        mmi.ptMaxSize.y = trabalho.bottom - trabalho.top
        mmi.ptMaxPosition.x = trabalho.left - tela.left
        mmi.ptMaxPosition.y = trabalho.top - tela.top
        mmi.ptMaxTrackSize.x = mmi.ptMaxSize.x
        mmi.ptMaxTrackSize.y = mmi.ptMaxSize.y
        return True, 0
    except Exception:  # noqa: BLE001
        return False, 0
