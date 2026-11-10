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

# Nome do grupo especial cujos pedaços NÃO são gêneros (veja abaixo).
IGNORAR = "(ignorar)"

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
    "Pop": ("pop", "k-pop", "j-pop", "synth", "new wave", "electropop", "synthpop", "europop"),
    "Hip Hop": ("hip hop", "hip-hop", "hiphop", "rap", "trap"),
    "R&B e Soul": ("r&b", "soul", "funk", "motown", "disco"),
    "MPB": (
        "mpb", "bossa", "samba", "pagode", "forró", "forro", "axé", "axe",
        "brazilian", "brasileira", "música popular",
    ),
    "Sertanejo": ("sertanejo", "música caipira"),
    "Jazz e Blues": ("jazz", "blues", "bebop", "swing"),
    "Classica": ("classical", "clássica", "opera", "ópera"),
    "Reggae": ("reggae", "ska", "dancehall"),
    "Gospel": ("gospel", "christian", "worship", "religios"),
    "Country": ("country", "bluegrass", "folk"),
    "Latina": ("latin", "latino", "reggaeton", "salsa", "cumbia", "tango"),
    # Rótulos que dizem de onde a música vem (um filme, um país, uma ocasião) e
    # não como ela soa: não viram pasta. A faixa fica com o gênero de reserva.
    IGNORAR: (
        "bollywood", "tollywood", "kollywood", "indian", "soundtrack", "score",
        "original score", "musicals", "anime", "karaoke", "holiday", "christmas",
        "natal", "spoken word", "audiobooks", "comedy", "children", "infantil",
    ),
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


def groups_to_text(grupos: Dict[str, tuple]) -> str:
    """Uma linha por grupo: `Pasta: pedaço, pedaço, ...` (o formato da tela)."""
    return "\n".join(f"{pasta}: {', '.join(pedacos)}" for pasta, pedacos in grupos.items())


def parse_groups(texto: str) -> Dict[str, tuple]:
    """Lê o formato de `groups_to_text`. Linhas vazias e `#` são ignoradas.

    Vazio (ou sem nenhum grupo válido) devolve {}: quem chama usa o padrão.
    Um grupo repetido soma os pedaços; a ordem das linhas é a ordem de teste.
    """
    grupos: Dict[str, list] = {}
    for linha in (texto or "").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or ":" not in linha:
            continue
        pasta, _, resto = linha.partition(":")
        pasta = pasta.strip()
        pedacos = [p.strip() for p in resto.split(",") if p.strip()]
        if pasta and pedacos:
            grupos.setdefault(pasta, []).extend(pedacos)
    return {pasta: tuple(pedacos) for pasta, pedacos in grupos.items()}


def _normalizar(texto: str) -> str:
    """Minúsculas, sem acento e só com palavras: "Drum & Bass" -> "drum and bass"."""
    texto = strip_accents(texto or "").lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", " ", texto).strip()


def match_genre(raw: str, grupos: Optional[Dict[str, tuple]] = None) -> tuple:
    """Classifica o gênero cru da API. Devolve (pasta, estado).

    estado: "grupo" (casou com um grupo), "ignorado" (só casou com rótulos de
    `IGNORAR`) ou "desconhecido" (não casou com nada).

    Três regras mantêm o resultado previsível:

    - um pedaço só casa com **palavras inteiras**: "dance" não está em
      "Dancehall", nem "pop" em "Popular", nem "rap" em "Therapy";
    - em "Dance Pop" ou "Folk Rock" vale a **última** palavra, que é o gênero
      de base (o resto o qualifica): vira Pop e Rock, não Eletronica e Country;
    - "Hip-Hop/Rap" e "Pop/Rock" têm alternativas: vale a primeira que casar.
    """
    grupos = grupos or GRUPOS
    pecas = []  # (palavras, pasta, ordem)
    for ordem, (pasta, pedacos) in enumerate(grupos.items()):
        for pedaco in pedacos:
            palavras = tuple(_normalizar(pedaco).split())
            if palavras:
                pecas.append((palavras, pasta, ordem))

    ignorado = False
    for segmento in re.split(r"[/;,|]", raw or ""):
        texto = _normalizar(segmento).split()
        melhor = None
        for palavras, pasta, ordem in pecas:
            n = len(palavras)
            for i in range(len(texto) - n + 1):
                if tuple(texto[i:i + n]) != palavras:
                    continue
                if pasta == IGNORAR:
                    ignorado = True
                    continue
                # mais à direita, depois o pedaço mais longo, depois a ordem
                chave = (i + n, n, -ordem)
                if melhor is None or chave > melhor[0]:
                    melhor = (chave, pasta)
        if melhor is not None:
            return melhor[1], "grupo"
    return None, ("ignorado" if ignorado else "desconhecido")


def group_genre(raw: str, grupos: Optional[Dict[str, tuple]] = None) -> str:
    """Reduz o gênero da API a um dos grupos; devolve o original se não casar."""
    pasta, _estado = match_genre(raw, grupos)
    return pasta or raw


def resolve_genre(
    raw: str, grupos: Optional[Dict[str, tuple]] = None, manter_desconhecidos: bool = False
) -> Optional[str]:
    """Pasta de gênero para o que a API devolveu, ou None se não serve.

    None manda quem chamou para o gênero de reserva (nome da playlist). Isso
    vale para rótulos ignorados ("Bollywood") e, a menos que se peça para
    mantê-los, para gêneros que nenhum grupo reconhece: melhor uma pasta que
    já existe do que uma nova para cada rótulo estranho da API.
    """
    if not (raw or "").strip():
        return None
    pasta, estado = match_genre(raw, grupos)
    if pasta:
        return pasta
    if estado == "desconhecido" and manter_desconhecidos:
        return raw
    return None


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
        grupos: Optional[Dict[str, tuple]] = None,
        manter_desconhecidos: bool = False,
        timeout: float = 10.0,
        min_interval: float = 1.0,
        max_failures: int = 5,
        log: Optional[Callable[[str, str], None]] = None,
    ):
        self._cache = cache
        self._group = group
        self._grupos = grupos or None  # None = os grupos padrão
        self._manter = manter_desconhecidos
        self._timeout = timeout
        self._min_interval = min_interval
        self._max_failures = max_failures
        self._log = log or (lambda level, message: None)
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._failures = 0
        self.disabled = False

    def _resolver(self, bruto: str) -> Optional[str]:
        if not self._group:
            return bruto
        return resolve_genre(bruto, self._grupos, self._manter)

    def cached(self, artist: str, title: str) -> Optional[str]:
        """Gênero já consultado, pelas regras atuais. Nunca vai à rede.

        None = nada em cache. "" = há resposta, mas ela não serve como pasta
        (rótulo ignorado ou fora dos grupos): use o gênero de reserva.
        """
        artist, title = (artist or "").strip(), clean_title(title)
        if not title:
            return None
        chave = f"{strip_accents(artist).lower()}|{strip_accents(title).lower()}"
        guardado = self._cache.get_genre(chave)
        if not guardado:
            return None
        return self._resolver(guardado) or ""

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
            return self._resolver(guardado)

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
        return self._resolver(bruto)

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
