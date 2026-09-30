"""Leitura dos metadados do YouTube Music e montagem da árvore de pastas.

Estrutura final: <saída>/<Gênero>/<Artista>/<Álbum>/<NN - Título>.<ext>
Os nomes são saneados para FAT32/exFAT, que é o formato usado em pen drives
automotivos e o mais restritivo dos sistemas de arquivo em uso.
"""

from __future__ import annotations

import os.path
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, Optional

# Caracteres proibidos em FAT32/NTFS, mais os de controle.
_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTI_SPACE = re.compile(r"\s+")
_TOPIC_SUFFIX = re.compile(r"\s*-\s*(Topic|Tópico)$", re.IGNORECASE)
_VARIOUS = re.compile(r"^(various artists|va|unknown|n/?a)$", re.IGNORECASE)

# Nomes reservados no Windows; alguns rádios automotivos herdam a mesma restrição.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Marcadores de participação. Removemos a parte do convidado para que a faixa
# caia na pasta do artista principal, e não em uma pasta nova por convidado.
# Não dividimos em vírgula nem "&": quebraria nomes como "Earth, Wind & Fire".
_FEATURING = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring|com|with|w/)\s+",
    re.IGNORECASE,
)


@dataclass
class TrackTags:
    genre: str
    artist: str
    album: str
    title: str
    track_number: Optional[int] = None

    def folder_parts(self) -> list[str]:
        return [self.genre, self.artist, self.album]


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def sanitize_component(name: str, ascii_only: bool = True, max_len: int = 60) -> str:
    """Transforma um texto livre em um nome de pasta/arquivo seguro."""
    name = (name or "").strip()
    if ascii_only:
        name = strip_accents(name)
        # Símbolos fora do ASCII imprimível viram espaço em vez de sumirem,
        # para não grudar palavras.
        name = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in name)
    name = _INVALID_CHARS.sub(" ", name)
    name = _MULTI_SPACE.sub(" ", name).strip()
    # FAT não aceita ponto nem espaço no fim do nome.
    name = name.rstrip(". ")
    if name.upper() in _RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    return name or "Desconhecido"


def _first(info: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = info.get(key)
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _clean_artist(raw: str) -> str:
    raw = _TOPIC_SUFFIX.sub("", raw).strip()
    if not raw or _VARIOUS.match(raw):
        return ""
    primary = _FEATURING.split(raw)[0].strip(" -–—")
    return primary or raw


def resolve_tags(info: Dict[str, Any], default_genre: str, default_album: str) -> TrackTags:
    """Extrai gênero/artista/álbum do dicionário de informações do yt-dlp.

    O YouTube Music raramente expõe gênero; quando falta, cai no valor padrão
    configurado para que a estrutura de pastas continue previsível.
    """
    genre = _first(info, "genre", "genres")
    if not genre:
        categories = info.get("categories") or []
        # "Music" é a categoria de praticamente todo vídeo do YT Music: inútil.
        genre = next(
            (c for c in categories if isinstance(c, str) and c.strip().lower() != "music"),
            "",
        )
    genre = genre or default_genre

    artist = ""
    # "artists" (lista) vem antes de "artist" (string com vírgulas) porque o
    # primeiro elemento da lista já é o artista principal, sem ambiguidade.
    for key in ("album_artist", "artists", "artist", "creator", "composer", "uploader", "channel"):
        artist = _clean_artist(_first(info, key))
        if artist:
            break
    artist = artist or "Artista Desconhecido"

    album = _first(info, "album") or default_album
    title = _first(info, "track", "title") or info.get("id") or "faixa"

    track_number = info.get("track_number")
    try:
        track_number = int(track_number) if track_number is not None else None
    except (TypeError, ValueError):
        track_number = None

    return TrackTags(
        genre=genre,
        artist=artist,
        album=album,
        title=title,
        track_number=track_number,
    )


def build_relative_path(tags: TrackTags, ascii_only: bool = True, max_len: int = 60) -> str:
    """Caminho relativo (sem extensão) dentro do diretório de saída."""
    parts = [sanitize_component(p, ascii_only, max_len) for p in tags.folder_parts()]
    stem = sanitize_component(tags.title, ascii_only, max_len)
    if tags.track_number:
        stem = f"{tags.track_number:02d} - {stem}"
        stem = stem[: max_len + 5].rstrip(". ")
    return "/".join(parts + [stem])


def preview_path(
    root: str,
    ascii_only: bool = True,
    extension: str = "ext",
    max_len: int = 60,
    path_module=os.path,
) -> str:
    """Caminho de exemplo para a interface, com o saneamento real aplicado.

    `path_module` existe para os testes: permite montar o exemplo com as regras
    do Windows (ntpath) rodando em qualquer sistema.
    """
    root = (root or "").strip().rstrip("/\\") or "<saída>"
    parts = [sanitize_component(p, ascii_only, max_len) for p in ("Gênero", "Artista", "Álbum")]
    stem = sanitize_component("01 - Título", ascii_only, max_len)
    joined = path_module.sep.join([root, *parts, f"{stem}.{extension}"])
    # normpath uniformiza o separador: no Windows o QFileDialog devolve o
    # caminho com "/", e sem isto o exemplo sairia com os dois misturados.
    return path_module.normpath(joined)
