# -*- mode: python ; coding: utf-8 -*-
"""Especificação do PyInstaller para o executável standalone.

Uso: pyinstaller --noconfirm packaging.spec
O ffmpeg NÃO é embutido: ele continua sendo pré-requisito do sistema.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# O yt-dlp traz os scripts do solucionador de desafios do YouTube como arquivos
# .js dentro do pacote (extractor/youtube/jsc/_builtin/vendor). O hook oficial
# do yt-dlp só coleta os do pacote opcional yt_dlp_ejs, então sem esta linha o
# executável sai sem eles e todo download falha com "The page needs to be
# reloaded", mesmo com um runtime JavaScript instalado.
YT_DLP_DATAS = collect_data_files("yt_dlp", includes=["**/*.js"])

# Ferramentas externas embutidas (ffmpeg e o runtime JavaScript). O workflow
# baixa os executáveis para esta pasta antes de compilar; sem ela o build sai
# enxuto e o programa procura as ferramentas no sistema, como antes.
BUNDLED_TOOLS = []
BUNDLED_DOCS = []
_bundle_dir = os.environ.get("PDM_BUNDLE_DIR", "")
if _bundle_dir and os.path.isdir(_bundle_dir):
    for _entry in sorted(os.listdir(_bundle_dir)):
        _path = os.path.join(_bundle_dir, _entry)
        if not os.path.isfile(_path):
            continue
        # Destino "." = raiz do bundle, que é onde o programa procura.
        if _entry.lower().endswith((".txt", ".md")):
            BUNDLED_DOCS.append((_path, "."))   # licenças: não são executáveis
        else:
            BUNDLED_TOOLS.append((_path, "."))
    print(f"Embutindo ferramentas: {[b[0] for b in BUNDLED_TOOLS + BUNDLED_DOCS]}")

# Módulos Qt que a aplicação não usa; excluí-los reduz bastante o binário.
EXCLUDED_QT = [
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput", "PySide6.Qt3DLogic", "PySide6.Qt3DRender",
    "PySide6.QtBluetooth", "PySide6.QtCharts", "PySide6.QtDataVisualization",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNfc", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickControls2", "PySide6.QtQuickWidgets",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtSensors",
    "PySide6.QtSerialPort", "PySide6.QtSpatialAudio", "PySide6.QtSql",
    "PySide6.QtStateMachine", "PySide6.QtSvg", "PySide6.QtSvgWidgets",
    "PySide6.QtTest", "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
    "PySide6.QtWebChannel", "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets", "PySide6.QtXml",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=BUNDLED_TOOLS,
    datas=YT_DLP_DATAS + BUNDLED_DOCS,
    # O yt-dlp registra seus próprios hooks do PyInstaller; os extratores são
    # importados dinamicamente e precisam ser coletados inteiros.
    hiddenimports=["yt_dlp"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDED_QT + ["tkinter", "unittest", "pydoc_data"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PenDriveMaker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,  # aplicação gráfica: sem janela de console no Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
