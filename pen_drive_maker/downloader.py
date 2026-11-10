"""Motor de extração e download, independente da interface gráfica.

Estratégia contra bloqueio do servidor:

1. A playlist é extraída uma única vez em modo "flat" (``extract_flat``), ou
   seja, sem resolver os metadados de cada faixa. Isso é uma requisição só,
   mesmo para playlists de milhares de músicas.
2. A fila vai para o disco (SQLite). Cada faixa só é resolvida e baixada
   quando chega a sua vez e depois de um intervalo mínimo com jitter.
3. Erros típicos de bloqueio (429, 403, verificação de robô) disparam pausa
   com backoff exponencial; bloqueios repetidos encerram a execução de forma
   limpa, com a fila intacta para retomada.
"""

from __future__ import annotations

import os
import queue
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.postprocessor.common import PostProcessor
from yt_dlp.cookies import CookieLoadError, load_cookies
from yt_dlp.utils import Popen

from .config import AppConfig
from .genre import GenreLookup
from .library import reconcile, reorganize
from .organizer import TrackTags, build_relative_path, metadata_genre, resolve_tags
from .tools import (  # reexportados: a interface e os testes importam daqui
    describe_missing_ejs,
    describe_missing_ffmpeg,
    describe_missing_js_runtime,
    ejs_package_version,
    find_ffmpeg,
    find_js_runtime,
)
from .store import STATE_DIRNAME, QueueItem, QueueStats, QueueStore

# --------------------------------------------------------------------------- #
# Classificação de erros
# --------------------------------------------------------------------------- #

# Sinais de que o servidor está limitando/bloqueando o cliente.
BLOCK_PATTERNS = [
    r"http error 429",
    r"too many requests",
    r"http error 403",
    r"\bforbidden\b",
    r"confirm you.{0,3}re not a bot",
    r"not a bot",
    r"this helps protect our community",
    r"rate[ -]?limit",
    r"temporarily blocked",
    r"blocked from",
    r"failed to extract any player response",
    r"unable to download api page",
    r"please solve the captcha",
    r"use --cookies",
]

# Faixa que nunca vai baixar: não adianta tentar de novo nem contar como bloqueio.
PERMANENT_PATTERNS = [
    r"video unavailable",
    r"private video",
    r"removed by the uploader",
    r"account associated with this video has been terminated",
    r"this video is not available",
    r"members[- ]only",
    r"join this channel",
    r"video has been removed",
    r"is not available in your country",
    r"who has blocked it",
    r"age[- ]restricted",
    r"confirm your age",
    r"requested format is not available",
]

_BLOCK_RE = re.compile("|".join(BLOCK_PATTERNS), re.IGNORECASE)
_PERMANENT_RE = re.compile("|".join(PERMANENT_PATTERNS), re.IGNORECASE)

# O YouTube exige a execução de um script JavaScript para liberar os links de
# áudio; sem um runtime instalado o yt-dlp falha sempre com estas mensagens.
JS_CHALLENGE_PATTERNS = [
    r"the page needs to be reloaded",
    r"signature solving failed",
    r"challenge solving failed",
    r"javascript runtime",
    r"challenge solver",
    r"wiki/ejs",
]
_JS_CHALLENGE_RE = re.compile("|".join(JS_CHALLENGE_PATTERNS), re.IGNORECASE)

ERROR_BLOCK = "block"
ERROR_PERMANENT = "permanent"
ERROR_TRANSIENT = "transient"
ERROR_JS_RUNTIME = "js_runtime"


def classify_error(message: str) -> str:
    text = message or ""
    if _JS_CHALLENGE_RE.search(text):
        return ERROR_JS_RUNTIME
    if _BLOCK_RE.search(text):
        return ERROR_BLOCK
    if _PERMANENT_RE.search(text):
        return ERROR_PERMANENT
    return ERROR_TRANSIENT


# Navegadores baseados em Chromium. Para todos eles o yt-dlp levanta o mesmo
# PermissionError (errno 13) no Windows quando não consegue copiar o banco de
# cookies, e a mensagem dele diz "Chrome" mesmo que o navegador seja outro.
CHROMIUM_BROWSERS = {"chrome", "chromium", "edge", "brave", "opera", "vivaldi"}

# Cada navegador esconde num lugar diferente as opções que o mantêm vivo depois
# de fechar a janela, que é a causa usual do banco de cookies travado.
BROWSER_CLOSE_HINTS = {
    "edge": (
        "O Edge continua rodando depois que a janela fecha. Em "
        "edge://settings/system desligue \"Aumento de inicialização\" e "
        "\"Continuar executando extensões e aplicativos em segundo plano quando o "
        "Microsoft Edge estiver fechado\", feche o Edge e encerre os processos "
        "msedge.exe no Gerenciador de Tarefas."
    ),
    "chrome": (
        "Em chrome://settings/system desligue \"Continuar executando apps em "
        "segundo plano quando o Google Chrome estiver fechado\", feche o Chrome e "
        "encerre os processos chrome.exe no Gerenciador de Tarefas."
    ),
    "brave": (
        "Em brave://settings/system desligue a execução em segundo plano, feche o "
        "Brave e encerre os processos brave.exe no Gerenciador de Tarefas."
    ),
}
BROWSER_LABELS = {
    "chrome": "Google Chrome",
    "chromium": "Chromium",
    "edge": "Microsoft Edge",
    "brave": "Brave",
    "opera": "Opera",
    "vivaldi": "Vivaldi",
    "firefox": "Firefox",
    "safari": "Safari",
}
GENERIC_CLOSE_HINT = (
    "Feche o navegador por completo e encerre os processos dele que continuarem "
    "no Gerenciador de Tarefas: enquanto ele roda, o banco de cookies fica "
    "travado."
)


