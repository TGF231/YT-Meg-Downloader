"""Consulta do gênero musical numa fonte externa, com cache em disco.

O YouTube Music não expõe gênero, então a alternativa é perguntar a quem
tem: a busca pública do iTunes devolve `primaryGenreName` sem exigir chave.

Duas decisões que valem explicação:

**Agrupamento.** A resposta crua é granular demais para pastas de pen drive:
numa playlist de EDM, "Faded" volta como *Dance*, "Wake Me Up" como *House* e
"Strobe" como *Electronic*. Três pastas para o que o ouvinte trata como uma.
Por padrão os subgêneros são agrupados; desligue para usar a resposta crua.

**A consulta nunca derruba o download.** Erro de rede, resposta vazia ou
resultado de outro artista devolvem None, e quem chamou cai para o nome da
playlist. Depois de algumas falhas seguidas a consulta se desliga sozinha
pelo resto da execução.
"""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional

from .organizer import strip_accents

ITUNES_SEARCH = "https://itunes.apple.com/search"
USER_AGENT = "pen-drive-maker (+https://github.com/TGF231/Pen-Drive-Maker)"

# Lixo que vem no título de vídeo e atrapalha a busca.
_TITLE_NOISE = re.compile(
    r"""\s*[\(\[][^)\]]*(
        official|video|audio|lyric|lyrics|clipe|oficial|hd|4k|visualizer|
        music\s*video|mv|live|ao\s*vivo|legendado|letra|extended|radio\s*edit
    )[^)\]]*[\)\]]""",
    re.IGNORECASE | re.VERBOSE,
)
_FEAT = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|com|with)\s+.*$", re.IGNORECASE)
# O mesmo enfeite aparece sem parênteses, depois de hífen ou no fim do título.
_TITLE_NOISE_TAIL = re.compile(
    r"""(\s*[-–—]\s*|\s+)(
        official\s*(music\s*)?video|official\s*audio|lyric\s*video|lyrics|
        video\s*oficial|clipe\s*oficial|audio\s*oficial|visualizer|
        hd|hq|4k|full\s*hd|remaster(ed)?(\s*\d{4})?
    )\s*$""",
    re.IGNORECASE | re.VERBOSE,
)

# Agrupamento de subgêneros. A chave é o nome da pasta; os valores são
# pedaços que podem aparecer na resposta da API.
GRUPOS: Dict[str, tuple] = {
    "Eletronica": (
        "dance", "house", "electronic", "electronica", "techno", "trance",
        "dubstep", "drum & bass", "drum and bass", "edm", "club", "breakbeat",
        "big room", "electro", "ambient", "downtempo", "garage", "idm",
    ),
    "Rock": (
        "rock", "alternative", "metal", "punk", "grunge", "indie", "emo",
        "hardcore", "post-punk", "shoegaze",
    ),
    "Pop": ("pop", "k-pop", "j-pop", "synth", "new wave"),
    "Hip Hop": ("hip hop", "hip-hop", "rap", "trap"),
    "R&B e Soul": ("r&b", "soul", "funk", "motown", "disco"),
    "MPB": (
        "mpb", "bossa", "samba", "pagode", "forró", "forro", "axé", "axe",
        "brazilian", "brasileira", "música popular",
    ),
    "Sertanejo": ("sertanejo", "música caipira"),
    "Jazz e Blues": ("jazz", "blues", "bebop", "swing"),
    "Classica": ("classical", "clássica", "opera", "ópera", "soundtrack", "score"),
    "Reggae": ("reggae", "ska", "dancehall"),
    "Gospel": ("gospel", "christian", "worship", "religios"),
    "Country": ("country", "bluegrass", "folk"),
    "Latina": ("latin", "latino", "reggaeton", "salsa", "cumbia", "tango"),
}


def clean_title(title: str) -> str:
    """Tira o enfeite de título de vídeo que atrapalha a busca."""
    title = _TITLE_NOISE.sub("", title or "")
    title = _FEAT.sub("", title)
    anterior = None
    while anterior != title:  # "Animals - Official Video HD" tem dois enfeites
        anterior = title
        title = _TITLE_NOISE_TAIL.sub("", title)
    return re.sub(r"\s+", " ", title).strip(" -–—")


