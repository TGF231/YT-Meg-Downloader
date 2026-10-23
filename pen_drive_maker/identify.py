"""Reconhece arquivos soltos na biblioteca e vincula cada um a uma URL do YouTube.

Um arquivo sem a tag de origem não é baixado por este programa. Aqui o nome
("Artista - Título.mp3") ou as tags servem de pista: busca-se no YouTube e, se o
resultado bater com título, artista e duração, o arquivo entra no catálogo com
a URL. Resultados incertos nunca são vinculados sozinhos: voltam como dúvidas
para o usuário confirmar.

Depois de vinculado, o arquivo é "refinado": ganha tags corretas (com a URL de
origem), o nome ideal e a pasta ideal da estrutura configurada.
"""

from __future__ import annotations

import random
import re
import threading
import unicodedata
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TALB, TCON, TIT2, TPE1, TRCK
from mutagen.mp3 import MP3
from mutagen.mp4 import MP4
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from .config import AppConfig
from .downloader import auth_opts
from .genre import GenreLookup
from .library import (
    Progresso, _avisar, _destino_livre, _mover, _parar, read_tags, remove_empty_dirs,
)
from .organizer import TrackTags, build_relative_path, resolve_tags
from .store import STATUS_DONE, QueueStore

# Acima disso o vínculo é automático; entre DUVIDA e AUTO, o usuário decide.
LIMIAR_AUTO = 0.85
LIMIAR_DUVIDA = 0.55

# Palavras que enfeitam títulos de vídeo e não dizem qual é a música.
_RUIDO = {
    "official", "video", "videoclipe", "audio", "lyrics", "lyric", "letra", "hd", "4k",
    "clipe", "oficial", "topic", "music", "visualizer", "remastered", "remaster",
}
_PREFIXO_FAIXA = re.compile(r"^\s*\d{1,3}\s*[-._)]?\s+")
_ENTRE_PARENTESES = re.compile(r"[\(\[][^\)\]]*[\)\]]")

Busca = Callable[[str], List[Dict[str, object]]]


@dataclass
class Palpite:
    artista: str
    titulo: str
    duracao: Optional[float] = None

    @property
    def consulta(self) -> str:
        return f"{self.artista} {self.titulo}".strip()


@dataclass
class Combinacao:
    relpath: str
    palpite: Palpite
    video_id: str
    url: str
    titulo_achado: str
    canal: str
    pontos: float


@dataclass
class IdentifyReport:
    linked: List[Combinacao] = field(default_factory=list)
    doubtful: List[Combinacao] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)
    duplicates: List[Combinacao] = field(default_factory=list)  # música já na biblioteca
    failed: int = 0           # buscas que deram erro (rede, bloqueio)
    interrupted: bool = False


# ------------------------------------------------------------------ texto


def _tokens(texto: str) -> List[str]:
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c)).casefold()
    return [t for t in re.findall(r"[a-z0-9]+", texto) if t not in _RUIDO]


def _limpar_titulo(texto: str) -> str:
    texto = _ENTRE_PARENTESES.sub(" ", texto).replace("_", " ")
    return re.sub(r"\s+", " ", texto).strip(" -")


def palpite_do_arquivo(caminho: Path) -> Palpite:
    """Artista, título e duração: das tags quando completas, senão do nome."""
    tags = read_tags(caminho)
    artista = str(tags.get("artist", "")).strip()
    titulo = str(tags.get("title", "")).strip()
    if not (artista and titulo):
        nome = _PREFIXO_FAIXA.sub("", caminho.stem.replace("_", " "))
        for separador in (" - ", " – ", " — "):
            if separador in nome:
                a, t = nome.split(separador, 1)
                artista = artista or a.strip()
                titulo = titulo or t.strip()
                break
        else:
            titulo = titulo or nome.strip()
    duracao = None
    try:
        arquivo = mutagen.File(str(caminho))
        if arquivo is not None and arquivo.info is not None:
            duracao = float(arquivo.info.length) or None
    except Exception:
        pass
    return Palpite(_limpar_titulo(artista), _limpar_titulo(titulo), duracao)