def describe_cookie_failure(cfg: "AppConfig", details: List[str]) -> str:
    """Mensagem acionável para uma falha ao carregar cookies."""
    detail = " ".join(d.strip() for d in details if d.strip())
    key = (cfg.cookies_browser or "").strip().lower()
    browser = BROWSER_LABELS.get(key, key.capitalize())
    lines: List[str] = []

    if cfg.cookies_mode == "browser":
        lines.append(f"Não foi possível ler os cookies do {browser or 'navegador'}.")
        if key in CHROMIUM_BROWSERS:
            lines.append(f"1) {BROWSER_CLOSE_HINTS.get(key, GENERIC_CLOSE_HINT)}")
            lines.append(
                "2) Se continuar falhando no Windows: os navegadores baseados em "
                "Chromium (Chrome, Edge, Brave, Opera, Vivaldi) passaram a guardar os "
                "cookies com uma criptografia ligada ao aplicativo que o yt-dlp não "
                "consegue abrir. Nesse caso use o Firefox ou um arquivo cookies.txt."
            )
        else:
            lines.append(f"1) {GENERIC_CLOSE_HINT}")
            lines.append(
                f"2) Confirme que existe um perfil do {browser or 'navegador'} nesta "
                "máquina, com a conta do YouTube Music logada."
            )
        lines.append(
            "3) Para exportar um cookies.txt, use uma extensão de navegador que gere o "
            "formato Netscape, salve o arquivo e escolha 'Arquivo cookies.txt' aqui."
        )
    elif cfg.cookies_mode == "file":
        lines.append(f"Não foi possível ler o arquivo de cookies: {cfg.cookies_file}")
        lines.append(
            "Confirme que o arquivo existe, que está no formato Netscape (a primeira "
            "linha costuma ser '# Netscape HTTP Cookie File') e que não está aberto "
            "em outro programa."
        )
    else:
        lines.append("Falha ao carregar cookies.")

    lines.append(
        "Você também pode seguir sem cookies: aumente o intervalo entre downloads, "
        "porque a chance de bloqueio fica maior."
    )
    if detail:
        lines.append(f"Detalhe do yt-dlp: {detail}")
        # O yt-dlp usa a palavra "Chrome" nessa mensagem para qualquer navegador
        # baseado em Chromium; sem essa nota o usuário acha que selecionou errado.
        if "chrome cookie database" in detail.lower() and key != "chrome":
            lines.append(
                f"(A mensagem acima cita o Chrome, mas o yt-dlp escreve isso para "
                f"qualquer navegador Chromium. O navegador consultado foi o {browser}.)"
            )
    return "\n".join(lines)


def check_cookies(cfg: "AppConfig") -> Optional[str]:
    """Carrega os cookies configurados sem tocar na rede.

    Retorna None se estiver tudo certo, ou a mensagem de erro já explicada.
    Roda antes da extração para não descobrir o problema no meio da playlist.
    """
    if cfg.cookies_mode == "none":
        return None

    details: List[str] = []
    # Só interessam avisos e erros; o yt-dlp também loga o andamento normal.
    probe_logger = _YdlLogger(
        lambda level, message: details.append(message) if level in ("warn", "error") else None
    )
    auth: Dict[str, Any] = {}
    if cfg.cookies_mode == "file" and cfg.cookies_file:
        auth["cookiefile"] = str(Path(cfg.cookies_file).expanduser())
    elif cfg.cookies_mode == "browser" and cfg.cookies_browser:
        auth["cookiesfrombrowser"] = (cfg.cookies_browser, None, None, None)
    if not auth:
        return None

    try:
        with YoutubeDL({"quiet": True, "no_warnings": True, "logger": probe_logger}) as ydl:
            jar = load_cookies(auth.get("cookiefile"), auth.get("cookiesfrombrowser"), ydl)
    except Exception:
        return describe_cookie_failure(cfg, details)

    if len(jar) == 0:
        return describe_cookie_failure(
            cfg, details + ["nenhum cookie foi encontrado na origem escolhida"]
        )
    return None


def auth_opts(cfg: "AppConfig") -> Dict[str, Any]:
    """Opções de cookies do yt-dlp para a configuração."""
    opts: Dict[str, Any] = {}
    if cfg.cookies_mode == "file" and cfg.cookies_file:
        opts["cookiefile"] = str(Path(cfg.cookies_file).expanduser())
    elif cfg.cookies_mode == "browser" and cfg.cookies_browser:
        # Formato esperado: (navegador, perfil, keyring, container)
        opts["cookiesfrombrowser"] = (cfg.cookies_browser, None, None, None)
    return opts


class _TagInjector(PostProcessor):
    """Põe no dicionário da faixa os campos `meta_*` que o yt-dlp grava nas tags."""

    def __init__(self, campos: Dict[str, str]):
        super().__init__()
        self._campos = campos

    def run(self, info):
        info.update(self._campos)
        return [], info


class Stopped(Exception):
    """Parada solicitada pelo usuário."""


class CookieError(Exception):
    """Cookies configurados não puderam ser lidos: é problema de configuração."""


class EnvironmentProblem(Exception):
    """Falta algo na máquina (runtime JavaScript): repetir não resolve."""


class BlockedTooOften(Exception):
    """Bloqueios persistentes: encerramento limpo."""


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #


@dataclass
class EngineCallbacks:
    """Ganchos para a interface. Todos opcionais."""

    log: Callable[[str, str], None] = lambda level, message: None
    status: Callable[[str], None] = lambda text: None
    stats: Callable[[QueueStats], None] = lambda stats: None
    item_done: Callable[[str, TrackTags], None] = lambda path, tags: None
    file_progress: Callable[[float, str], None] = lambda percent, text: None
    finished: Callable[[str], None] = lambda reason: None


class _YdlLogger:
    """Roteia as mensagens do yt-dlp para o log da aplicação."""

    def __init__(self, emit: Callable[[str, str], None]):
        self._emit = emit

    def debug(self, msg: str) -> None:
        # O yt-dlp manda as linhas de "[download] ..." como debug; só interessam
        # as que não começam com o prefixo de depuração.
        if msg.startswith("[debug] "):
            return
        self._emit("debug", msg)

    def info(self, msg: str) -> None:
        self._emit("debug", msg)

    def warning(self, msg: str) -> None:
        self._emit("warn", msg)

    def error(self, msg: str) -> None:
        self._emit("error", msg)


# --------------------------------------------------------------------------- #
# Motor
# --------------------------------------------------------------------------- #


@dataclass
class _Job:
    """Faixa já baixada, esperando conversão, tags e mudança de pasta."""

    item: QueueItem
    info: Dict[str, Any]       # metadados do probe
    result: Dict[str, Any]     # o que o yt-dlp devolveu do download cru
    raw_path: str
    staging: Path
    position: int
    total: int
    started: float


class _PostWorker:
    """Converte e organiza as faixas enquanto os downloads seguem.

    Vários threads consomem a mesma fila. Ela é curta de propósito: se a
    conversão não acompanha o download, quem baixa espera, em vez de encher o
    disco de áudio cru.
    """

    def __init__(self, engine: "DownloadEngine", threads: int = 1, capacity: int = 3):
        self._engine = engine
        self._queue: "queue.Queue[Optional[_Job]]" = queue.Queue(maxsize=max(capacity, threads * 2))
        self._threads = [
            threading.Thread(target=self._run, name=f"conversao-{i + 1}", daemon=True)
            for i in range(threads)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, job: _Job) -> None:
        self._queue.put(job)  # bloqueia se a conversão está atrasada

    def close(self) -> None:
        """Termina o que já foi baixado e encerra as threads."""
        pendentes = self._queue.qsize()
        if pendentes:
            self._engine._log("info", f"Terminando {pendentes} faixa(s) já baixada(s)...")
            self._engine._status(f"Finalizando {pendentes} conversão(ões)...")
        for _ in self._threads:
            self._queue.put(None)
        for thread in self._threads:
            thread.join()

    def _run(self) -> None:
        engine = self._engine
        while True:
            job = self._queue.get()
            if job is None:
                return
            engine._converting_delta(+1)
            try:
                engine._complete(job)
            except Exception as exc:  # uma faixa ruim não derruba a conversão das outras
                engine._on_post_error(job.item, exc)
            finally:
                engine._converting_delta(-1)
                engine._release(job.item.video_id)
                engine._emit_stats()


