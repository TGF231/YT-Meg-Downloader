#!/usr/bin/env python3
"""Baixa ffmpeg e deno para embutir no executável.

Usado pelo workflow de release antes do PyInstaller. Coloca os executáveis em
uma pasta e imprime o caminho dela; o packaging.spec lê a variável
PDM_BUNDLE_DIR e embute tudo que encontrar ali.

Uso: python scripts/fetch_tools.py <windows|linux> <pasta-destino>
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

# O build "gpl" do BtbN é estático e inclui o libmp3lame, necessário para MP3.
FFMPEG_URLS = {
    "windows": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "linux": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
}
DENO_URLS = {
    "windows": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-pc-windows-msvc.zip",
    "linux": "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip",
}
EXE_SUFFIX = {"windows": ".exe", "linux": ""}


def download(url: str) -> bytes:
    print(f"baixando {url}", flush=True)
    request = urllib.request.Request(url, headers={"User-Agent": "pen-drive-maker-build"})
    with urllib.request.urlopen(request, timeout=600) as response:
        data = response.read()
    print(f"  {len(data) / 1e6:.1f} MB", flush=True)
    return data


def extract_member(archive: bytes, url: str, wanted: str, destination: Path) -> None:
    """Extrai um executável do arquivo, ignorando a estrutura de pastas."""
    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
            name = next(
                (n for n in bundle.namelist() if Path(n).name.lower() == wanted.lower()),
                None,
            )
            if name is None:
                raise SystemExit(f"{wanted} não encontrado em {url}")
            with bundle.open(name) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as bundle:
            member = next(
                (m for m in bundle.getmembers() if Path(m.name).name == wanted), None
            )
            if member is None:
                raise SystemExit(f"{wanted} não encontrado em {url}")
            source = bundle.extractfile(member)
            if source is None:
                raise SystemExit(f"{wanted} não pôde ser lido em {url}")
            with destination.open("wb") as target:
                shutil.copyfileobj(source, target)

    destination.chmod(destination.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"  -> {destination} ({destination.stat().st_size / 1e6:.1f} MB)", flush=True)


LICENSE_HEADER = """Licencas das ferramentas embutidas no PenDriveMaker
=======================================================

Este executavel embute dois programas de terceiros, executados como processos
separados pelo yt-dlp:

  ffmpeg  - build estatico "gpl" de https://github.com/BtbN/FFmpeg-Builds
            Licenca GPL. Codigo-fonte: https://ffmpeg.org/download.html e os
            scripts de build em https://github.com/BtbN/FFmpeg-Builds
  deno    - https://github.com/denoland/deno  (licenca MIT)

O PenDriveMaker em si e distribuido sob a licenca MIT; veja LICENSE no
repositorio https://github.com/TGF231/Pen-Drive-Maker

A seguir, o texto de licenca que acompanha cada build.
"""


def collect_license(archive: bytes, url: str) -> str:
    """Extrai o texto de licenca que vem dentro do arquivo baixado."""
    names_wanted = ("license", "licence", "copying")
    try:
        if url.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                for name in bundle.namelist():
                    stem = Path(name).name.lower()
                    if any(stem.startswith(w) for w in names_wanted):
                        return bundle.read(name).decode("utf-8", "replace")
        else:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:xz") as bundle:
                for member in bundle.getmembers():
                    stem = Path(member.name).name.lower()
                    if member.isfile() and any(stem.startswith(w) for w in names_wanted):
                        handle = bundle.extractfile(member)
                        if handle is not None:
                            return handle.read().decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile, tarfile.TarError):
        pass
    return "(texto de licenca nao encontrado no arquivo baixado)"


def main() -> int:
    if len(sys.argv) != 3:
        return int(bool(print(__doc__)))
    platform, target = sys.argv[1], Path(sys.argv[2])
    if platform not in FFMPEG_URLS:
        raise SystemExit(f"plataforma desconhecida: {platform}")

    target.mkdir(parents=True, exist_ok=True)
    suffix = EXE_SUFFIX[platform]

    # O ffprobe é opcional: o yt-dlp usa o próprio ffmpeg quando ele falta, e
    # deixá-lo de fora economiza quase 90 MB no executável final.
    ffmpeg_archive = download(FFMPEG_URLS[platform])
    extract_member(
        ffmpeg_archive, FFMPEG_URLS[platform], f"ffmpeg{suffix}", target / f"ffmpeg{suffix}"
    )
    deno_archive = download(DENO_URLS[platform])
    extract_member(
        deno_archive, DENO_URLS[platform], f"deno{suffix}", target / f"deno{suffix}"
    )

    # A licença precisa acompanhar o binário; o ffmpeg embutido é GPL.
    licenses = target / "THIRD-PARTY-LICENSES.txt"
    licenses.write_text(
        LICENSE_HEADER
        + "\n\n===== ffmpeg =====\n"
        + collect_license(ffmpeg_archive, FFMPEG_URLS[platform])
        + "\n\n===== deno =====\n"
        + collect_license(deno_archive, DENO_URLS[platform]),
        encoding="utf-8",
    )
    print(f"  -> {licenses}", flush=True)

    total = sum(p.stat().st_size for p in target.iterdir() if p.is_file())
    print(f"total embutido: {total / 1e6:.1f} MB", flush=True)
    if os.environ.get("GITHUB_ENV"):
        with open(os.environ["GITHUB_ENV"], "a", encoding="utf-8") as env:
            env.write(f"PDM_BUNDLE_DIR={target.resolve()}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
