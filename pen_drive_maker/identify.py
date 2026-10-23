"""Reconhece arquivos soltos na biblioteca e vincula cada um a uma URL do YouTube.

Um arquivo sem a tag de origem não é baixado por este programa. Aqui o nome
("Artista - Título.mp3") ou as tags servem de pista: busca-se no YouTube e, se o
resultado bater com título, artista e duração, o arquivo entra no catálogo com
a URL. Resultados incertos nunca são vinculados sozinhos: voltam como dúvidas
para o usuário confirmar.
"""

from __future__ import annotations

import random
import re
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import mutagen

from .library import Progresso, _avisar, _parar, read_tags
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