def pontuar(palpite: Palpite, candidato: Dict[str, object]) -> float:
    """0 a 1: quanto o resultado da busca parece ser a música do arquivo."""
    titulo_alvo = set(_tokens(palpite.titulo))
    if not titulo_alvo:
        return 0.0
    titulo_achado = set(_tokens(str(candidato.get("title") or "")))
    canal = set(_tokens(str(candidato.get("channel") or candidato.get("uploader") or "")))
    achado = titulo_achado | canal
    cobertura_titulo = len(titulo_alvo & titulo_achado) / len(titulo_alvo)
    artista_alvo = set(_tokens(palpite.artista))
    if artista_alvo:
        cobertura_artista = len(artista_alvo & achado) / len(artista_alvo)
        pontos = 0.65 * cobertura_titulo + 0.35 * cobertura_artista
    else:
        pontos = 0.85 * cobertura_titulo  # sem artista não dá para passar de 0,85
    # Versões ao vivo, remixes e covers têm o mesmo título e outra duração.
    esperado, real = palpite.duracao, candidato.get("duration")
    if esperado and isinstance(real, (int, float)) and real:
        diferenca = abs(float(real) - esperado)
        if diferenca <= 5:
            pontos += 0.10
        elif diferenca > 20:
            pontos -= 0.35
    return max(0.0, min(1.0, pontos))


# ------------------------------------------------------------------ busca


def buscar_no_youtube(consulta: str, limite: int = 5) -> List[Dict[str, object]]:
    """Resultados do YouTube pela API do yt-dlp, sem baixar nada."""
    from yt_dlp import YoutubeDL

    opcoes = {
        "quiet": True, "no_warnings": True, "extract_flat": True, "skip_download": True,
        "noplaylist": True,
    }
    with YoutubeDL(opcoes) as ydl:
        info = ydl.extract_info(f"ytsearch{limite}:{consulta}", download=False)
    return [e for e in (info or {}).get("entries") or [] if e and e.get("id")]


# ------------------------------------------------------------ varredura


def find_loose(
    store: QueueStore,
    root: Path,
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
) -> List[str]:
    """Arquivos de áudio da biblioteca que o catálogo não conhece."""
    from .library import scan

    indice = scan(root, progresso, stop)
    conhecidos = {i.relpath for i in store.list_items() if i.relpath}
    achados = set(indice.found.values())
    return [r for r in indice.unknown if r not in conhecidos and r not in achados]


def identify(
    store: QueueStore,
    root: Path,
    relpaths: List[str],
    busca: Busca = buscar_no_youtube,
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
    pausa: float = 2.0,
    falhas_seguidas_max: int = 3,
) -> IdentifyReport:
    """Busca cada arquivo; vincula os certos e devolve os duvidosos."""
    relatorio = IdentifyReport()
    root = Path(root)
    catalogo = {i.video_id: i for i in store.list_items()}
    seguidas = 0
    for indice, relativo in enumerate(relpaths, 1):
        if _parar(stop):
            relatorio.interrupted = True
            break
        _avisar(progresso, indice, len(relpaths), f"Buscando {relativo}")
        palpite = palpite_do_arquivo(root / relativo)
        if not palpite.titulo:
            relatorio.not_found.append(relativo)
            continue
        try:
            candidatos = busca(palpite.consulta)
            seguidas = 0
        except Exception:
            relatorio.failed += 1
            relatorio.not_found.append(relativo)
            seguidas += 1
            if seguidas >= falhas_seguidas_max:
                # Muitas falhas seguidas é rede caída ou bloqueio: insistir só piora.
                relatorio.interrupted = True
                break
            continue
        melhor, pontos = None, 0.0
        for candidato in candidatos:
            p = pontuar(palpite, candidato)
            if p > pontos:
                melhor, pontos = candidato, p
        if melhor is None or pontos < LIMIAR_DUVIDA:
            relatorio.not_found.append(relativo)
        else:
            video_id = str(melhor["id"])
            combinacao = Combinacao(
                relpath=relativo, palpite=palpite, video_id=video_id,
                url=str(melhor.get("url") or f"https://www.youtube.com/watch?v={video_id}"),
                titulo_achado=str(melhor.get("title") or ""),
                canal=str(melhor.get("channel") or melhor.get("uploader") or ""),
                pontos=pontos,
            )
            existente = catalogo.get(video_id)
            if existente is not None and existente.status == STATUS_DONE:
                relatorio.duplicates.append(combinacao)  # já tem esta música em outro arquivo
            elif pontos >= LIMIAR_AUTO:
                vincular(store, root, combinacao)
                catalogo[video_id] = store.get_item(video_id)
                relatorio.linked.append(combinacao)
            else:
                relatorio.doubtful.append(combinacao)
        if pausa and indice < len(relpaths) and stop is not None:
            stop.wait(pausa * random.uniform(0.7, 1.4))
    return relatorio


