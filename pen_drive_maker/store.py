"""Catálogo da biblioteca, em SQLite.

Cada faixa é identificada pelo id do vídeo e guarda **onde foi salva**, como
caminho relativo à pasta da biblioteca. É esse vínculo — origem do download e
local de destino — que permite:

- baixar uma segunda playlist sem repetir o que a primeira já trouxe;
- notar faixas apagadas e baixá-las de novo no mesmo lugar;
- respeitar uma estrutura de pastas montada à mão (faixa *fixada*);
- reproduzir a biblioteca inteira em outro pen drive.

O caminho é relativo para o catálogo continuar valendo quando a mesma
biblioteca aparece sob outra letra de unidade, ou é copiada para outro disco.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
# Removida da biblioteca pelo usuário: fica no catálogo justamente para uma
# atualização da playlist não trazê-la de volta.
STATUS_EXCLUDED = "excluded"

STATE_DIRNAME = ".pen-drive-maker"
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    video_id   TEXT PRIMARY KEY,
    position   INTEGER NOT NULL DEFAULT 0,
    url        TEXT NOT NULL,
    title      TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    filepath   TEXT,
    genre      TEXT,
    artist     TEXT,
    album      TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS playlists (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    url          TEXT NOT NULL UNIQUE,
    title        TEXT,
    extracted_at TEXT
);

CREATE TABLE IF NOT EXISTS playlist_items (
    playlist_id INTEGER NOT NULL,
    video_id    TEXT NOT NULL,
    position    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (playlist_id, video_id)
);

CREATE TABLE IF NOT EXISTS genre_cache (
    key        TEXT PRIMARY KEY,
    genre      TEXT NOT NULL,
    fetched_at TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""

# Colunas que a versão 1 do banco não tinha.
_NEW_ITEM_COLUMNS = (
    ("relpath", "TEXT"),
    ("pinned", "INTEGER NOT NULL DEFAULT 0"),
    ("track_number", "INTEGER"),
)

# Arquivos que aparecem ao lado do áudio durante o download e não contam como ele.
_NOT_AUDIO = {".part", ".ytdl", ".temp", ".tmp", ".jpg", ".jpeg", ".png", ".webp", ".json"}


def file_present(filepath: str) -> bool:
    """O arquivo baixado ainda existe no disco?

    Aceita também outra extensão com o mesmo nome: se o yt-dlp gravou o
    caminho antes da conversão (.webm) e o arquivo final é .mp3, a faixa está
    no disco do mesmo jeito, e marcá-la como ausente a baixaria de novo.
    """
    try:
        if os.path.isfile(filepath):
            return True
        pasta, nome = os.path.split(filepath)
        base = os.path.splitext(nome)[0]
        if not base or not os.path.isdir(pasta):
            return False
        for entrada in os.listdir(pasta):
            raiz, extensao = os.path.splitext(entrada)
            if raiz == base and extensao.lower() not in _NOT_AUDIO:
                return True
    except OSError:
        return False
    return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_posix(relpath: str) -> str:
    """Caminho relativo sempre com "/", para o catálogo valer em qualquer sistema."""
    return relpath.replace("\\", "/")


@dataclass
class QueueItem:
    video_id: str
    position: int
    url: str
    title: str
    status: str
    attempts: int
    last_error: Optional[str] = None
    filepath: Optional[str] = None
    genre: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    relpath: Optional[str] = None
    pinned: bool = False
    track_number: Optional[int] = None
    playlists: str = ""


@dataclass
class QueueStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    pending: int = 0
    excluded: int = 0


class QueueStore:
    """Acesso serializado ao catálogo; usado pela thread de download e pela GUI."""

    def __init__(self, db_path: Path, root: Optional[Path] = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if root is not None:
            self.root: Optional[Path] = Path(root)
        elif self.db_path.parent.name == STATE_DIRNAME:
            # O catálogo mora dentro da própria biblioteca.
            self.root = self.db_path.parent.parent
        else:
            self.root = None
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            # WAL reduz o risco de corromper o catálogo se o processo morrer no meio.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._migrate()
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # -------------------------------------------------------------- migração

    def _migrate(self) -> None:
        """Leva um banco da versão 1 (uma playlist só) ao catálogo da versão 2.

        Nada é perdido: a playlist antiga vira a primeira da biblioteca, cada
        faixa fica ligada a ela na mesma posição, e os caminhos absolutos
        ganham a forma relativa sempre que estiverem dentro da biblioteca.
        """
        colunas = {row["name"] for row in self._conn.execute("PRAGMA table_info(items)")}
        for nome, tipo in _NEW_ITEM_COLUMNS:
            if nome not in colunas:
                self._conn.execute(f"ALTER TABLE items ADD COLUMN {nome} {tipo}")

        antiga = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='playlist'"
        ).fetchone()
        if antiga is not None:
            for linha in self._conn.execute("SELECT * FROM playlist").fetchall():
                pid = self._upsert_playlist(linha["url"], linha["title"] or "")
                self._conn.execute(
                    "INSERT OR IGNORE INTO playlist_items (playlist_id, video_id, position) "
                    "SELECT ?, video_id, position FROM items",
                    (pid,),
                )
            self._conn.execute("DROP TABLE playlist")

        if self.root is not None:
            for linha in self._conn.execute(
                "SELECT video_id, filepath FROM items "
                "WHERE relpath IS NULL AND filepath IS NOT NULL AND filepath != ''"
            ).fetchall():
                relativo = self._relative(linha["filepath"])
                if relativo:
                    self._conn.execute(
                        "UPDATE items SET relpath = ? WHERE video_id = ?",
                        (relativo, linha["video_id"]),
                    )

        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )

    # -------------------------------------------------------------- caminhos

    def _relative(self, filepath: str) -> Optional[str]:
        if self.root is None or not filepath:
            return None
        try:
            relativo = _as_posix(os.path.relpath(filepath, self.root))
        except ValueError:  # outra unidade no Windows
            return None
        # Fora da biblioteca não há caminho relativo que valha em outro disco.
        if relativo == ".." or relativo.startswith("../"):
            return None
        return relativo

    def location(self, item: QueueItem) -> Optional[Path]:
        """Onde a faixa deveria estar agora, preferindo o caminho relativo."""
        if item.relpath and self.root is not None:
            return self.root / item.relpath
        if item.filepath:
            return Path(item.filepath)
        return None

    # ------------------------------------------------------------- playlists

    def _upsert_playlist(self, url: str, title: str) -> int:
        self._conn.execute(
            "INSERT INTO playlists (url, title, extracted_at) VALUES (?, ?, ?) "
            "ON CONFLICT(url) DO UPDATE SET "
            "  title = COALESCE(NULLIF(excluded.title, ''), playlists.title), "
            "  extracted_at = excluded.extracted_at",
            (url, title, _now()),
        )
        return int(
            self._conn.execute("SELECT id FROM playlists WHERE url = ?", (url,)).fetchone()[0]
        )

    def add_playlist(self, url: str, title: str) -> int:
        with self._lock:
            pid = self._upsert_playlist(url, title)
            self._conn.commit()
            return pid

    # Nome antigo, mantido para quem ainda o chama.
    set_playlist = add_playlist

    def get_playlist_by_url(self, url: str) -> Optional[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM playlists WHERE url = ?", (url,)
            ).fetchone()

    def get_playlist(self) -> Optional[sqlite3.Row]:
        """A playlist acrescentada ou atualizada por último."""
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM playlists ORDER BY extracted_at DESC, id DESC LIMIT 1"
            ).fetchone()

    def list_playlists(self) -> List[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT p.*, (SELECT COUNT(*) FROM playlist_items pi "
                "             WHERE pi.playlist_id = p.id) AS faixas "
                "FROM playlists p ORDER BY p.id"
            ).fetchall()

    def playlist_size(self, playlist_id: int) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM playlist_items WHERE playlist_id = ?", (playlist_id,)
            ).fetchone()[0])

    def playlist_title_for(self, video_id: str) -> str:
        """Nome da primeira playlist que trouxe a faixa (vira pasta de gênero)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT p.title FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id "
                "WHERE pi.video_id = ? ORDER BY p.id LIMIT 1",
                (video_id,),
            ).fetchone()
        return (row["title"] if row else "") or ""

    def clear_all(self) -> None:
        """Esquece a biblioteca. O cache de gêneros fica: ele não depende dela."""
        with self._lock:
            self._conn.execute("DELETE FROM items")
            self._conn.execute("DELETE FROM playlists")
            self._conn.execute("DELETE FROM playlist_items")
            self._conn.commit()

    # ------------------------------------------------------ cache de gênero

    def get_genre(self, key: str) -> Optional[str]:
        """Gênero consultado antes. String vazia = consultado e não encontrado."""
        with self._lock:
            row = self._conn.execute(
                "SELECT genre FROM genre_cache WHERE key = ?", (key,)
            ).fetchone()
        return row["genre"] if row else None

    def set_genre(self, key: str, genre: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO genre_cache (key, genre, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET genre=excluded.genre, "
                "fetched_at=excluded.fetched_at",
                (key, genre, _now()),
            )
            self._conn.commit()

    def clear_genre_cache(self) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM genre_cache")
            self._conn.commit()
            return cur.rowcount

    # ---------------------------------------------------------------- faixas

    def add_items(self, entries: Sequence[dict], playlist_id: Optional[int] = None) -> int:
        """Acrescenta faixas ao catálogo. Devolve quantas eram novas.

        Uma faixa que já está na biblioteca — trazida por outra playlist, ou
        baixada antes — **não** volta para a fila: só ganha o vínculo com esta
        playlist. É isso que evita baixar duas vezes a mesma música.
        """
        with self._lock:
            known = {
                row[0] for row in self._conn.execute("SELECT video_id FROM items").fetchall()
            }
            inserted = 0
            for position, entry in enumerate(entries):
                if entry["video_id"] not in known:
                    inserted += 1
                    known.add(entry["video_id"])
                self._conn.execute(
                    "INSERT INTO items (video_id, position, url, title, status, attempts, updated_at) "
                    "VALUES (?, ?, ?, ?, 'pending', 0, ?) "
                    "ON CONFLICT(video_id) DO UPDATE SET "
                    "  title = COALESCE(NULLIF(excluded.title, ''), items.title)",
                    (entry["video_id"], position, entry["url"], entry.get("title") or "", _now()),
                )
                if playlist_id is not None:
                    self._conn.execute(
                        "INSERT INTO playlist_items (playlist_id, video_id, position) "
                        "VALUES (?, ?, ?) ON CONFLICT(playlist_id, video_id) "
                        "DO UPDATE SET position = excluded.position",
                        (playlist_id, entry["video_id"], position),
                    )
            self._conn.commit()
        return inserted

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM items WHERE status != ?", (STATUS_EXCLUDED,)
            ).fetchone()[0])

    def stats(self) -> QueueStats:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM items GROUP BY status"
            ).fetchall()
        stats = QueueStats()
        for row in rows:
            if row["status"] == STATUS_EXCLUDED:
                stats.excluded = row["n"]
                continue
            stats.total += row["n"]
            if row["status"] == STATUS_DONE:
                stats.done = row["n"]
            elif row["status"] == STATUS_FAILED:
                stats.failed = row["n"]
            else:
                stats.pending += row["n"]
        return stats

    def next_pending(self, exclude: Iterable[str] = ()) -> Optional[QueueItem]:
        """Próxima faixa da fila, pulando as que já estão em andamento."""
        # rowid = ordem de chegada: a primeira playlist vem antes da segunda.
        excluidas = list(exclude)
        filtro = ""
        if excluidas:
            filtro = " AND video_id NOT IN (%s)" % ",".join("?" * len(excluidas))
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM items WHERE status = ?" + filtro + " ORDER BY rowid LIMIT 1",
                (STATUS_PENDING, *excluidas),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def get_item(self, video_id: str) -> Optional[QueueItem]:
        with self._lock:
            row = self._conn.execute(
                self._SELECT_COM_PLAYLISTS + " WHERE i.video_id = ?", (video_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def artist_genre(self, artist: str) -> Optional[str]:
        """Gênero que predomina entre as faixas já baixadas deste artista.

        Empate: o que chegou primeiro. É o que mantém um artista numa pasta
        só, em vez de uma faixa em Pop e outra em Eletronica.
        """
        artista = (artist or "").strip().lower()
        if not artista:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT genre FROM items WHERE status = ? AND genre != '' "
                "AND LOWER(artist) = ? GROUP BY genre "
                "ORDER BY COUNT(*) DESC, MIN(rowid) LIMIT 1",
                (STATUS_DONE, artista),
            ).fetchone()
        return row["genre"] if row else None

    def filepath_owner(self, stem: str) -> Optional[str]:
        """Quem já ocupa um arquivo com este caminho (absoluto, sem extensão).

        Com menos pastas, duas faixas podem gerar o mesmo nome; sem esta
        checagem o yt-dlp pularia a segunda achando que já a tinha, e a faixa
        sumiria em silêncio.
        """
        def _like(texto: str) -> str:
            return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + ".%"

        relativo = self._relative(stem)
        with self._lock:
            row = self._conn.execute(
                "SELECT video_id FROM items WHERE status != ? AND ("
                " filepath LIKE ? ESCAPE '\\' OR relpath LIKE ? ESCAPE '\\') LIMIT 1",
                (STATUS_EXCLUDED, _like(stem), _like(relativo or "\0")),
            ).fetchone()
        return row["video_id"] if row else None

    def mark_done(
        self,
        video_id: str,
        filepath: str,
        genre: str = "",
        artist: str = "",
        album: str = "",
        title: str = "",
        track_number: Optional[int] = None,
        pinned: Optional[bool] = None,
    ) -> None:
        """Registra a faixa como salva, guardando as tags e o local relativo."""
        relativo = self._relative(filepath) if filepath else None
        with self._lock:
            self._conn.execute(
                "UPDATE items SET status = ?, filepath = ?, relpath = ?, genre = ?, "
                "artist = ?, album = ?, title = COALESCE(NULLIF(?, ''), title), "
                "track_number = COALESCE(?, track_number), "
                "pinned = COALESCE(?, pinned), last_error = NULL, updated_at = ? "
                "WHERE video_id = ?",
                (
                    STATUS_DONE, filepath or None, relativo, genre, artist, album, title,
                    track_number, None if pinned is None else int(pinned), _now(), video_id,
                ),
            )
            self._conn.commit()

    def set_location(self, video_id: str, relpath: str, pinned: bool) -> None:
        """Novo local de uma faixa, depois de movida (pelo usuário ou pelo programa)."""
        relpath = _as_posix(relpath)
        absoluto = str(self.root / relpath) if self.root is not None else relpath
        with self._lock:
            self._conn.execute(
                "UPDATE items SET relpath = ?, filepath = ?, pinned = ?, updated_at = ? "
                "WHERE video_id = ?",
                (relpath, absoluto, int(pinned), _now(), video_id),
            )
            self._conn.commit()

    def set_tags(
        self, video_id: str, artist: str = "", album: str = "", genre: str = "",
        track_number: Optional[int] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE items SET artist = ?, album = ?, genre = ?, track_number = ?, "
                "updated_at = ? WHERE video_id = ?",
                (artist, album, genre, track_number, _now(), video_id),
            )
            self._conn.commit()

    def playlist_links(self) -> dict:
        """{video_id: [url das playlists que trouxeram a faixa]}."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT pi.video_id, p.url FROM playlist_items pi "
                "JOIN playlists p ON p.id = pi.playlist_id ORDER BY p.id"
            ).fetchall()
        vinculos: dict = {}
        for row in rows:
            vinculos.setdefault(row["video_id"], []).append(row["url"])
        return vinculos

    def set_pinned(self, video_ids: Iterable[str], pinned: bool) -> int:
        ids = list(video_ids)
        with self._lock:
            self._conn.executemany(
                "UPDATE items SET pinned = ?, updated_at = ? WHERE video_id = ?",
                [(int(pinned), _now(), v) for v in ids],
            )
            self._conn.commit()
        return len(ids)

    def register_failure(self, video_id: str, error: str, max_retries: int, final: bool = False) -> int:
        """Incrementa a tentativa. Marca 'failed' ao esgotar as retentativas.

        Retorna o número de tentativas acumuladas.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM items WHERE video_id = ?", (video_id,)
            ).fetchone()
            attempts = (row["attempts"] if row else 0) + 1
            status = STATUS_FAILED if (final or attempts >= max_retries) else STATUS_PENDING
            self._conn.execute(
                "UPDATE items SET attempts = ?, status = ?, last_error = ?, updated_at = ? "
                "WHERE video_id = ?",
                (attempts, status, error[:2000], _now(), video_id),
            )
            self._conn.commit()
        return attempts

    def missing_items(self) -> List[QueueItem]:
        """Faixas marcadas como salvas cujo arquivo não está onde deveria.

        Só lê: decidir se a faixa foi movida ou apagada é com quem chama.
        Faixas sem local gravado ficam de fora — não há como provar que
        sumiram.
        """
        with self._lock:
            baixadas = [
                self._row_to_item(row)
                for row in self._conn.execute(
                    "SELECT * FROM items WHERE status = ?", (STATUS_DONE,)
                ).fetchall()
            ]
        # A checagem de disco fica fora do lock: milhares de arquivos numa
        # unidade lenta não devem travar a interface que também usa o banco.
        ausentes = []
        for item in baixadas:
            onde = self.location(item)
            if onde is not None and not file_present(str(onde)):
                ausentes.append(item)
        return ausentes

    def requeue(self, video_ids: Iterable[str], forget_location: bool = True) -> int:
        """Devolve faixas à fila, zerando as tentativas.

        `forget_location=False` mantém o local de uma faixa fixada, para ela
        ser baixada de novo exatamente onde estava.
        """
        ids = list(video_ids)
        with self._lock:
            if forget_location:
                self._conn.executemany(
                    "UPDATE items SET status = ?, attempts = 0, last_error = NULL, "
                    "filepath = CASE WHEN pinned = 1 THEN filepath ELSE NULL END, "
                    "relpath = CASE WHEN pinned = 1 THEN relpath ELSE NULL END, "
                    "updated_at = ? WHERE video_id = ?",
                    [(STATUS_PENDING, _now(), v) for v in ids],
                )
            else:
                self._conn.executemany(
                    "UPDATE items SET status = ?, attempts = 0, last_error = NULL, "
                    "updated_at = ? WHERE video_id = ?",
                    [(STATUS_PENDING, _now(), v) for v in ids],
                )
            self._conn.commit()
        return len(ids)

    def verify_files(self) -> List[QueueItem]:
        """Devolve à fila as faixas 'baixadas' cujo arquivo sumiu do disco.

        Quem chama deve garantir que a pasta da biblioteca está acessível: com
        o pen drive desconectado, todo arquivo pareceria ausente. Para também
        reconhecer arquivos movidos, use `library.reconcile`.
        """
        ausentes = self.missing_items()
        self.requeue(i.video_id for i in ausentes)
        return ausentes

    def requeue_failed(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE items SET status = ?, attempts = 0, last_error = NULL, updated_at = ? "
                "WHERE status = ?",
                (STATUS_PENDING, _now(), STATUS_FAILED),
            )
            self._conn.commit()
            return cur.rowcount

    def exclude(self, video_ids: Iterable[str]) -> int:
        ids = list(video_ids)
        with self._lock:
            self._conn.executemany(
                "UPDATE items SET status = ?, filepath = NULL, relpath = NULL, "
                "pinned = 0, updated_at = ? WHERE video_id = ?",
                [(STATUS_EXCLUDED, _now(), v) for v in ids],
            )
            self._conn.commit()
        return len(ids)

    def adopt(
        self, video_id: str, url: str, relpath: str, title: str = "",
        artist: str = "", album: str = "", genre: str = "",
        track_number: Optional[int] = None,
    ) -> bool:
        """Registra um arquivo que já estava na biblioteca, achado pela tag.

        Devolve True se a faixa era desconhecida. Fica fixada: foi o usuário
        quem a pôs ali, e uma reorganização não deve tirá-la do lugar.
        """
        relpath = _as_posix(relpath)
        absoluto = str(self.root / relpath) if self.root is not None else relpath
        with self._lock:
            existia = self._conn.execute(
                "SELECT 1 FROM items WHERE video_id = ?", (video_id,)
            ).fetchone() is not None
            self._conn.execute(
                "INSERT INTO items (video_id, position, url, title, status, attempts, "
                " filepath, relpath, pinned, genre, artist, album, track_number, updated_at) "
                "VALUES (?, 0, ?, ?, ?, 0, ?, ?, 1, ?, ?, ?, ?, ?) "
                "ON CONFLICT(video_id) DO UPDATE SET status = excluded.status, "
                " filepath = excluded.filepath, relpath = excluded.relpath, pinned = 1, "
                " attempts = 0, last_error = NULL, "
                " title = COALESCE(NULLIF(items.title, ''), excluded.title), "
                " genre = COALESCE(NULLIF(items.genre, ''), excluded.genre), "
                " artist = COALESCE(NULLIF(items.artist, ''), excluded.artist), "
                " album = COALESCE(NULLIF(items.album, ''), excluded.album), "
                " track_number = COALESCE(items.track_number, excluded.track_number), "
                " updated_at = excluded.updated_at",
                (video_id, url, title, STATUS_DONE, absoluto, relpath, genre, artist,
                 album, track_number, _now()),
            )
            self._conn.commit()
        return not existia

    # Inclui os nomes das playlists de cada faixa, na ordem em que chegaram.
    _SELECT_COM_PLAYLISTS = (
        "SELECT i.*, (SELECT GROUP_CONCAT(title, ', ') FROM ("
        "    SELECT p.title FROM playlist_items pi JOIN playlists p ON p.id = pi.playlist_id"
        "    WHERE pi.video_id = i.video_id ORDER BY p.id)) AS playlist_names "
        "FROM items i"
    )

    def list_items(self, status: Optional[str] = None, limit: int = 0) -> List[QueueItem]:
        query = self._SELECT_COM_PLAYLISTS
        params: list = []
        if status:
            query += " WHERE i.status = ?"
            params.append(status)
        query += " ORDER BY i.rowid"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def folder_tree(self) -> List[tuple]:
        """[(pastas, quantidade)] das faixas salvas, lido dos caminhos reais.

        Vem do local de cada arquivo, não das tags: com pastas montadas à mão,
        só o caminho diz como a biblioteca está organizada de fato.
        """
        contagem: dict = {}
        for item in self.list_items(STATUS_DONE):
            relativo = item.relpath
            if not relativo and item.filepath:
                relativo = self._relative(item.filepath) or item.filepath
            if not relativo:
                continue
            pastas = tuple(p for p in _as_posix(relativo).split("/")[:-1] if p)
            contagem[pastas] = contagem.get(pastas, 0) + 1
        return sorted(contagem.items())

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueueItem:
        chaves = row.keys()
        return QueueItem(
            video_id=row["video_id"],
            position=row["position"],
            url=row["url"],
            title=row["title"] or "",
            status=row["status"],
            attempts=row["attempts"],
            last_error=row["last_error"],
            filepath=row["filepath"],
            genre=row["genre"],
            artist=row["artist"],
            album=row["album"],
            relpath=row["relpath"] if "relpath" in chaves else None,
            pinned=bool(row["pinned"]) if "pinned" in chaves else False,
            track_number=row["track_number"] if "track_number" in chaves else None,
            playlists=(row["playlist_names"] or "") if "playlist_names" in chaves else "",
        )
