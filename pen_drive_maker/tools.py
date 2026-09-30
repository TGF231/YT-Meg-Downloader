"""Localização das ferramentas externas: ffmpeg e o runtime JavaScript.

O `shutil.which` não serve sozinho no Windows. Ele não trata três formatos de
entrada de PATH que o shell aceita sem reclamar:

    "C:\\ffmpeg\\bin"      entrada entre aspas
    %USERPROFILE%\\bin     variável não expandida (chave REG_SZ no registro)
     C:\\ffmpeg\\bin       espaço sobrando antes ou depois

Qualquer uma delas faz o programa dizer que o ffmpeg não está instalado quando
ele está no PATH e funciona no prompt. Por isso a busca aqui é própria: além de
normalizar as entradas, olha os diretórios de instalação usuais e confirma cada
candidato executando `-version`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Sequence

# Nomes dos executáveis aceitos pelo yt-dlp, na ordem de preferência dele.
JS_RUNTIME_EXECUTABLES = ("deno", "bun", "node", "qjs")

_WINDOWS_SUFFIXES = (".exe", ".cmd", ".bat", "")
_POSIX_SUFFIXES = ("",)


def _suffixes() -> Sequence[str]:
    return _WINDOWS_SUFFIXES if os.name == "nt" else _POSIX_SUFFIXES


def _no_window_kwargs() -> dict:
    """Evita o piscar de um console ao sondar um executável num app gráfico."""
    if os.name == "nt":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


def iter_path_dirs() -> Iterator[Path]:
    """Diretórios do PATH, normalizados como o shell faz."""
    for entry in (os.environ.get("PATH") or "").split(os.pathsep):
        entry = entry.strip().strip('"').strip()
        if not entry:
            continue
        expanded = os.path.expandvars(os.path.expanduser(entry))
        if "%" in expanded and os.name == "nt":
            continue  # variável que não existe: entrada inútil
        try:
            yield Path(expanded)
        except (ValueError, OSError):
            continue


def bundled_dir() -> Optional[Path]:
    """Pasta onde o PyInstaller extrai o que foi embutido no executável."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            path = Path(meipass)
            return path if path.is_dir() else None
        except (ValueError, OSError):
            return None
    return None


def app_dirs() -> List[Path]:
    """Pastas do próprio programa, onde o usuário pode largar um executável.

    A pasta do bundle vem primeiro: o que embutimos é a versão que sabemos que
    funciona, e evita depender do que estiver instalado na máquina.
    """
    dirs: List[Path] = []
    bundled = bundled_dir()
    if bundled is not None:
        dirs.append(bundled)
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        dirs += [base, base / "bin", base / "ffmpeg", base / "ffmpeg" / "bin"]
    try:
        dirs.append(Path.cwd())
    except OSError:
        pass
    return dirs


