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


# Quais níveis de pasta criar, sempre nesta ordem: gênero, artista, álbum.
# O que sai da pasta entra no nome do arquivo, para a faixa continuar
# identificável e a ordenação continuar fazendo sentido.
FOLDER_GENRE_ARTIST_ALBUM = "genre_artist_album"   # EDM/Avicii/True/02 - Wake Me Up
FOLDER_GENRE_ARTIST = "genre_artist"               # EDM/Avicii/Wake Me Up
FOLDER_GENRE = "genre"                             # EDM/Avicii - Wake Me Up
FOLDER_ARTIST = "artist"                           # Avicii/Wake Me Up
FOLDER_ALBUM = "album"                             # True/02 - Avicii - Wake Me Up

FOLDER_LEVELS = {
    FOLDER_GENRE_ARTIST_ALBUM: ("genre", "artist", "album"),
    FOLDER_GENRE_ARTIST: ("genre", "artist"),
    FOLDER_GENRE: ("genre",),
    FOLDER_ARTIST: ("artist",),
    FOLDER_ALBUM: ("album",),
}
FOLDER_STRUCTURES = tuple(FOLDER_LEVELS)


# De onde sai o nome da pasta de gênero.
GENRE_ONLINE = "online"            # consulta na internet (padrão)
GENRE_FROM_PLAYLIST = "playlist"   # nome da playlist
GENRE_FROM_METADATA = "metadata"   # metadados da faixa, com a playlist de reserva
GENRE_FIXED = "fixed"              # sempre o texto configurado
GENRE_SOURCES = (GENRE_ONLINE, GENRE_FROM_PLAYLIST, GENRE_FROM_METADATA, GENRE_FIXED)


@dataclass
class TrackTags:
    genre: str
    artist: str
    album: str
    title: str
    track_number: Optional[int] = None

    def level_values(self) -> dict:
        return {"genre": self.genre, "artist": self.artist, "album": self.album}

    def folder_parts(self, structure: str = FOLDER_GENRE_ARTIST_ALBUM) -> list[str]:
        """Níveis de pasta. O que sai daqui vai para o nome do arquivo."""
        valores = self.level_values()
        niveis = FOLDER_LEVELS.get(structure, FOLDER_LEVELS[FOLDER_GENRE_ARTIST_ALBUM])
        return [valores[n] for n in niveis]

    def file_stem(self, structure: str = FOLDER_GENRE_ARTIST_ALBUM) -> str:
        """Nome do arquivo, sem extensão, antes do saneamento.

        Duas regras, nesta ordem:

        - o número da faixa só aparece quando há pasta de álbum, porque fora
          dela ele não ordena nada — só intercala faixas de discos diferentes;
        - o artista entra no nome quando não houver pasta dele, senão a faixa
          fica órfã no meio de centenas de outras.

        O gênero nunca entra no nome: ele classifica, não identifica.
        """
        niveis = FOLDER_LEVELS.get(structure, FOLDER_LEVELS[FOLDER_GENRE_ARTIST_ALBUM])
        partes = []
        if "album" in niveis and self.track_number:
            partes.append(f"{self.track_number:02d}")
        if "artist" not in niveis and self.artist:
            partes.append(self.artist)
        partes.append(self.title)
        return " - ".join(partes)


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


def metadata_genre(info: Dict[str, Any]) -> str:
    """Gênero declarado nos metadados, se houver. Quase sempre vazio no YT Music."""
    genre = _first(info, "genre", "genres")
    if genre:
        return genre
    categories = info.get("categories") or []
    # "Music" é a categoria de praticamente todo vídeo do YT Music: inútil.
    return next(
        (c for c in categories if isinstance(c, str) and c.strip().lower() != "music"),
        "",
    )


def resolve_tags(
    info: Dict[str, Any],
    default_genre: str,
    default_album: str,
    genre_source: str = GENRE_ONLINE,
    playlist_genre: str = "",
    online_genre: str = "",
) -> TrackTags:
    """Extrai gênero/artista/álbum do dicionário de informações do yt-dlp.

    O YouTube Music quase nunca preenche o gênero, então por padrão a pasta usa
    o nome da playlist: quem monta uma playlist chamada "EDM" já classificou as
    faixas, e é essa classificação que serve ao pen drive.
    """
    playlist_genre = (playlist_genre or "").strip()
    online_genre = (online_genre or "").strip()
    if genre_source == GENRE_FIXED:
        genre = default_genre
    elif genre_source == GENRE_FROM_METADATA:
        genre = metadata_genre(info) or playlist_genre or default_genre
    elif genre_source == GENRE_ONLINE:
        genre = online_genre or metadata_genre(info) or playlist_genre or default_genre
    else:  # GENRE_FROM_PLAYLIST
        genre = playlist_genre or metadata_genre(info) or default_genre

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


def build_relative_path(
    tags: TrackTags,
    ascii_only: bool = True,
    max_len: int = 60,
    structure: str = FOLDER_GENRE_ARTIST_ALBUM,
) -> str:
    """Caminho relativo (sem extensão) dentro do diretório de saída."""
    parts = [
        sanitize_component(p, ascii_only, max_len)
        for p in tags.folder_parts(structure)
    ]
    # O nome pode trazer artista e número, então cabe um pouco mais que a pasta.
    stem = sanitize_component(tags.file_stem(structure), ascii_only, max_len + 24)
    return "/".join(parts + [stem])


def preview_path(
    root: str,
    ascii_only: bool = True,
    extension: str = "ext",
    genre: str = "Gênero",
    structure: str = FOLDER_GENRE_ARTIST_ALBUM,
    max_len: int = 60,
    path_module=os.path,
) -> str:
    """Caminho de exemplo para a interface, com o saneamento real aplicado.

    `path_module` existe para os testes: permite montar o exemplo com as regras
    do Windows (ntpath) rodando em qualquer sistema.
    """
    root = (root or "").strip().rstrip("/\\") or "<saída>"
    exemplo = TrackTags(
        genre=genre, artist="Artista", album="Álbum", title="Título", track_number=1
    )
    parts = [
        sanitize_component(p, ascii_only, max_len) for p in exemplo.folder_parts(structure)
    ]
    stem = sanitize_component(exemplo.file_stem(structure), ascii_only, max_len + 24)
    joined = path_module.sep.join([root, *parts, f"{stem}.{extension}"])
    # normpath uniformiza o separador: no Windows o QFileDialog devolve o
    # caminho com "/", e sem isto o exemplo sairia com os dois misturados.
    return path_module.normpath(joined)