def group_genre(raw: str) -> str:
    """Reduz o gênero da API a um dos grupos; devolve o original se não casar."""
    alvo = strip_accents(raw or "").lower()
    for pasta, pedacos in GRUPOS.items():
        for pedaco in pedacos:
            if strip_accents(pedaco).lower() in alvo:
                return pasta
    return raw


def _tokens(texto: str) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", strip_accents(texto or "").lower()) if len(t) > 1}


def artists_match(consultado: str, devolvido: str) -> bool:
    """Evita aceitar a faixa errada quando a busca erra o alvo."""
    a, b = _tokens(consultado), _tokens(devolvido)
    if not a or not b:
        return False
    return bool(a & b)


class GenreLookup:
    """Consulta com cache. Seguro para usar a partir da thread de download."""

    def __init__(
        self,
        cache,
        group: bool = True,
        timeout: float = 10.0,
        min_interval: float = 1.0,
        max_failures: int = 5,
        log: Optional[Callable[[str, str], None]] = None,
    ):
        self._cache = cache
        self._group = group
        self._timeout = timeout
        self._min_interval = min_interval
        self._max_failures = max_failures
        self._log = log or (lambda level, message: None)
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._failures = 0
        self.disabled = False

    def lookup(self, artist: str, title: str) -> Optional[str]:
        artist, title = (artist or "").strip(), clean_title(title)
        if not title:
            return None
        chave = f"{strip_accents(artist).lower()}|{strip_accents(title).lower()}"

        guardado = self._cache.get_genre(chave)
        if guardado is not None:
            if not guardado:
                return None  # string vazia = já consultamos e não há
            # O cache guarda a resposta crua: assim ligar ou desligar o
            # agrupamento vale na hora, sem precisar consultar tudo de novo.
            return group_genre(guardado) if self._group else guardado

        if self.disabled:
            return None

        estado, bruto = self._fetch(artist, title)
        if estado == "erro":
            # Falha de rede não vira resposta: gravar "" aqui deixaria a faixa
            # sem gênero para sempre, mesmo depois de a internet voltar.
            return None
        # Ausência confirmada é gravada, para não repetir a consulta na retomada.
        self._cache.set_genre(chave, bruto or "")
        if not bruto:
            return None
        return group_genre(bruto) if self._group else bruto

    def _fetch(self, artist: str, title: str) -> tuple:
        """Devolve ("ok"|"vazio"|"erro", gênero)."""
        with self._lock:
            espera = self._min_interval - (time.monotonic() - self._last_call)
            if espera > 0:
                time.sleep(espera)
            self._last_call = time.monotonic()

        termo = f"{artist} {title}".strip()
        url = ITUNES_SEARCH + "?" + urllib.parse.urlencode(
            {"term": termo, "entity": "song", "limit": 3, "media": "music"}
        )
        pedido = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        dados = None
        for tentativa in range(2):  # o serviço devolve 403 esporádico
            try:
                with urllib.request.urlopen(pedido, timeout=self._timeout) as resposta:
                    dados = json.load(resposta)
                break
            except (urllib.error.URLError, OSError, ValueError) as erro:
                if tentativa == 0:
                    time.sleep(1.5)
                    continue
                self._register_failure(f"{type(erro).__name__}: {erro}")
                return ("erro", None)

        self._failures = 0
        for resultado in dados.get("results") or []:
            genero = (resultado.get("primaryGenreName") or "").strip()
            if not genero:
                continue
            if artist and not artists_match(artist, resultado.get("artistName") or ""):
                continue
            return ("ok", genero)
        return ("vazio", None)

    def _register_failure(self, motivo: str) -> None:
        self._failures += 1
        if self._failures >= self._max_failures:
            self.disabled = True
            self._log(
                "warn",
                "Consulta de gênero desligada após falhas seguidas; o nome da "
                f"playlist será usado. Último erro: {motivo}",
            )
        else:
            self._log("debug", f"Consulta de gênero falhou: {motivo}")
