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

import random
import re
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from yt_dlp import YoutubeDL
from yt_dlp.cookies import CookieLoadError, load_cookies
from yt_dlp.utils import DownloadError, ExtractorError

from .config import AppConfig
from .organizer import TrackTags, build_relative_path, resolve_tags, sanitize_component
from .tools import (  # reexportados: a interface e os testes importam daqui
    describe_missing_ffmpeg,
    describe_missing_js_runtime,
    find_ffmpeg,
    find_js_runtime,
)
from .store import QueueItem, QueueStats, QueueStore

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

        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()  # começa "não pausado"

        self._block_events = 0
        self._consecutive_failures = 0
        self._last_download_ts: Optional[float] = None
        self._last_progress_ts = 0.0
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
        opts: Dict[str, Any] = {}
        if self.cfg.cookies_mode == "file" and self.cfg.cookies_file:
            opts["cookiefile"] = str(Path(self.cfg.cookies_file).expanduser())
        elif self.cfg.cookies_mode == "browser" and self.cfg.cookies_browser:
            # Formato esperado: (navegador, perfil, keyring, container)
            opts["cookiesfrombrowser"] = (self.cfg.cookies_browser, None, None, None)
        return opts

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
        opts = self._base_opts()
        opts.update(
            {
                "format": "bestaudio/best",
                "outtmpl": {"default": outtmpl},
                "postprocessors": self._postprocessors(),
                "writethumbnail": self.cfg.embed_thumbnail,
                "prefer_ffmpeg": True,
                "overwrites": False,
                "continuedl": True,
                "progress_hooks": [self._progress_hook],
                "postprocessor_hooks": [self._postprocessor_hook],
            }
        )
        return opts

    def _progress_hook(self, event: Dict[str, Any]) -> None:
        if self._stop.is_set():
            # Interrompe o download em andamento; o yt-dlp propaga a exceção.
            raise Stopped()
        if event.get("status") != "downloading":
            return
        now = time.monotonic()
        if now - self._last_progress_ts < 0.3:
            return
        self._last_progress_ts = now
        total = event.get("total_bytes") or event.get("total_bytes_estimate") or 0
        done = event.get("downloaded_bytes") or 0
        percent = (done / total * 100.0) if total else 0.0
        speed = event.get("speed")
        speed_txt = f"{speed / 1024:.0f} KiB/s" if speed else "--"
        self.cb.file_progress(percent, f"{percent:5.1f}% a {speed_txt}")

    def _postprocessor_hook(self, event: Dict[str, Any]) -> None:
        if event.get("status") == "started":
            self.cb.file_progress(100.0, f"processando ({event.get('postprocessor')})")

    # ------------------------------------------------------------- extração

    def _verify_environment(self) -> None:
        runtime = find_js_runtime()
        if runtime is None:
            if not self.allow_missing_js_runtime:
                raise EnvironmentProblem(describe_missing_js_runtime())
            self._log("warn", "Nenhum runtime JavaScript encontrado; seguindo assim mesmo.")
        else:
            self._log("info", f"Runtime JavaScript: {runtime}")
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
        existing = self.store.get_playlist()
        if (
            not force
            and existing is not None
            and existing["url"] == url
            and self.store.count() > 0
        ):
            self._log(
                "info",
                f"Playlist já extraída em {existing['extracted_at']} "
                f"({self.store.count()} faixas). Retomando a fila existente.",
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
        if existing is not None and existing["url"] != url:
            self._log("warn", "URL diferente da playlist anterior: a fila será recriada.")
            self.store.clear_all()

        self.store.set_playlist(url, title)
        inserted = self.store.add_items(entries)
        self._log(
            "info",
            f'Playlist "{title}": {len(entries)} faixas ({inserted} novas na fila).',
        )
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

    def _outtmpl_for(self, tags: TrackTags) -> str:
        relative = build_relative_path(tags, self.cfg.ascii_only, self.cfg.max_component_len)
        base = Path(self.cfg.output_dir).expanduser().resolve() / relative
        # O outtmpl é uma string de formatação: '%' literal precisa ser escapado.
        return str(base).replace("%", "%%") + ".%(ext)s"

    def _download_one(self, item: QueueItem) -> tuple[str, TrackTags]:
        """Resolve metadados e baixa uma faixa. Uma extração de rede por faixa."""
        with YoutubeDL(self._probe_opts()) as probe:
            info = probe.extract_info(item.url, download=False)
        if not info:
            raise RuntimeError("Metadados vazios.")

        tags = resolve_tags(info, self.cfg.default_genre, self.cfg.default_album)
        outtmpl = self._outtmpl_for(tags)
        self._log(
            "info",
            f"Destino: {sanitize_component(tags.genre, self.cfg.ascii_only)}/"
            f"{sanitize_component(tags.artist, self.cfg.ascii_only)}/"
            f"{sanitize_component(tags.album, self.cfg.ascii_only)}/",
        )

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

        path = self._final_path(result)
        return path, tags

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
        self._block_events += 1
        if self._block_events > self.cfg.max_block_events:
            raise BlockedTooOften(reason)

        backoff = min(
            self.cfg.block_base_backoff * (2 ** (self._block_events - 1)),
            self.cfg.block_max_backoff,
        )
        backoff *= 1.0 + random.uniform(0.0, 0.25)
        self._log(
            "error",
            f"Bloqueio detectado ({self._block_events}/{self.cfg.max_block_events}): {reason}",
        )
        self._log("warn", f"Pausando {self._fmt_duration(backoff)} antes de tentar de novo.")
        self._sleep(backoff, "Aguardando após bloqueio:")
        self._log("info", "Retomando a fila após o backoff.")

    def _wait_rate_limit(self) -> None:
        if self._last_download_ts is None:
            return  # primeira faixa da sessão: sem espera
        delay = self.cfg.min_interval + random.uniform(0.0, self.cfg.jitter)
        elapsed = time.monotonic() - self._last_download_ts
        remaining = delay - elapsed
        if remaining > 0:
            self._sleep(remaining, "Aguardando intervalo:")

    # ------------------------------------------------------------- execução

    def run(self, force_reextract: bool = False) -> None:
        reason = "Concluído"
        try:
            Path(self.cfg.output_dir).expanduser().mkdir(parents=True, exist_ok=True)
            self._verify_environment()
            self._verify_cookies()
            self._extract_playlist(force=force_reextract)
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

    def _process_queue(self) -> None:
        while True:
            self._check_control()
            item = self.store.next_pending()
            if item is None:
                return

            stats = self.store.stats()
            position = stats.done + stats.failed + 1
            label = item.title or item.video_id
            self._status(f"[{position}/{stats.total}] {label}")

            self._wait_rate_limit()
            self._check_control()

            self._log("info", f"[{position}/{stats.total}] Baixando: {label}")
            started = time.monotonic()
            try:
                path, tags = self._download_one(item)
            except (Stopped, CookieError, CookieLoadError, EnvironmentProblem):
                raise
            except (DownloadError, ExtractorError, OSError, RuntimeError) as exc:
                self._last_download_ts = time.monotonic()
                self._on_item_error(item, exc)
            except Exception as exc:  # erro fora do previsto no yt-dlp
                self._last_download_ts = time.monotonic()
                self._on_item_error(item, exc)
            else:
                self._last_download_ts = time.monotonic()
                self._consecutive_failures = 0
                self._block_events = 0  # sucesso limpa o histórico de bloqueios
                # Guarda os nomes já saneados: é o que existe de fato no disco.
                genre, artist, album = (
                    sanitize_component(part, self.cfg.ascii_only, self.cfg.max_component_len)
                    for part in tags.folder_parts()
                )
                self.store.mark_done(item.video_id, path, genre, artist, album, tags.title)
                self._log(
                    "ok",
                    f"OK em {time.monotonic() - started:.0f}s: {path or '(caminho não informado)'}",
                )
                self.cb.item_done(path, tags)
            finally:
                self._emit_stats()

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

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.cfg.max_consecutive_failures:
            self._consecutive_failures = 0
            self._handle_block(
                f"{self.cfg.max_consecutive_failures} falhas consecutivas "
                "(provável bloqueio do servidor)"
            )