class DownloadEngine:
    def __init__(
        self,
        config: AppConfig,
        store: QueueStore,
        callbacks: EngineCallbacks,
        allow_missing_js_runtime: bool = False,
    ):
        self.cfg = config
        self.store = store
        self.cb = callbacks
        # O usuário pode insistir quando a detecção do runtime não acha nada:
        # a busca cobre os lugares usuais, não todos os possíveis.
        self.allow_missing_js_runtime = allow_missing_js_runtime
        self._ffmpeg_path: Optional[str] = None
        self._playlist_genre: str = ""
        self._genre_lookup: Optional[GenreLookup] = None

        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()  # começa "não pausado"

        self._block_events = 0
        self._consecutive_failures = 0
        self._last_progress_ts = 0.0

        # Vários downloads e conversões ao mesmo tempo: tudo que as threads
        # dividem passa por um destes travas.
        self._lock = threading.RLock()          # contadores, faixas em andamento
        self._mudou = threading.Condition(self._lock)  # uma faixa saiu de andamento
        self._active: set = set()               # ids baixando ou convertendo
        self._halt = threading.Event()          # um erro fatal: ninguém pega faixa nova
        self._fatal: Optional[BaseException] = None
        self._block_until = 0.0                 # bloqueio detectado: todos esperam
        self._start_lock = threading.Lock()     # escalona o início dos downloads
        self._last_start = 0.0
        self._artist_lock = threading.Lock()
        self._artist_genres: Dict[str, str] = {}
        self._artistas: set = set()             # artistas vistos nesta execução
        self._move_lock = threading.Lock()      # só uma faixa por vez escolhe o destino
        self._percent: Dict[int, float] = {}    # progresso de cada download em curso
        self._converting = 0
        self._logger = _YdlLogger(self._log)

    # ------------------------------------------------------------- controles

    def stop(self) -> None:
        self._stop.set()
        self._resume.set()  # destrava quem estiver esperando na pausa

    def pause(self) -> None:
        self._resume.clear()

    def resume(self) -> None:
        self._resume.set()

    @property
    def is_paused(self) -> bool:
        return not self._resume.is_set()

    # ------------------------------------------------------------- utilidades

    def _log(self, level: str, message: str) -> None:
        self.cb.log(level, message.rstrip())

    def _status(self, text: str) -> None:
        self.cb.status(text)

    def _emit_stats(self) -> None:
        self.cb.stats(self.store.stats())

    def _check_control(self) -> None:
        """Bloqueia enquanto pausado; levanta Stopped se o usuário parou."""
        if self._stop.is_set():
            raise Stopped()
        if self.is_paused:
            self._status("Pausado")
            self._log("info", "Pausado pelo usuário.")
            while not self._resume.wait(timeout=0.25):
                if self._stop.is_set():
                    raise Stopped()
            if self._stop.is_set():
                raise Stopped()
            self._log("info", "Retomando.")

    def _sleep(self, seconds: float, label: str) -> None:
        """Espera interrompível, com contagem regressiva na barra de status."""
        deadline = time.monotonic() + seconds
        while True:
            self._check_control()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._status(f"{label} {self._fmt_duration(remaining)}")
            if self._stop.wait(timeout=min(1.0, remaining)):
                raise Stopped()

    @staticmethod
    def _fmt_duration(seconds: float) -> str:
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}s"
        minutes, secs = divmod(seconds, 60)
        if minutes < 60:
            return f"{minutes}min {secs:02d}s"
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes:02d}min"

    # -------------------------------------------------------- opções do yt-dlp

    def _auth_opts(self) -> Dict[str, Any]:
        return auth_opts(self.cfg)

    def _base_opts(self) -> Dict[str, Any]:
        opts: Dict[str, Any] = {
            "quiet": True,
            "no_warnings": False,
            "noprogress": True,
            "consoletitle": False,
            "logger": self._logger,
            "ignoreerrors": False,
            "noplaylist": True,
            "retries": 2,
            "fragment_retries": 4,
            "extractor_retries": 1,
            "socket_timeout": 30,
            # Espaça as requisições internas de um mesmo item (player, api, etc).
            "sleep_interval_requests": 1,
        }
        # Sem isto o yt-dlp procura o ffmpeg por conta própria no PATH, e volta a
        # falhar exatamente nos casos que a nossa busca resolveu.
        if self._ffmpeg_path:
            opts["ffmpeg_location"] = self._ffmpeg_path
        if self.cfg.allow_remote_components:
            opts["remote_components"] = ["ejs:github", "ejs:npm"]
        opts.update(self._auth_opts())
        return opts

    def _postprocessors(self) -> List[Dict[str, Any]]:
        pps: List[Dict[str, Any]] = []
        if self.cfg.audio_format != "best":
            pps.append(
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": self.cfg.audio_format,
                    # "0" = melhor qualidade disponível no codec escolhido.
                    "preferredquality": "0",
                    "nopostoverwrites": False,
                }
            )
        if self.cfg.embed_metadata:
            pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": False})
        if self.cfg.embed_thumbnail:
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
        return pps

    def _probe_opts(self) -> Dict[str, Any]:
        opts = self._base_opts()
        opts.update({"skip_download": True, "format": "bestaudio/best"})
        return opts

    def _download_opts(self, outtmpl: str) -> Dict[str, Any]:
        """Só baixa o áudio cru: converter e gravar tags é a etapa seguinte."""
        opts = self._base_opts()
        opts.update(
            {
                "format": "bestaudio/best",
                "outtmpl": {"default": outtmpl},
                "writethumbnail": self.cfg.embed_thumbnail,
                "overwrites": False,
                "continuedl": True,
                "progress_hooks": [self._progress_hook],
            }
        )
        return opts

    def _post_opts(self, marcas: Optional[list] = None) -> Dict[str, Any]:
        """Conversão, tags e capa: roda na thread de conversão, sem rede.

        `marcas` recebe (instante, status, etapa) de cada etapa do yt-dlp, para
        o log dizer onde o tempo da conversão foi.
        """
        def gancho(evento: Dict[str, Any]) -> None:
            if marcas is not None:
                marcas.append((time.monotonic(), evento.get("status"), evento.get("postprocessor")))
            self._postprocessor_hook(evento)

        opts = self._base_opts()
        opts.update(
            {
                "postprocessors": self._postprocessors(),
                "prefer_ffmpeg": True,
                "postprocessor_hooks": [gancho],
            }
        )
        return opts

    @staticmethod
    def _tempos(marcas: list) -> str:
        """"áudio 4.1s · tags 0.2s · capa 0.6s": quanto durou cada etapa da conversão."""
        nomes = {"ExtractAudio": "áudio", "Metadata": "tags", "EmbedThumbnail": "capa"}
        inicio: Dict[str, float] = {}
        gasto: Dict[str, float] = {}
        for instante, status, etapa in marcas:
            if status == "started":
                inicio[etapa] = instante
            elif status == "finished" and etapa in inicio:
                gasto[etapa] = gasto.get(etapa, 0.0) + instante - inicio.pop(etapa)
        return " · ".join(f"{rotulo} {gasto[chave]:.1f}s" for chave, rotulo in nomes.items()
                          if chave in gasto)

    def _progress_hook(self, event: Dict[str, Any]) -> None:
        if self._stop.is_set():
            # Interrompe o download em andamento; o yt-dlp propaga a exceção.
            raise Stopped()
        if event.get("status") != "downloading":
            return
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        done = event.get("downloaded_bytes") or 0
        with self._lock:
            self._percent[threading.get_ident()] = (done / total * 100.0) if total else 0.0
            agora = time.monotonic()
            if agora - self._last_progress_ts < 0.3:
                return
            self._last_progress_ts = agora
        self._emit_progress()

    def _emit_progress(self) -> None:
        """Uma barra para todos: média dos downloads em curso + quantos convertem."""
        with self._lock:
            ativos = list(self._percent.values())
            convertendo = self._converting
        media = sum(ativos) / len(ativos) if ativos else (100.0 if convertendo else 0.0)
        partes = []
        if ativos:
            partes.append(f"{len(ativos)} baixando ({media:.0f}%)")
        if convertendo:
            partes.append(f"{convertendo} convertendo")
        self.cb.file_progress(media, " · ".join(partes) or "ocioso")

    def _converting_delta(self, delta: int) -> None:
        with self._lock:
            self._converting += delta
        self._emit_progress()

    def _release(self, video_id: str) -> None:
        with self._lock:
            self._active.discard(video_id)
            self._mudou.notify_all()

    def _postprocessor_hook(self, event: Dict[str, Any]) -> None:
        if event.get("status") == "started":
            self._log("debug", f"Processando ({event.get('postprocessor')})")

    # ------------------------------------------------------------- extração

    def sync_with_disk(self, root_existed: bool = True) -> int:
        """Faixas marcadas como baixadas mas ausentes no disco voltam para a fila."""
        if not root_existed:
            if self.store.stats().done:
                self._log(
                    "warn",
                    "A pasta de saída não existia: não conferi os arquivos já baixados. "
                    "Se ela fica num pen drive, conecte-o e abra de novo.",
                )
            return 0
        relatorio = reconcile(self.store, Path(self.cfg.output_dir).expanduser())
        if relatorio.moved:
            self._log(
                "info",
                f"{len(relatorio.moved)} faixa(s) mudaram de pasta; a biblioteca agora "
                "aponta para onde elas estão.",
            )
        if relatorio.missing:
            self._log(
                "warn",
                f"{len(relatorio.missing)} faixa(s) estavam marcadas como baixadas, mas o "
                "arquivo não está mais no disco. Voltaram para a fila.",
            )
        if relatorio.changed:
            self._emit_stats()
        return len(relatorio.missing)

    def _verify_environment(self) -> None:
        runtime = find_js_runtime()
        if runtime is None:
            if not self.allow_missing_js_runtime:
                raise EnvironmentProblem(describe_missing_js_runtime())
            self._log("warn", "Nenhum runtime JavaScript encontrado; seguindo assim mesmo.")
        else:
            self._log("info", f"Runtime JavaScript: {runtime}")
        solver = ejs_package_version()
        if solver:
            self._log("info", f"Solucionador de desafios (yt-dlp-ejs): {solver}")
        elif self.cfg.allow_remote_components:
            self._log(
                "warn",
                "Pacote yt-dlp-ejs ausente; os scripts serão baixados durante o uso.",
            )
        else:
            for line in describe_missing_ejs().splitlines():
                self._log("warn", line)

        self._ffmpeg_path = find_ffmpeg(self.cfg.ffmpeg_path)
        if self._ffmpeg_path:
            self._log("info", f"ffmpeg: {self._ffmpeg_path}")
        else:
            for line in describe_missing_ffmpeg(self.cfg.ffmpeg_path).splitlines():
                self._log("warn", line)

    def _verify_cookies(self) -> None:
        if self.cfg.cookies_mode == "none":
            self._log("info", "Sem cookies: o risco de bloqueio é maior em playlists longas.")
            return
        self._status("Verificando cookies...")
        problem = check_cookies(self.cfg)
        if problem:
            raise CookieError(problem)
        self._log("info", "Cookies carregados.")

    def _extract_playlist(self, force: bool = False) -> None:
        url = self.cfg.playlist_url.strip()
        if not url:
            # Sem URL o trabalho é completar a biblioteca: o que veio de uma
            # importação, de uma cópia ou de faixas que sumiram do disco.
            self._log("info", "Sem URL de playlist: baixando o que falta na biblioteca.")
            return
        existing = self.store.get_playlist_by_url(url)
        if (
            not force
            and existing is not None
            and self.store.playlist_size(existing["id"]) > 0
        ):
            self._log(
                "info",
                f'Playlist "{existing["title"]}" já está na biblioteca '
                f"({self.store.playlist_size(existing['id'])} faixas). "
                "Use 'Atualizar lista da playlist' para buscar músicas novas.",
            )
            return

        self._status("Extraindo lista de faixas (modo flat)...")
        self._log("info", f"Extraindo playlist: {url}")
        opts = self._base_opts()
        opts.update(
            {
                # 'in_playlist' devolve só id/título/URL de cada faixa: uma
                # requisição para a playlist inteira, sem tocar em cada vídeo.
                "extract_flat": "in_playlist",
                "skip_download": True,
                "noplaylist": False,
                "ignoreerrors": True,
            }
        )
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError("Não foi possível extrair a playlist (resposta vazia).")

        entries = list(self._flatten_entries(info))
        if not entries:
            raise RuntimeError(
                "A playlist não retornou nenhuma faixa. Verifique a URL ou use cookies "
                "se a playlist for privada."
            )

        title = info.get("title") or "Playlist"
        # Uma playlist nova se soma à biblioteca: faixas que outra playlist já
        # trouxe ganham só o vínculo, e não são baixadas de novo.
        playlist_id = self.store.add_playlist(url, title)
        inserted = self.store.add_items(entries, playlist_id)
        repetidas = len({e["video_id"] for e in entries}) - inserted
        mensagem = f'Playlist "{title}": {len(entries)} faixas, {inserted} novas'
        if repetidas:
            mensagem += f", {repetidas} já estavam na biblioteca e não serão baixadas de novo"
        self._log("info", mensagem + ".")
        self._emit_stats()

    def _flatten_entries(self, info: Dict[str, Any]):
        """Percorre playlists aninhadas e devolve as faixas em ordem."""
        entries = info.get("entries")
        if entries is None:
            if info.get("id"):
                yield self._entry_to_row(info)
            return
        for entry in entries:
            if not entry:
                continue
            if entry.get("_type") in ("playlist", "multi_video") or entry.get("entries"):
                yield from self._flatten_entries(entry)
            else:
                row = self._entry_to_row(entry)
                if row:
                    yield row

    @staticmethod
    def _entry_to_row(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        video_id = entry.get("id")
        if not video_id:
            return None
        url = entry.get("url") or entry.get("webpage_url")
        if not url or not str(url).startswith("http"):
            url = f"https://www.youtube.com/watch?v={video_id}"
        return {"video_id": str(video_id), "url": str(url), "title": entry.get("title") or ""}

    # ------------------------------------------------------------- download

    def _base_for(self, tags: TrackTags, video_id: str = "") -> Path:
        """Destino da faixa na estrutura configurada, sem extensão."""
        relative = build_relative_path(
            tags,
            self.cfg.ascii_only,
            self.cfg.max_component_len,
            self.cfg.folder_structure,
        )
        base = Path(self.cfg.output_dir).expanduser().resolve() / relative
        dono = self.store.filepath_owner(str(base))
        if dono and dono != video_id:
            # Outra faixa já ocupa esse nome: sem desempate ela seria sobrescrita
            # ou esta seria pulada como "já baixada".
            base = base.with_name(f"{base.name} [{video_id}]")
            self._log("warn", f"Nome repetido; esta faixa vira {base.name}.")
        return base

    def _outtmpl_for(self, tags: TrackTags, video_id: str = "") -> str:
        # O outtmpl é uma string de formatação: '%' literal precisa ser escapado.
        return str(self._base_for(tags, video_id)).replace("%", "%%") + ".%(ext)s"

    def _staging_root(self) -> Path:
        """Pasta de espera do áudio cru: disco local, não o pen drive.

        Converter direto no pen drive escreve o arquivo inteiro de novo a cada
        etapa (extrair áudio, tags, capa), e a memória flash USB é lenta nisso.
        Só o resultado final é gravado lá, uma vez.
        """
        if self.cfg.staging_dir.strip():
            base = Path(self.cfg.staging_dir).expanduser()
        else:
            base = Path(tempfile.gettempdir()) / "PenDriveMaker"
        raiz = base / "staging"
        try:
            raiz.mkdir(parents=True, exist_ok=True)
            return raiz
        except OSError:
            # Sem permissão no temporário: volta para dentro da biblioteca.
            alternativa = Path(self.cfg.output_dir).expanduser().resolve() / STATE_DIRNAME / "staging"
            alternativa.mkdir(parents=True, exist_ok=True)
            return alternativa

    def _staging_dir(self, video_id: str) -> Path:
        return self._staging_root() / video_id

    def _limpar_staging_antigo(self, dias: float = 3.0) -> None:
        """Sobras de execuções interrompidas, esquecidas há dias, não ficam para sempre."""
        limite = time.time() - dias * 86400
        try:
            for pasta in self._staging_root().iterdir():
                if pasta.is_dir() and pasta.stat().st_mtime < limite:
                    shutil.rmtree(pasta, ignore_errors=True)
        except OSError:
            pass

    def _preparar_capa(self, result: Dict[str, Any], destino: Path) -> None:
        """Capa de 500x500 em JPEG, no lugar da imagem original do YouTube.

        A miniatura vem em 1280x720 (WebP) e o yt-dlp a converteria em PNG:
        1 a 2 MB por música, lento de embutir e pesado para o pen drive, e muitos
        aparelhos de carro nem exibem capas grandes. Corta o quadrado central
        (a arte do álbum fica no meio) e reduz.
        """
        capas = result.get("thumbnails") or []
        if not capas or not capas[-1].get("filepath") or not self._ffmpeg_path:
            return
        origem = Path(capas[-1]["filepath"])
        if not origem.is_file():
            return
        jpg = destino / "capa.jpg"
        comando = [
            self._ffmpeg_path, "-y", "-loglevel", "error", "-i", str(origem),
            "-vf", "crop='min(iw,ih)':'min(iw,ih)',scale='min(500,iw)':-2",
            "-q:v", "3", "-frames:v", "1", str(jpg),
        ]
        try:
            _out, erro, codigo = Popen.run(comando, text=True, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE)
        except OSError as exc:
            self._log("debug", f"Capa não reduzida: {exc}")
            return
        if codigo == 0 and jpg.is_file() and jpg.stat().st_size > 0:
            capas[-1]["filepath"] = str(jpg)
        else:
            self._log("debug", f"Capa não reduzida (ffmpeg {codigo}): {str(erro).strip()[:200]}")

    def _fetch(self, item: QueueItem, position: int, total: int) -> "_Job":
        """Etapa de rede: metadados e áudio cru. A conversão fica para outra thread."""
        started = time.monotonic()
        with YoutubeDL(self._probe_opts()) as probe:
            info = probe.extract_info(item.url, download=False)
        if not info:
            raise RuntimeError("Metadados vazios.")

        staging = self._staging_dir(item.video_id)
        staging.mkdir(parents=True, exist_ok=True)
        outtmpl = str(staging / "audio").replace("%", "%%") + ".%(ext)s"
        # Reaproveita o dicionário já extraído (mesmo caminho do --load-info-json
        # do yt-dlp), então o download não faz uma segunda extração de página.
        payload = YoutubeDL.sanitize_info(info, remove_private_keys=True)
        with YoutubeDL(self._download_opts(outtmpl)) as ydl:
            try:
                result = ydl.process_ie_result(payload, download=True)
            except (Stopped, CookieLoadError):
                raise
            except Exception as exc:
                if classify_error(str(exc)) != ERROR_TRANSIENT:
                    raise
                # Informações defasadas (URLs de mídia expiram): refaz pela URL.
                self._log("warn", f"Reaproveitamento falhou ({exc}); baixando pela URL.")
                result = ydl.extract_info(item.url, download=True)

        raw = self._final_path(result)
        if not raw or not Path(raw).is_file():
            raise RuntimeError("O download não gerou arquivo.")
        return _Job(item, info, result, raw, staging, position, total, started)

    def _generos_de_reserva(self) -> set:
        """Nomes que valem como "não se sabe o gênero": playlists e o gênero de reserva."""
        return self.store.playlist_titles() | {self.cfg.default_genre.strip().lower()}

    def _genero_do_artista(self, tags: TrackTags, confiavel: bool = True) -> TrackTags:
        """Um artista, um gênero: segue o que já predomina na biblioteca.

        Só vale quando o gênero vem da internet ou dos metadados da faixa, que
        variam de música para música; o nome da playlist e o gênero fixo são
        escolhas do usuário e não se mexem.
        """
        if not self.cfg.genre_per_artist or self.cfg.genre_source not in ("online", "metadata"):
            return tags
        chave = tags.artist.strip().lower()
        with self._artist_lock:
            # Duas conversões do mesmo artista ao mesmo tempo: a primeira a chegar
            # decide, e a outra a segue, em vez de cada uma olhar o banco antes
            # de a outra gravar.
            conhecido = self._artist_genres.get(chave) or self.store.artist_genre(
                tags.artist, self._generos_de_reserva()
            )
            final = conhecido or tags.genre
            self._artistas.add(tags.artist)
            if confiavel or conhecido:
                # O gênero de reserva (nome da playlist) não decide pelo artista:
                # só uma resposta de verdade, da internet ou da faixa, vale.
                self._artist_genres[chave] = final
        if final != tags.genre:
            self._log("debug", f"Gênero de {tags.artist}: {tags.genre} -> {final} "
                               "(o que já predomina na biblioteca)")
            return replace(tags, genre=final)
        return tags

    def _finish(self, job: "_Job") -> tuple[str, TrackTags]:
        """Etapa local: tags, conversão, capa e mover para a pasta certa."""
        item, info = job.item, job.info
        # A consulta online precisa de artista e título já limpos, então roda
        # depois da primeira resolução e só substitui o gênero.
        tags = resolve_tags(
            info,
            self.cfg.default_genre,
            self.cfg.default_album,
            self.cfg.genre_source,
            # cada faixa usa a playlist que a trouxe, não a da URL atual
            self.store.playlist_title_for(item.video_id) or self._playlist_genre,
        )
        confiavel = bool(metadata_genre(info))
        if self._genre_lookup is not None and not self._genre_lookup.disabled:
            encontrado = self._genre_lookup.lookup(tags.artist, tags.title)
            if encontrado:
                tags = replace(tags, genre=encontrado)
                confiavel = True
            else:
                self._log("debug", f"Gênero não encontrado para {tags.artist} - {tags.title}")
        tags = self._genero_do_artista(tags, confiavel)
        raiz = Path(self.cfg.output_dir).expanduser().resolve()
        if item.pinned and item.relpath:
            # Local escolhido à mão (ou vindo de outra biblioteca): vale mais
            # do que a estrutura de pastas configurada.
            self._log("info", f"Destino fixado: {item.relpath}")
        else:
            self._log("info", "Convertendo: " + (item.title or item.video_id))

        entrada = dict(job.result)
        if self.cfg.embed_metadata:
            _TagInjector(self._campos_de_tag(tags, item)).run(entrada)
        if self.cfg.embed_thumbnail:
            inicio_capa = time.monotonic()
            self._preparar_capa(entrada, job.staging)
            tempo_capa = time.monotonic() - inicio_capa
        else:
            tempo_capa = 0.0
        marcas: list = []
        inicio_pos = time.monotonic()
        with YoutubeDL(self._post_opts(marcas)) as ydl:
            saida = ydl.post_process(job.raw_path, entrada)
        tempo_pos = time.monotonic() - inicio_pos
        produzido = Path(saida["filepath"])
        if not produzido.is_file():
            raise RuntimeError("A conversão não gerou arquivo.")

        # Escolher o nome e mover é rápido; o que demora (conversão) ficou de fora
        # da trava. Sem ela, duas faixas com o mesmo nome escolheriam o mesmo destino.
        with self._move_lock:
            if item.pinned and item.relpath:
                base = (raiz / item.relpath).with_suffix("")
            else:
                base = self._base_for(tags, item.video_id)
                self._log("info", f"Destino: {Path(os.path.relpath(base, raiz)).as_posix()}")
            destino = base.with_name(base.name + produzido.suffix)
            if destino.exists() and self.store.filepath_owner(str(base)) != item.video_id:
                # Um arquivo que não é desta faixa (posto à mão, por exemplo) nunca é
                # sobrescrito; o da própria faixa, ao baixar de novo, é substituído.
                destino = destino.with_name(f"{destino.stem} [{item.video_id}]{destino.suffix}")
            destino.parent.mkdir(parents=True, exist_ok=True)
            inicio_mover = time.monotonic()
            shutil.move(str(produzido), str(destino))
            tempo_mover = time.monotonic() - inicio_mover
        shutil.rmtree(job.staging, ignore_errors=True)
        detalhe = self._tempos(marcas)
        self._log("info", (
            f"Conversão: {tempo_pos + tempo_capa:.1f}s"
            + (f" ({detalhe}" + (f" · capa reduzida {tempo_capa:.1f}s" if tempo_capa else "") + ")"
               if detalhe else "")
            + f" · gravar no destino {tempo_mover:.1f}s"
        ))
        return str(destino), tags

    @staticmethod
    def _campos_de_tag(tags: TrackTags, item: QueueItem) -> Dict[str, str]:
        """Tags a gravar no arquivo, iguais às que decidiram a pasta.

        Sem isto o yt-dlp grava como gênero a categoria do YouTube ("Music"),
        e a pasta diz uma coisa enquanto o rádio do carro mostra outra. O
        `comment` leva a URL de origem: é por ela que a biblioteca reconhece o
        arquivo depois de movido.
        """
        origem = f"https://www.youtube.com/watch?v={item.video_id}"
        campos = {
            "meta_title": tags.title,
            "meta_artist": tags.artist,
            "meta_album": tags.album,
            "meta_genre": tags.genre,
            "meta_comment": origem,
            "meta_purl": origem,
        }
        if tags.track_number:
            campos["meta_track"] = str(tags.track_number)
        return {k: str(v) for k, v in campos.items() if v}

    @staticmethod
    def _final_path(result: Optional[Dict[str, Any]]) -> str:
        if not result:
            return ""
        downloads = result.get("requested_downloads") or []
        if downloads:
            entry = downloads[0]
            return entry.get("filepath") or entry.get("_filename") or ""
        return result.get("filepath") or result.get("_filename") or ""

    # ------------------------------------------------------------- bloqueios

    def _handle_block(self, reason: str) -> None:
        with self._lock:
            self._block_events += 1
            eventos = self._block_events
        if eventos > self.cfg.max_block_events:
            raise BlockedTooOften(reason)

        backoff = min(
            self.cfg.block_base_backoff * (2 ** (eventos - 1)),
            self.cfg.block_max_backoff,
        )
        backoff *= 1.0 + random.uniform(0.0, 0.25)
        self._log(
            "error",
            f"Bloqueio detectado ({eventos}/{self.cfg.max_block_events}): {reason}",
        )
        self._log("warn", f"Pausando {self._fmt_duration(backoff)} antes de tentar de novo.")
        with self._lock:
            # Vale para todas as vagas de download, não só para a que foi barrada.
            self._block_until = max(self._block_until, time.monotonic() + backoff)
        self._wait_unblocked()
        self._log("info", "Retomando a fila após o backoff.")

    def _wait_unblocked(self) -> None:
        """Espera o fim do backoff de um bloqueio, qualquer que seja a vaga que o achou."""
        while True:
            resta = self._block_until - time.monotonic()
            if resta <= 0:
                return
            self._sleep(resta, "Aguardando após bloqueio:")

    def _wait_rate_limit(self, ultimo_fim: Optional[float]) -> None:
        """Intervalo entre downloads, contado por vaga: cada uma espera o seu."""
        if ultimo_fim is None:
            return  # primeira faixa desta vaga: sem espera
        delay = self.cfg.min_interval + random.uniform(0.0, self.cfg.jitter)
        resta = delay - (time.monotonic() - ultimo_fim)
        if resta > 0:
            self._sleep(resta, "Aguardando intervalo:")

    def _stagger(self) -> None:
        """Com várias vagas, não deixa os downloads começarem todos no mesmo instante."""
        folga = min(self.cfg.min_interval, 3.0) if self.cfg.max_downloads > 1 else 0.0
        if folga <= 0:
            return
        with self._start_lock:
            resta = self._last_start + folga - time.monotonic()
            if resta > 0:
                self._sleep(resta, "Escalonando downloads:")
            self._last_start = time.monotonic()

    # ------------------------------------------------------------- execução

    def run(self, force_reextract: bool = False) -> None:
        reason = "Concluído"
        try:
            raiz = Path(self.cfg.output_dir).expanduser()
            # Anotar antes do mkdir: criar a pasta de uma unidade ausente "cura"
            # a ausência e esconderia que o pen drive não está conectado.
            raiz_existia = raiz.is_dir()
            raiz.mkdir(parents=True, exist_ok=True)
            self.sync_with_disk(raiz_existia)
            self._limpar_staging_antigo()
            self._verify_environment()
            self._verify_cookies()
            self._extract_playlist(force=force_reextract)
            self._load_playlist_genre()
            self._process_queue()
            stats = self.store.stats()
            if stats.failed:
                reason = (
                    f"Fila concluída com {stats.failed} falha(s). "
                    "Use 'Reenfileirar falhas' para tentar de novo."
                )
            else:
                reason = f"Fila concluída: {stats.done} faixa(s) baixada(s)."
            self._log("info", reason)
        except Stopped:
            reason = "Parado pelo usuário. A fila foi salva; clique em Iniciar para retomar."
            self._log("warn", reason)
        except EnvironmentProblem as exc:
            reason = str(exc)
            for line in reason.splitlines():
                self._log("error", line)
            reason = reason.splitlines()[0]
        except (CookieError, CookieLoadError) as exc:
            reason = str(exc) if isinstance(exc, CookieError) else describe_cookie_failure(
                self.cfg, [str(exc)]
            )
            for line in reason.splitlines():
                self._log("error", line)
            reason = reason.splitlines()[0]
        except BlockedTooOften as exc:
            reason = (
                f"Encerrado por bloqueios repetidos do servidor ({exc}). "
                "A fila está salva no diretório de saída."
            )
            self._log("error", reason)
            self._log(
                "info",
                "Para retomar: aguarde algumas horas, informe cookies do navegador, "
                "aumente o intervalo entre downloads e clique em Iniciar novamente. "
                "Nada já baixado será repetido.",
            )
        except Exception as exc:  # falha inesperada: registra e encerra limpo
            reason = f"Erro inesperado: {exc}"
            self._log("error", reason)
            self._log("debug", traceback.format_exc())
        finally:
            self._emit_stats()
            self._status("Ocioso")
            self.cb.finished(reason)

    def _load_playlist_genre(self) -> None:
        """O nome da playlist vira a pasta de gênero; vem do banco, então vale
        igual numa execução retomada, sem reextrair nada."""
        row = self.store.get_playlist()
        self._playlist_genre = (row["title"] if row else "") or ""
        if self.cfg.genre_source == "online":
            self._genre_lookup = GenreLookup(
                self.store, group=self.cfg.group_subgenres,
                grupos=self.cfg.genre_group_map(),
                manter_desconhecidos=self.cfg.genre_keep_unmapped, log=self._log
            )
            reserva = self._playlist_genre or self.cfg.default_genre
            self._log("info", f'Gênero consultado na internet; reserva: "{reserva}".')
            return
        if self.cfg.genre_source == "fixed":
            self._log("info", f"Gênero fixo: {self.cfg.default_genre}")
        elif self._playlist_genre:
            origem = "playlist" if self.cfg.genre_source == "playlist" else "reserva"
            self._log("info", f'Gênero pelo nome da playlist ({origem}): "{self._playlist_genre}"')
        else:
            self._log("warn", f"Playlist sem nome; gênero cai em {self.cfg.default_genre}.")

    def _process_queue(self) -> None:
        """Baixa em várias vagas e converte em outras threads, tudo ao mesmo tempo."""
        pool = _PostWorker(self, threads=self.cfg.max_conversions)
        vagas = [
            threading.Thread(target=self._download_loop, args=(pool,), name=f"download-{i + 1}",
                             daemon=True)
            for i in range(self.cfg.max_downloads)
        ]
        self._log("info", f"{self.cfg.max_downloads} download(s) e "
                          f"{self.cfg.max_conversions} conversão(ões) simultâneos.")
        try:
            for vaga in vagas:
                vaga.start()
            for vaga in vagas:
                vaga.join()
        finally:
            # Mesmo parando ou sendo bloqueado: o que já foi baixado é convertido.
            pool.close()
            self._unificar_generos()
        if self._fatal is not None:
            raise self._fatal

    def _unificar_generos(self) -> None:
        """Fecha a corrida entre conversões: uma faixa sem gênero próprio pode ter
        sido salva na pasta de reserva antes de outra do mesmo artista descobrir o
        gênero dele. Aqui o artista inteiro passa para o gênero que predomina."""
        if not self._artistas or not self.cfg.genre_per_artist \
                or self.cfg.genre_source not in ("online", "metadata"):
            return
        try:
            relatorio = reorganize(
                self.store, Path(self.cfg.output_dir).expanduser(), self.cfg.folder_structure,
                self.cfg.ascii_only, self.cfg.max_component_len, self.cfg.default_genre,
                self.cfg.default_album, por_artista=True, artistas=set(self._artistas),
                so_genero=True,
            )
        except Exception as exc:  # a biblioteca já está salva: isto é só o acabamento
            self._log("warn", f"Não consegui unificar o gênero por artista: {exc}")
            return
        if relatorio.moved:
            self._log("info", f"{relatorio.moved} faixa(s) passaram para o gênero que já "
                              "predomina no artista.")

    def _claim_next(self) -> Optional[QueueItem]:
        """Pega a próxima faixa livre, sem que duas vagas peguem a mesma."""
        with self._lock:
            item = self.store.next_pending(exclude=list(self._active))
            if item is not None:
                self._active.add(item.video_id)
            return item

    def _download_loop(self, pool: _PostWorker) -> None:
        """Uma vaga de download: pega faixas até a fila acabar ou algo fatal acontecer."""
        ultimo_fim: Optional[float] = None
        try:
            while not self._halt.is_set():
                self._check_control()
                item = self._claim_next()
                if item is None:
                    with self._mudou:
                        if not self._active:
                            return
                        # Outra vaga ou uma conversão pode devolver uma faixa à fila.
                        self._status("Aguardando as conversões terminarem...")
                        self._mudou.wait(timeout=0.5)
                    continue

                entregue = False
                try:
                    self._wait_unblocked()
                    self._wait_rate_limit(ultimo_fim)
                    self._stagger()
                    self._check_control()

                    stats = self.store.stats()
                    with self._lock:
                        posicao = stats.done + stats.failed + len(self._active)
                    rotulo = item.title or item.video_id
                    self._status(f"[{posicao}/{stats.total}] {rotulo}")
                    self._log("info", f"[{posicao}/{stats.total}] Baixando: {rotulo}")
                    try:
                        job = self._fetch(item, posicao, stats.total)
                    except (Stopped, CookieError, CookieLoadError, EnvironmentProblem):
                        raise
                    except Exception as exc:  # erro de rede, yt-dlp ou disco
                        ultimo_fim = time.monotonic()
                        self._on_item_error(item, exc)
                    else:
                        ultimo_fim = time.monotonic()
                        with self._lock:
                            self._consecutive_failures = 0
                            self._block_events = 0  # sucesso limpa o histórico de bloqueios
                        self._log("info", f"Baixado: {rotulo} (convertendo em segundo plano)")
                        pool.submit(job)
                        entregue = True  # a conversão libera a faixa quando terminar
                except BaseException as exc:
                    # Antes de liberar a faixa: senão outra vaga a pega de volta e
                    # tenta de novo no instante em que o motor deveria parar.
                    self._set_fatal(exc)
                    raise
                finally:
                    with self._lock:
                        self._percent.pop(threading.get_ident(), None)
                    if not entregue:
                        self._release(item.video_id)
                    self._emit_progress()
                    self._emit_stats()
        except BaseException as exc:  # Stopped, bloqueio demais, ambiente: encerra todas as vagas
            self._set_fatal(exc)

    def _set_fatal(self, exc: BaseException) -> None:
        with self._lock:
            if self._fatal is None:
                self._fatal = exc
        self._halt.set()

    def _complete(self, job: _Job) -> None:
        """Roda na thread de conversão: termina a faixa e a marca como baixada."""
        item = job.item
        path, tags = self._finish(job)
        # Guarda os níveis que existem de fato: sem a pasta de álbum, o banco e
        # a árvore da interface não devem inventá-la. As tags reais também ficam:
        # reorganizar para outra estrutura precisa saber artista e álbum.
        self.store.mark_done(
            item.video_id, path, tags.genre, tags.artist, tags.album,
            tags.title, tags.track_number,
        )
        self._log("ok", f"OK em {time.monotonic() - job.started:.0f}s: {path}")
        try:
            self.cb.item_done(path, tags)
        except Exception as exc:  # a faixa já está salva: erro de interface não a desfaz
            self._log("debug", f"Callback item_done falhou: {exc}")

    def _on_post_error(self, item: QueueItem, exc: Exception) -> None:
        """Falha na conversão: não é bloqueio, só conta como tentativa da faixa."""
        message = str(exc).strip() or exc.__class__.__name__
        attempts = self.store.register_failure(
            item.video_id, message, self.cfg.max_retries, final=False
        )
        nivel = "error" if attempts >= self.cfg.max_retries else "warn"
        self._log(nivel, f"Falha ao converter '{item.title or item.video_id}' "
                         f"({attempts}/{self.cfg.max_retries}): {message}")

    def _on_item_error(self, item: QueueItem, exc: Exception) -> None:
        if self._stop.is_set():
            # A parada foi sinalizada de dentro do hook de progresso e o yt-dlp
            # pode tê-la embrulhado num DownloadError: não conta como falha.
            raise Stopped()
        message = str(exc).strip() or exc.__class__.__name__
        kind = classify_error(message)

        if kind == ERROR_BLOCK:
            # Bloqueio não é culpa da faixa: não gasta retentativa dela.
            self._log("error", f"Falha por bloqueio em '{item.title or item.video_id}': {message}")
            self._handle_block(message)
            return

        if kind == ERROR_JS_RUNTIME:
            runtime = find_js_runtime()
            if runtime is None and not self.allow_missing_js_runtime:
                # Sem runtime nada vai baixar: parar agora poupa a fila inteira.
                raise EnvironmentProblem(describe_missing_js_runtime())
            if runtime is None:
                self._log(
                    "warn",
                    "Falha no desafio JavaScript e nenhum runtime encontrado. "
                    "Instale o Deno para que os downloads funcionem.",
                )
            else:
                # Com runtime presente, provavelmente é o yt-dlp desatualizado ou
                # uma mudança recente do YouTube: ainda vale retentar, com a dica.
                self._log(
                    "warn",
                    f"Falha ao resolver o desafio JavaScript usando {runtime}. "
                    "Se repetir em todas as faixas, atualize o yt-dlp "
                    "(pip install -U yt-dlp) ou baixe a versão mais nova do programa.",
                )

        final = kind == ERROR_PERMANENT
        attempts = self.store.register_failure(
            item.video_id, message, self.cfg.max_retries, final=final
        )
        if final:
            self._log("warn", f"Faixa indisponível, ignorada: {item.title} — {message}")
            with self._lock:
                self._consecutive_failures = 0
            return

        if attempts >= self.cfg.max_retries:
            self._log(
                "error",
                f"Falhou {attempts}x, marcada como falha: {item.title} — {message}",
            )
        else:
            self._log(
                "warn",
                f"Falha {attempts}/{self.cfg.max_retries} em '{item.title}': {message}",
            )

        with self._lock:
            self._consecutive_failures += 1
            bloqueio = self._consecutive_failures >= self.cfg.max_consecutive_failures
            if bloqueio:
                self._consecutive_failures = 0
        if bloqueio:
            self._handle_block(
                f"{self.cfg.max_consecutive_failures} falhas consecutivas "
                "(provável bloqueio do servidor)"
            )