def vincular(store: QueueStore, root: Path, combinacao: Combinacao) -> None:
    """Põe o arquivo no catálogo com a URL achada, fixado onde está."""
    tags = read_tags(Path(root) / combinacao.relpath)
    canal = re.sub(r"\s*-\s*topic$", "", combinacao.canal, flags=re.IGNORECASE).strip()
    store.adopt(
        combinacao.video_id,
        combinacao.url,
        combinacao.relpath,
        title=str(tags.get("title") or combinacao.palpite.titulo),
        artist=str(tags.get("artist") or combinacao.palpite.artista or canal),
        album=str(tags.get("album", "")),
        genre=str(tags.get("genre", "")),
        track_number=tags.get("track"),  # type: ignore[arg-type]
    )


# ------------------------------------------------------- organizar o arquivo


@dataclass
class Refinado:
    relpath: str           # onde o arquivo ficou
    tags: TrackTags
    gravou_tags: bool      # False: formato que não sabemos gravar (o arquivo foi só movido)
    movido: bool


def info_do_youtube(cfg: AppConfig) -> Callable[[str], Dict[str, object]]:
    """Leitor de metadados de um vídeo (sem baixar), com os cookies da configuração."""
    from yt_dlp import YoutubeDL

    def ler(url: str) -> Dict[str, object]:
        opcoes: Dict[str, object] = {
            "quiet": True, "no_warnings": True, "skip_download": True, "noplaylist": True,
            # só queremos os metadados: não falhar por falta de formato
            "ignore_no_formats_error": True,
        }
        if cfg.allow_remote_components:
            opcoes["remote_components"] = ["ejs:github", "ejs:npm"]
        opcoes.update(auth_opts(cfg))
        with YoutubeDL(opcoes) as ydl:
            return ydl.extract_info(url, download=False) or {}

    return ler


def write_tags(caminho: Path, tags: TrackTags, url: str) -> bool:
    """Grava título, artista, álbum, gênero, faixa e a URL de origem no arquivo.

    A URL vai no campo de comentário, como o yt-dlp faz: é ela que permite
    reconhecer o arquivo mesmo se o catálogo se perder. Outros campos do
    arquivo (capa embutida, por exemplo) são preservados. Devolve False para
    formatos que não sabemos gravar.
    """
    try:
        arquivo = mutagen.File(str(caminho))
        if arquivo is None:
            return False
        faixa = str(tags.track_number) if tags.track_number else ""
        if isinstance(arquivo, MP3):
            quadros = ID3(str(caminho)) if arquivo.tags is not None else ID3()
            for chave in ("TIT2", "TPE1", "TALB", "TCON", "TRCK"):
                quadros.delall(chave)
            quadros.delall("COMM")
            quadros.add(TIT2(encoding=3, text=tags.title))
            quadros.add(TPE1(encoding=3, text=tags.artist))
            quadros.add(TALB(encoding=3, text=tags.album))
            quadros.add(TCON(encoding=3, text=tags.genre))
            if faixa:
                quadros.add(TRCK(encoding=3, text=faixa))
            quadros.add(COMM(encoding=3, lang="eng", desc="", text=url))
            quadros.save(str(caminho), v2_version=3)  # v2.3: o que os rádios antigos leem
            return True
        if isinstance(arquivo, MP4):
            if arquivo.tags is None:
                arquivo.add_tags()
            arquivo.tags["\xa9nam"] = [tags.title]
            arquivo.tags["\xa9ART"] = [tags.artist]
            arquivo.tags["\xa9alb"] = [tags.album]
            arquivo.tags["\xa9gen"] = [tags.genre]
            arquivo.tags["\xa9cmt"] = [url]
            if tags.track_number:
                arquivo.tags["trkn"] = [(int(tags.track_number), 0)]
            arquivo.save()
            return True
        if isinstance(arquivo, (FLAC, OggOpus, OggVorbis)):
            if arquivo.tags is None:
                arquivo.add_tags()
            arquivo["title"] = [tags.title]
            arquivo["artist"] = [tags.artist]
            arquivo["album"] = [tags.album]
            arquivo["genre"] = [tags.genre]
            arquivo["comment"] = [url]
            if faixa:
                arquivo["tracknumber"] = [faixa]
            arquivo.save()
            return True
    except Exception:
        return False
    return False


