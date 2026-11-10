"""Operações de biblioteca: reconhecer, conferir, mover, exportar e copiar.

O elo entre uma música e o lugar onde ela está é o **id do vídeo gravado
dentro do arquivo**. O yt-dlp põe a URL de origem nas tags `comment` e
`purl`, e este módulo a lê de volta. Por isso o caminho deixa de ser a única
pista: um arquivo movido à mão no Explorer continua sendo reconhecido, e uma
pasta montada à mão vira a estrutura oficial daquela faixa.

Nada aqui apaga arquivo sem que a função diga isso no nome.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

# Importado no topo de propósito: se faltar (num executável mal empacotado),
# o programa deve falhar ao abrir. Escondido num try, todo arquivo pareceria
# "sem origem" e músicas movidas seriam baixadas de novo, em silêncio.
import mutagen
import mutagen.mp3

from .organizer import TrackTags, build_relative_path
from .store import STATE_DIRNAME, STATUS_DONE, STATUS_EXCLUDED, QueueStore, file_present

AUDIO_EXTS = {
    ".mp3", ".m4a", ".opus", ".ogg", ".oga", ".webm", ".flac", ".aac",
    ".wav", ".mp4", ".wma", ".mka",
}

_VIDEO_ID = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^\s\"']*&)?v=|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
)

EXPORT_FORMAT = "pen-drive-maker/biblioteca"
EXPORT_VERSION = 1

Progresso = Optional[Callable[[int, int, str], None]]


def _avisar(progresso: Progresso, feito: int, total: int, texto: str) -> None:
    if progresso is not None:
        progresso(feito, total, texto)


def _parar(stop: Optional[threading.Event]) -> bool:
    return stop is not None and stop.is_set()


# ------------------------------------------------------------------ tags


def _textos_das_tags(tags) -> Iterable[str]:
    """Todos os valores de tag como texto, qualquer que seja o formato."""
    if not tags:
        return
    try:
        itens = tags.items()
    except AttributeError:
        return
    for _chave, valor in itens:
        if isinstance(valor, (list, tuple)):
            for parte in valor:
                yield str(parte)
        else:
            yield str(valor)


def read_video_id(path: Path) -> Optional[str]:
    """Id do vídeo de origem gravado no arquivo, ou None.

    Procura em todas as tags, não só em `comment`: o nome do campo muda de
    formato para formato (COMM no MP3, ©cmt no M4A, COMMENT no Opus).
    """
    try:
        arquivo = mutagen.File(str(path))
    except Exception:  # arquivo corrompido, formato exótico, permissão
        return None
    if arquivo is None:
        return None
    for texto in _textos_das_tags(arquivo.tags):
        achado = _VIDEO_ID.search(texto)
        if achado:
            return achado.group(1)
    return None


def read_tags(path: Path) -> Dict[str, object]:
    """Título, artista, álbum, gênero e faixa gravados no arquivo."""
    resultado: Dict[str, object] = {}
    try:
        arquivo = mutagen.File(str(path), easy=True)
    except Exception:  # arquivo corrompido, formato exótico, permissão
        return resultado
    if arquivo is None or not arquivo.tags:
        return resultado

    def primeiro(chave: str) -> str:
        try:
            valor = arquivo.tags.get(chave)
        except Exception:
            return ""
        if isinstance(valor, (list, tuple)):
            valor = valor[0] if valor else ""
        return str(valor or "").strip()

    for chave in ("title", "artist", "album", "genre"):
        valor = primeiro(chave)
        if valor:
            resultado[chave] = valor
    faixa = primeiro("tracknumber").split("/")[0]
    if faixa.isdigit():
        resultado["track"] = int(faixa)
    return resultado


# ---------------------------------------------------------------- varredura


@dataclass
class ScanResult:
    found: Dict[str, str] = field(default_factory=dict)      # id -> caminho relativo
    unknown: List[str] = field(default_factory=list)         # áudio sem id
    duplicates: Dict[str, List[str]] = field(default_factory=dict)


def _arquivos_de_audio(root: Path) -> List[Path]:
    achados = []
    for pasta, subpastas, arquivos in os.walk(root):
        # O catálogo e pastas ocultas não fazem parte da biblioteca.
        subpastas[:] = [s for s in subpastas if s != STATE_DIRNAME and not s.startswith(".")]
        for nome in arquivos:
            if Path(nome).suffix.lower() in AUDIO_EXTS:
                achados.append(Path(pasta) / nome)
    return achados


def scan(
    root: Path, progresso: Progresso = None, stop: Optional[threading.Event] = None
) -> ScanResult:
    """Lê o id de origem de cada arquivo de áudio da biblioteca."""
    root = Path(root)
    resultado = ScanResult()
    arquivos = _arquivos_de_audio(root)
    for indice, caminho in enumerate(arquivos, 1):
        if _parar(stop):
            break
        relativo = caminho.relative_to(root).as_posix()
        if indice % 25 == 0 or indice == len(arquivos):
            _avisar(progresso, indice, len(arquivos), f"Lendo {relativo}")
        video_id = read_video_id(caminho)
        if video_id is None:
            resultado.unknown.append(relativo)
        elif video_id in resultado.found:
            resultado.duplicates.setdefault(video_id, [resultado.found[video_id]]).append(relativo)
        else:
            resultado.found[video_id] = relativo
    return resultado


# ------------------------------------------------------------- conferência


@dataclass
class Report:
    moved: List[str] = field(default_factory=list)       # achadas em outro lugar
    missing: List[str] = field(default_factory=list)     # sumiram: voltaram à fila
    adopted: List[str] = field(default_factory=list)     # arquivos que o catálogo não tinha
    unknown: List[str] = field(default_factory=list)     # áudio sem id de origem
    duplicates: Dict[str, List[str]] = field(default_factory=dict)
    scanned: bool = False

    @property
    def changed(self) -> bool:
        return bool(self.moved or self.missing or self.adopted)


def reconcile(
    store: QueueStore,
    root: Path,
    full: bool = False,
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
) -> Report:
    """Põe o catálogo de acordo com o que está no disco.

    - Faixa salva que sumiu do lugar: se o arquivo estiver em outra pasta da
      biblioteca, o catálogo passa a apontar para lá e ela fica **fixada**
      (foi você quem a moveu). Se não estiver em lugar nenhum, volta à fila.
    - Com `full=True`, também adota arquivos que o catálogo não conhecia mas
      que trazem o id de origem — é assim que uma estrutura montada à mão,
      ou copiada de outro pen drive, entra na biblioteca.

    A varredura só acontece quando há algo a explicar ou quando pedida: ler
    as tags de milhares de arquivos num pen drive lento leva tempo.
    """
    root = Path(root)
    relatorio = Report()
    ausentes = store.missing_items()
    if not ausentes and not full:
        return relatorio

    indice = scan(root, progresso, stop)
    relatorio.scanned = True
    relatorio.duplicates = indice.duplicates

    for item in ausentes:
        novo = indice.found.get(item.video_id)
        if novo is not None:
            store.set_location(item.video_id, novo, pinned=True)
            relatorio.moved.append(item.video_id)
        else:
            store.requeue([item.video_id])
            relatorio.missing.append(item.video_id)

    if full:
        catalogo = {i.video_id: i for i in store.list_items()}
        for video_id, relativo in indice.found.items():
            item = catalogo.get(video_id)
            if item is not None and item.status == STATUS_DONE:
                continue  # já está certo, ou acabou de ser achado acima
            if video_id in relatorio.moved:
                continue
            tags = read_tags(root / relativo)
            store.adopt(
                video_id,
                item.url if item is not None else f"https://www.youtube.com/watch?v={video_id}",
                relativo,
                title=str(tags.get("title", "")),
                artist=str(tags.get("artist", "")),
                album=str(tags.get("album", "")),
                genre=str(tags.get("genre", "")),
                track_number=tags.get("track"),  # type: ignore[arg-type]
            )
            relatorio.adopted.append(video_id)
        # Arquivos já vinculados a uma URL à mão (sem tag) não são "desconhecidos".
        conhecidos = {i.relpath for i in store.list_items() if i.relpath}
        relatorio.unknown = [u for u in indice.unknown if u not in conhecidos]
    return relatorio


# --------------------------------------------------------- mover arquivos


def _destino_livre(destino: Path, video_id: str) -> Path:
    """Não sobrescreve: um nome ocupado ganha o id do vídeo."""
    if not destino.exists():
        return destino
    return destino.with_name(f"{destino.stem} [{video_id}]{destino.suffix}")


def remove_empty_dirs(root: Path, pastas: Iterable[Path]) -> int:
    """Apaga as pastas que ficaram vazias, subindo até a raiz da biblioteca."""
    root = Path(root).resolve()
    removidas = 0
    for pasta in sorted({Path(p).resolve() for p in pastas}, key=lambda p: -len(p.parts)):
        atual = pasta
        while atual != root and root in atual.parents and atual.name != STATE_DIRNAME:
            try:
                atual.rmdir()  # só funciona em pasta vazia
            except OSError:
                break
            removidas += 1
            atual = atual.parent
    return removidas


def _mover(origem: Path, destino: Path) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(origem), str(destino))


def move_to_folder(
    store: QueueStore, root: Path, video_ids: Iterable[str], pasta_relativa: str
) -> int:
    """Move faixas para uma pasta escolhida à mão e as fixa lá."""
    root = Path(root)
    pasta_relativa = pasta_relativa.strip().strip("/\\")
    alvo = (root / pasta_relativa) if pasta_relativa else root
    movidas, deixadas = 0, []
    for video_id in video_ids:
        item = store.get_item(video_id)
        if item is None:
            continue
        origem = store.location(item)
        if item.status != STATUS_DONE or origem is None or not origem.is_file():
            # Ainda não baixada: basta lembrar o lugar, o download vai para lá.
            nome = (origem.name if origem is not None else None) or f"{item.title or video_id}.mp3"
            store.set_location(video_id, (Path(pasta_relativa) / nome).as_posix(), pinned=True)
            continue
        destino = _destino_livre(alvo / origem.name, video_id)
        if destino.resolve() == origem.resolve():
            store.set_pinned([video_id], True)
            continue
        _mover(origem, destino)
        deixadas.append(origem.parent)
        store.set_location(video_id, destino.relative_to(root).as_posix(), pinned=True)
        movidas += 1
    remove_empty_dirs(root, deixadas)
    return movidas


@dataclass
class ReorgReport:
    moved: int = 0
    kept_pinned: int = 0
    missing: int = 0


def write_genre(path: Path, genero: str) -> bool:
    """Regrava só a tag de gênero (mp3, m4a, opus, ogg, flac). False se não deu."""
    try:
        arquivo = mutagen.File(str(path), easy=True)
        if arquivo is None:
            return False
        if arquivo.tags is None:
            arquivo.add_tags()
        arquivo["genre"] = [genero]
        if isinstance(arquivo, mutagen.mp3.MP3):
            arquivo.save(v2_version=3)  # a mesma versão que o resto do programa grava
        else:
            arquivo.save()
        return True
    except Exception:
        return False


def reorganize(
    store: QueueStore,
    root: Path,
    structure: str,
    ascii_only: bool = True,
    max_len: int = 60,
    default_genre: str = "Sem Genero",
    default_album: str = "Singles",
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
    regrupar: Optional[Callable[[str, str], Optional[str]]] = None,
    por_artista: bool = False,
) -> ReorgReport:
    """Move os arquivos já baixados para a estrutura de pastas atual.

    Evita rebaixar a biblioteca inteira só porque a estrutura mudou. Faixas
    fixadas (postas à mão) ficam onde estão. As tags vêm do catálogo e, se
    faltarem, do próprio arquivo — o artista e o álbum que o yt-dlp gravou.

    `regrupar(artista, título)` devolve o gênero pelas regras de agrupamento
    atuais: None = nada em cache (o gênero fica como está), "" = a resposta
    guardada não serve como pasta (use o gênero de reserva). É como uma mudança
    nas regras alcança o que já foi baixado.

    Com `por_artista`, as faixas de cada artista passam todas para o gênero que
    predomina entre elas. Sempre que o gênero muda, a tag dentro do arquivo é
    regravada, para a pasta e o que o rádio mostra não divergirem.
    """
    root = Path(root)
    relatorio = ReorgReport()
    faixas = store.list_items(STATUS_DONE)

    # 1ª passada: o que cada faixa deve ser, sem mexer em nada.
    plano = []  # (item, origem, tags)
    for indice, item in enumerate(faixas, 1):
        if _parar(stop):
            return relatorio
        _avisar(progresso, indice, len(faixas), item.title or item.video_id)
        if item.pinned:
            relatorio.kept_pinned += 1
            continue
        origem = store.location(item)
        if origem is None or not origem.is_file():
            relatorio.missing += 1
            continue
        do_arquivo: Dict[str, object] = {}
        if not (item.artist and item.title and item.album):
            do_arquivo = read_tags(origem)
        reserva = store.playlist_title_for(item.video_id) or default_genre
        tags = TrackTags(
            genre=item.genre or reserva,
            artist=item.artist or str(do_arquivo.get("artist", "")) or "Artista Desconhecido",
            album=item.album or str(do_arquivo.get("album", "")) or default_album,
            title=item.title or str(do_arquivo.get("title", "")) or item.video_id,
            track_number=item.track_number or do_arquivo.get("track"),  # type: ignore[arg-type]
        )
        if regrupar is not None:
            novo_genero = regrupar(tags.artist, tags.title)
            if novo_genero is not None:
                tags = replace(tags, genre=novo_genero or reserva)
        plano.append((item, origem, tags))

    if por_artista:
        contagem: Dict[str, Dict[str, int]] = {}
        for _item, _origem, tags in plano:
            artista = tags.artist.strip().lower()
            if artista != "artista desconhecido":
                contagem.setdefault(artista, {}).setdefault(tags.genre, 0)
                contagem[artista][tags.genre] += 1
        predominante = {
            # max() fica com o primeiro em caso de empate (a ordem é a de chegada)
            artista: max(generos, key=generos.get)  # type: ignore[arg-type]
            for artista, generos in contagem.items()
        }
        plano = [
            (item, origem, replace(tags, genre=predominante.get(tags.artist.strip().lower(),
                                                                tags.genre)))
            for item, origem, tags in plano
        ]

    # 2ª passada: gravar o gênero novo e mover.
    deixadas = []
    for item, origem, tags in plano:
        if _parar(stop):
            break
        if tags.genre != item.genre:
            store.set_tags(item.video_id, tags.artist, tags.album, tags.genre,
                           tags.track_number)
            write_genre(origem, tags.genre)
        novo = build_relative_path(tags, ascii_only, max_len, structure) + origem.suffix
        destino = root / novo
        if destino.resolve() == origem.resolve():
            continue
        destino = _destino_livre(destino, item.video_id)
        _mover(origem, destino)
        deixadas.append(origem.parent)
        store.set_location(item.video_id, destino.relative_to(root).as_posix(), pinned=False)
        relatorio.moved += 1
    remove_empty_dirs(root, deixadas)
    return relatorio


def remove_tracks(
    store: QueueStore, root: Path, video_ids: Iterable[str], apagar_arquivos: bool
) -> int:
    """Tira faixas da biblioteca. Com `apagar_arquivos`, apaga-as do disco.

    Elas ficam no catálogo como excluídas: uma atualização da playlist não
    as traz de volta.
    """
    ids = list(video_ids)
    deixadas = []
    for video_id in ids:
        item = store.get_item(video_id)
        onde = store.location(item) if item is not None else None
        if apagar_arquivos and onde is not None and onde.is_file():
            try:
                onde.unlink()
                deixadas.append(onde.parent)
            except OSError:
                pass
    store.exclude(ids)
    remove_empty_dirs(root, deixadas)
    return len(ids)


# ------------------------------------------------------- exportar/importar


def export_data(store: QueueStore) -> dict:
    playlists = store.list_playlists()
    vinculos = store.playlist_links()

    faixas = []
    for item in store.list_items():
        if item.status == STATUS_EXCLUDED:
            continue
        faixas.append({
            "video_id": item.video_id,
            "url": item.url,
            "titulo": item.title,
            "artista": item.artist or "",
            "album": item.album or "",
            "genero": item.genre or "",
            "faixa": item.track_number,
            "local": item.relpath or "",
            "fixada": bool(item.pinned),
            "playlists": [u for u in vinculos.get(item.video_id, []) if u],
        })
    return {
        "formato": EXPORT_FORMAT,
        "versao": EXPORT_VERSION,
        "exportado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "playlists": [{"url": row["url"], "titulo": row["title"] or ""} for row in playlists],
        "faixas": faixas,
    }


def export_library(store: QueueStore, destino: Path) -> int:
    """Grava a biblioteca num arquivo JSON portátil. Devolve quantas faixas."""
    dados = export_data(store)
    Path(destino).write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(dados["faixas"])


class ImportError_(ValueError):
    """Arquivo que não é uma biblioteca exportada por este programa."""


def import_data(store: QueueStore, dados: dict) -> tuple:
    """Acrescenta a biblioteca exportada. Devolve (novas, já_existentes).

    Cada faixa com local gravado fica **fixada** nele: importar serve para
    reproduzir a mesma estrutura, mesmo que a estrutura escolhida aqui seja
    outra. O que já estava nesta biblioteca não é tocado.
    """
    if not isinstance(dados, dict) or dados.get("formato") != EXPORT_FORMAT:
        raise ImportError_("O arquivo não é uma biblioteca do Pen Drive Maker.")

    ids_playlist = {}
    for playlist in dados.get("playlists") or []:
        url = (playlist or {}).get("url")
        if url:
            ids_playlist[url] = store.add_playlist(url, playlist.get("titulo") or "")

    novas = existentes = 0
    for faixa in dados.get("faixas") or []:
        video_id = (faixa or {}).get("video_id")
        if not video_id:
            continue
        entrada = {
            "video_id": video_id,
            "url": faixa.get("url") or f"https://www.youtube.com/watch?v={video_id}",
            "title": faixa.get("titulo") or "",
        }
        ligadas = [ids_playlist[u] for u in faixa.get("playlists") or [] if u in ids_playlist]
        antes = store.get_item(video_id)
        if ligadas:
            for pid in ligadas:
                store.add_items([entrada], pid)
        else:
            store.add_items([entrada])
        if antes is not None:
            existentes += 1
            continue
        novas += 1
        store.set_tags(
            video_id, faixa.get("artista") or "", faixa.get("album") or "",
            faixa.get("genero") or "", faixa.get("faixa"),
        )
        if faixa.get("local"):
            store.set_location(video_id, faixa["local"], pinned=True)
            # set_location não muda o status: a faixa segue na fila até baixar.
    return novas, existentes


def import_library(store: QueueStore, origem: Path) -> tuple:
    try:
        dados = json.loads(Path(origem).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erro:
        raise ImportError_(f"Não foi possível ler o arquivo: {erro}") from erro
    return import_data(store, dados)


# ------------------------------------------------------ copiar para outro


@dataclass
class CopyReport:
    copied: int = 0
    queued: int = 0
    failed: List[str] = field(default_factory=list)


def copy_library(
    store: QueueStore,
    destino: Path,
    copiar_arquivos: bool = True,
    progresso: Progresso = None,
    stop: Optional[threading.Event] = None,
) -> CopyReport:
    """Reproduz a biblioteca em outra pasta (outro pen drive), mesma estrutura.

    Copia os arquivos que existem aqui; o que faltar fica na fila de lá, para
    ser baixado no lugar exato. Com `copiar_arquivos=False`, só a estrutura
    vai — tudo será baixado de novo, útil quando este disco não está à mão.
    """
    destino = Path(destino)
    relatorio = CopyReport()
    alvo = QueueStore(destino / STATE_DIRNAME / "queue.db")
    try:
        import_data(alvo, export_data(store))
        faixas = store.list_items(STATUS_DONE)
        for indice, item in enumerate(faixas, 1):
            if _parar(stop):
                break
            _avisar(progresso, indice, len(faixas), item.title or item.video_id)
            origem = store.location(item)
            if not copiar_arquivos or origem is None or not file_present(str(origem)) \
                    or not item.relpath:
                continue
            if not origem.is_file():
                continue  # existe com outra extensão; fica para baixar de novo
            final = destino / item.relpath
            try:
                final.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(origem), str(final))
            except OSError as erro:
                relatorio.failed.append(f"{item.relpath}: {erro}")
                continue
            alvo.mark_done(
                item.video_id, str(final), item.genre or "", item.artist or "",
                item.album or "", item.title, item.track_number, pinned=True,
            )
            relatorio.copied += 1
        relatorio.queued = alvo.stats().pending + alvo.stats().failed
    finally:
        alvo.close()
    return relatorio

