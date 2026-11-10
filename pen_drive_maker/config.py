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


def _as_bool(valor: Any, padrao: bool = False) -> bool:
    """O QSettings devolve bool como texto ("true"/"false") no registro e no .ini.

    "false" é verdadeiro em Python: sem esta conversão a opção desligada vale
    como ligada, e a tela de opções nem abre (setChecked não aceita texto).
    """
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return padrao
    if isinstance(valor, (int, float)):
        return bool(valor)
    return str(valor).strip().lower() in ("true", "1", "yes", "sim", "on")


@dataclass
class AppConfig:
    """Parâmetros de execução. Todos ajustáveis pela interface."""

    playlist_url: str = ""
    output_dir: str = str(Path.home() / "PenDrive")

    # Ritmo entre downloads (segundos). O jitter é somado aleatoriamente.
    min_interval: float = 25.0
    jitter: float = 15.0

    # Quantas faixas baixam ao mesmo tempo e quantas convertem ao mesmo tempo.
    # O intervalo vale por "vaga" de download: mais vagas = mais requisições por
    # minuto ao YouTube, e com isso mais risco de bloqueio.
    max_downloads: int = 2
    max_conversions: int = 2

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
    # Regras do agrupamento, uma linha por grupo ("Pasta: pedaço, pedaço").
    # Vazio = as regras padrão do programa.
    genre_groups: str = ""
    # Gênero que nenhum grupo reconhece: False = usa o gênero de reserva (nome da
    # playlist), True = cria uma pasta com o nome que a internet devolveu.
    genre_keep_unmapped: bool = False
    # Um artista, um gênero: as faixas dele seguem o gênero que já predomina.
    genre_per_artist: bool = True
    default_genre: str = "Sem Genero"
    default_album: str = "Singles"
    # Níveis de pasta — veja organizer.FOLDER_STRUCTURES. O que sai da pasta
    # entra no nome do arquivo, e o álbum segue na tag em qualquer caso.
    folder_structure: str = "genre_artist_album"
    ascii_only: bool = True          # nomes ASCII puros, mais seguros em FAT32/head units
    max_component_len: int = 60

    # Retentativas e detecção de bloqueio
    max_retries: int = 3
    max_consecutive_failures: int = 5
    block_base_backoff: float = 120.0
    block_max_backoff: float = 3600.0
    max_block_events: int = 5

    def genre_group_map(self):
        """Regras de agrupamento como dicionário, ou None para o padrão."""
        from .genre import parse_groups

        return parse_groups(self.genre_groups) or None

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
        for nome in ("allow_remote_components", "embed_thumbnail", "embed_metadata",
                     "group_subgenres", "ascii_only", "genre_keep_unmapped",
                     "genre_per_artist"):
            setattr(self, nome, _as_bool(getattr(self, nome)))
        self.min_interval = max(0.0, float(self.min_interval))
        self.jitter = max(0.0, float(self.jitter))
        self.max_retries = max(1, int(self.max_retries))
        self.max_consecutive_failures = max(1, int(self.max_consecutive_failures))
        self.block_base_backoff = max(5.0, float(self.block_base_backoff))
        self.block_max_backoff = max(self.block_base_backoff, float(self.block_max_backoff))
        self.max_block_events = max(1, int(self.max_block_events))
        self.max_component_len = max(16, int(self.max_component_len))
        self.max_downloads = min(6, max(1, int(self.max_downloads)))
        self.max_conversions = min(8, max(1, int(self.max_conversions)))
        self.genre_groups = str(self.genre_groups or "").strip()
        if self.genre_groups and not self.genre_group_map():
            self.genre_groups = ""  # nada aproveitável: volta ao padrão
        if self.audio_format not in AUDIO_FORMATS:
            self.audio_format = "mp3"
        if self.cookies_mode not in ("none", "file", "browser"):
            self.cookies_mode = "none"
        if self.genre_source not in ("online", "playlist", "metadata", "fixed"):
            self.genre_source = "online"
        if self.folder_structure not in (
            "genre_artist_album", "genre_artist", "genre", "artist", "album"
        ):
            self.folder_structure = "genre_artist_album"

    def validate(self, library_has_work: bool = False) -> list[str]:
        """Retorna a lista de problemas que impedem o início do download.

        Sem URL ainda há o que fazer quando a biblioteca tem faixas pendentes:
        depois de importar uma biblioteca ou copiá-la para outro pen drive,
        "Iniciar" só precisa completar o que falta.
        """
        problems: list[str] = []
        if not self.playlist_url.strip():
            if not library_has_work:
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