def refinar(
    store: QueueStore,
    root: Path,
    combinacao: Combinacao,
    cfg: AppConfig,
    ler_info: Callable[[str], Dict[str, object]],
    consulta_genero: Optional[GenreLookup] = None,
) -> Refinado:
    """Dá ao arquivo vinculado as tags certas, o nome ideal e a pasta ideal.

    Os metadados vêm do YouTube (como numa faixa baixada) e seguem a mesma
    regra de gênero e estrutura de pastas da configuração. Quando o YouTube não
    traz título ou artista de música (só o título do vídeo), vale o que o
    arquivo já dizia.
    """
    root = Path(root)
    origem = root / combinacao.relpath
    info = ler_info(combinacao.url)
    palpite = combinacao.palpite
    tags = resolve_tags(
        info, cfg.default_genre, cfg.default_album, cfg.genre_source,
        # o gênero que o arquivo já tinha serve de reserva
        str(read_tags(origem).get("genre", "")),
    )
    if not info.get("track") and palpite.titulo:
        tags = replace(tags, title=palpite.titulo)
    if not (info.get("artist") or info.get("artists") or info.get("album_artist")) \
            and palpite.artista:
        tags = replace(tags, artist=palpite.artista)
    if consulta_genero is not None and not consulta_genero.disabled \
            and cfg.genre_source == "online":
        encontrado = consulta_genero.lookup(tags.artist, tags.title)
        if encontrado:
            tags = replace(tags, genre=encontrado)

    gravou = write_tags(origem, tags, f"https://www.youtube.com/watch?v={combinacao.video_id}")

    novo = build_relative_path(tags, cfg.ascii_only, cfg.max_component_len,
                               cfg.folder_structure) + origem.suffix
    destino = root / novo
    movido = destino.resolve() != origem.resolve()
    if movido:
        destino = _destino_livre(destino, combinacao.video_id)
        _mover(origem, destino)
        remove_empty_dirs(root, [origem.parent])
    relativo = destino.relative_to(root).as_posix() if movido else combinacao.relpath
    store.set_location(combinacao.video_id, relativo, pinned=False)
    store.set_tags(combinacao.video_id, tags.artist, tags.album, tags.genre, tags.track_number)
    return Refinado(relativo, tags, gravou, movido)


@dataclass
class LoteRefinado:
    feitos: List[tuple] = field(default_factory=list)    # (Combinacao, Refinado)
    falhas: List[tuple] = field(default_factory=list)    # (Combinacao, motivo)


def refinar_lote(
    store: QueueStore,
    root: Path,
    combinacoes: List[Combinacao],
    cfg: AppConfig,
    ler_info: Optional[Callable[[str], Dict[str, object]]] = None,
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
    pausa: float = 1.0,
    log: Optional[Callable[[str, str], None]] = None,
) -> LoteRefinado:
    """Refina cada combinação; uma que falhe continua vinculada, no lugar onde estava."""
    resultado = LoteRefinado()
    ler_info = ler_info or info_do_youtube(cfg)
    consulta = None
    if cfg.genre_source == "online":
        consulta = GenreLookup(store, group=cfg.group_subgenres, log=log)
    seguidas = 0
    for indice, combinacao in enumerate(combinacoes, 1):
        if _parar(stop):
            break
        _avisar(progresso, indice, len(combinacoes), f"Organizando {combinacao.relpath}")
        try:
            refinado = refinar(store, root, combinacao, cfg, ler_info, consulta)
            resultado.feitos.append((combinacao, refinado))
            seguidas = 0
        except Exception as erro:
            resultado.falhas.append((combinacao, f"{type(erro).__name__}: {erro}"))
            seguidas += 1
            if seguidas >= 3:
                break  # rede caída ou bloqueio: insistir só piora
        if pausa and stop is not None and indice < len(combinacoes):
            stop.wait(pausa * random.uniform(0.7, 1.4))
    return resultado