def windows_ffmpeg_dirs() -> List[Path]:
    """Onde os instaladores usuais do Windows colocam o ffmpeg."""
    if os.name != "nt":
        return []
    dirs: List[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        # O winget cria atalhos em Links e o pacote real fica em Packages.
        dirs.append(winget / "Links")
        packages = winget / "Packages"
        if packages.is_dir():
            try:
                for package in packages.glob("*FFmpeg*"):
                    dirs.append(package)
                    dirs.extend(p for p in package.glob("*/bin") if p.is_dir())
            except OSError:
                pass
    for env_var, tail in (
        ("ProgramData", "chocolatey/bin"),
        ("USERPROFILE", "scoop/shims"),
        ("ProgramFiles", "ffmpeg/bin"),
        ("ProgramFiles(x86)", "ffmpeg/bin"),
    ):
        root = os.environ.get(env_var)
        if root:
            dirs.append(Path(root).joinpath(*tail.split("/")))
    dirs += [Path("C:/ffmpeg/bin"), Path("C:/ffmpeg")]
    return dirs


def _candidates(name: str, directories: Iterable[Path]) -> Iterator[Path]:
    seen = set()
    for directory in directories:
        try:
            key = os.path.normcase(str(directory))
        except (ValueError, OSError):
            continue
        if key in seen:
            continue
        seen.add(key)
        for suffix in _suffixes():
            candidate = directory / f"{name}{suffix}"
            try:
                if candidate.is_file():
                    yield candidate
            except OSError:
                continue


# Cada ferramenta aceita uma sintaxe: o deno recusa "-version" e o ffmpeg
# recusa nada, então tentamos as duas formas antes de descartar o candidato.
_VERSION_FLAGS = ("--version", "-version")


def runs_ok(path: str, expected: str = "", timeout: float = 15.0) -> bool:
    """Confirma um executável rodando `--version`: é a única prova real."""
    for flag in _VERSION_FLAGS:
        try:
            result = subprocess.run(
                [path, flag],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=timeout,
                **_no_window_kwargs(),
            )
        except (OSError, subprocess.SubprocessError):
            return False  # nem executou: tentar outra flag não ajuda
        if result.returncode != 0:
            continue
        output = (result.stdout or b"").decode("utf-8", "replace").lower()
        if expected and expected.lower() not in output:
            continue
        if "unexpected argument" in output or "unrecognized option" in output:
            continue
        return True
    return False


def find_executable(
    name: str, extra_dirs: Optional[Iterable[Path]] = None, expected: str = ""
) -> Optional[str]:
    """Procura um executável e devolve o caminho do primeiro que responder."""
    directories: List[Path] = list(extra_dirs or [])
    directories += list(iter_path_dirs())

    for candidate in _candidates(name, directories):
        if runs_ok(str(candidate), expected):
            return str(candidate)

    # Último recurso: deixa o sistema resolver, caso a busca não cubra o caso.
    fallback = shutil.which(name)
    if fallback and runs_ok(fallback, expected):
        return fallback
    return None


def find_ffmpeg(configured: str = "") -> Optional[str]:
    """Caminho do ffmpeg: o configurado, se válido, senão a busca automática."""
    if configured:
        chosen = Path(configured).expanduser()
        if chosen.is_dir():
            found = next(_candidates("ffmpeg", [chosen]), None)
            if found and runs_ok(str(found), "ffmpeg version"):
                return str(found)
        elif chosen.is_file() and runs_ok(str(chosen), "ffmpeg version"):
            return str(chosen)
        return None
    return find_executable("ffmpeg", app_dirs() + windows_ffmpeg_dirs(), "ffmpeg version")


def find_js_runtime() -> Optional[str]:
    """Runtime JavaScript, procurado onde o yt-dlp procura."""
    extra = app_dirs()
    for name in JS_RUNTIME_EXECUTABLES:
        found = find_executable(name, extra)
        if found:
            return found
    return None


def ejs_package_version() -> Optional[str]:
    """Versão do yt-dlp-ejs, que traz os scripts do solucionador localmente.

    Sem esse pacote o yt-dlp cai no stub embutido, que importa a biblioteca do
    npm em tempo de execução — e, como esse download vem desligado por padrão,
    o resultado é "The page needs to be reloaded" em toda faixa.
    """
    try:
        import yt_dlp_ejs  # noqa: PLC0415 - opcional, checado em tempo de execução
        import yt_dlp_ejs.yt.solver  # garante que os scripts são legíveis

        if not yt_dlp_ejs.yt.solver.lib():
            return None
    except (ImportError, OSError):
        return None
    return str(getattr(yt_dlp_ejs, "version", None) or "versão desconhecida")


def describe_missing_ejs() -> str:
    return "\n".join([
        "Pacote yt-dlp-ejs não encontrado.",
        "Ele traz os scripts que resolvem os desafios JavaScript do YouTube. Sem "
        "ele o yt-dlp tenta buscar a biblioteca no npm durante o download, o que "
        "vem desligado por padrão, e toda faixa falha com 'The page needs to be "
        "reloaded'.",
        "Instale com: pip install yt-dlp-ejs",
        "Ou marque 'Permitir baixar componentes do yt-dlp' na interface, que "
        "autoriza o yt-dlp a buscá-los sozinho.",
    ])


def describe_missing_js_runtime() -> str:
    lines = [
        "Nenhum runtime JavaScript encontrado (deno, bun, node ou qjs).",
        "O YouTube passou a exigir a execução de um script para liberar os links "
        "de áudio. Sem isso o download falha com 'Signature solving failed' e "
        "'The page needs to be reloaded', por mais que os cookies estejam certos.",
        "Instale um destes — o Deno é o recomendado pelo yt-dlp:",
    ]
    if os.name == "nt":
        lines += [
            "  winget install DenoLand.Deno",
            "  ou baixe o deno.exe em https://github.com/denoland/deno/releases e "
            "coloque na MESMA PASTA do PenDriveMaker.exe.",
            "  (o Node.js também serve: winget install OpenJS.NodeJS)",
        ]
    elif sys.platform == "darwin":
        lines += ["  brew install deno", "  (o Node.js também serve: brew install node)"]
    else:
        lines += [
            "  curl -fsSL https://deno.land/install.sh | sh",
            "  (o Node.js também serve: sudo apt install nodejs)",
        ]
    lines.append("Depois de instalar, feche e abra o programa de novo.")
    return "\n".join(lines)


def describe_missing_ffmpeg(configured: str = "") -> str:
    lines: List[str] = []
    if configured:
        lines.append(f"O ffmpeg indicado não funcionou: {configured}")
        lines.append(
            "Aponte para o ffmpeg.exe ou para a pasta que o contém (normalmente "
            "termina em \\bin), ou limpe o campo para a busca automática."
        )
    else:
        lines.append("ffmpeg não encontrado.")
        lines.append(
            "A busca cobre o PATH, a pasta do programa e os locais usuais de "
            "instalação. Se ele estiver em outro lugar, indique o caminho no campo "
            "'ffmpeg' da interface."
        )
        if os.name == "nt":
            lines.append("  winget install Gyan.FFmpeg")
            lines.append(
                "  ou baixe em https://www.gyan.dev/ffmpeg/builds/ e coloque o "
                "ffmpeg.exe na mesma pasta do PenDriveMaker.exe."
            )
        elif sys.platform == "darwin":
            lines.append("  brew install ffmpeg")
        else:
            lines.append("  sudo apt install ffmpeg")
    lines.append(
        "Sem o ffmpeg o áudio é salvo no formato original, sem conversão para MP3, "
        "sem metadados e sem capa."
    )
    return "\n".join(lines)
