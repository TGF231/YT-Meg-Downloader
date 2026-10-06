"""Configuração da aplicação."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Dict

# Formatos oferecidos na interface. A chave é o valor salvo na configuração.
AUDIO_FORMATS = {
    "mp3": "MP3 (máxima compatibilidade automotiva)",
    "m4a": "M4A / AAC (melhor qualidade, sem reconversão)",
    "opus": "Opus (menor tamanho, pouco suportado em carros)",
    "best": "Melhor original disponível (sem conversão)",
}

BROWSERS = ["chrome", "chromium", "firefox", "edge", "brave", "opera", "vivaldi", "safari"]

STATE_DIRNAME = ".pen-drive-maker"
DB_FILENAME = "queue.db"


@dataclass
class AppConfig:
    """Parâmetros de execução. Todos ajustáveis pela interface."""

    playlist_url: str = ""
    output_dir: str = str(Path.home() / "PenDrive")

    # Ritmo entre downloads (segundos). O jitter é somado aleatoriamente.
    min_interval: float = 25.0
    jitter: float = 15.0

    audio_format: str = "mp3"
    # Vazio = busca automática. Aceita o executável ou a pasta que o contém.
    ffmpeg_path: str = ""
    # Deixa o yt-dlp baixar os scripts do solucionador quando o pacote local
    # não servir. Desligado por padrão: baixa código de terceiros na hora.
    allow_remote_components: bool = False
    embed_thumbnail: bool = True
    embed_metadata: bool = True

    # Cookies: "none" | "file" | "browser"
    cookies_mode: str = "none"
    cookies_file: str = ""
    cookies_browser: str = "chrome"

    # Organização em pastas
    # "online" | "playlist" | "metadata" | "fixed" — veja organizer.GENRE_SOURCES
    genre_source: str = "online"
    # Agrupa subgêneros (Dance/House/Electronic -> Eletronica) para a playlist
    # não se espalhar por muitas pastas.
    group_subgenres: bool = True
    default_genre: str = "Sem Genero"
    default_album: str = "Singles"
    ascii_only: bool = True          # nomes ASCII puros, mais seguros em FAT32/head units
    max_component_len: int = 60

    # Retentativas e detecção de bloqueio
    max_retries: int = 3
    max_consecutive_failures: int = 5
    block_base_backoff: float = 120.0
    block_max_backoff: float = 3600.0
    max_block_events: int = 5

    def state_dir(self) -> Path:
        return Path(self.output_dir).expanduser() / STATE_DIRNAME

    def db_path(self) -> Path:
        return self.state_dir() / DB_FILENAME

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        known = {f.name: f.type for f in fields(cls)}
        kwargs: Dict[str, Any] = {}
        for key, value in (data or {}).items():
            if key not in known:
                continue
            kwargs[key] = value
        cfg = cls(**kwargs)
        cfg.normalize()
        return cfg

    def normalize(self) -> None:
        self.min_interval = max(0.0, float(self.min_interval))
        self.jitter = max(0.0, float(self.jitter))
        self.max_retries = max(1, int(self.max_retries))
        self.max_consecutive_failures = max(1, int(self.max_consecutive_failures))
        self.block_base_backoff = max(5.0, float(self.block_base_backoff))
        self.block_max_backoff = max(self.block_base_backoff, float(self.block_max_backoff))
        self.max_block_events = max(1, int(self.max_block_events))
        self.max_component_len = max(16, int(self.max_component_len))
        if self.audio_format not in AUDIO_FORMATS:
            self.audio_format = "mp3"
        if self.cookies_mode not in ("none", "file", "browser"):
            self.cookies_mode = "none"
        if self.genre_source not in ("online", "playlist", "metadata", "fixed"):
            self.genre_source = "online"

    def validate(self) -> list[str]:
        """Retorna a lista de problemas que impedem o início do download."""
        problems: list[str] = []
        if not self.playlist_url.strip():
            problems.append("Informe a URL da playlist.")
        elif not self.playlist_url.strip().lower().startswith(("http://", "https://")):
            problems.append("A URL da playlist deve começar com http:// ou https://.")
        if not self.output_dir.strip():
            problems.append("Escolha o diretório de saída.")
        if self.cookies_mode == "file":
            if not self.cookies_file.strip():
                problems.append("Selecione o arquivo de cookies ou mude o modo para 'Nenhum'.")
            elif not Path(self.cookies_file).expanduser().is_file():
                problems.append(f"Arquivo de cookies não encontrado: {self.cookies_file}")
        return problems
