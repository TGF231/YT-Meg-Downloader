"""Fila persistente em SQLite.

Guarda a playlist extraída e o estado de cada faixa, para que uma execução
interrompida (fechamento do programa, bloqueio do servidor, queda de energia)
seja retomada sem reextrair a playlist nem rebaixar o que já foi salvo.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Sequence

STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

SCHEMA = """
CREATE TABLE IF NOT EXISTS playlist (
    id           INTEGER PRIMARY KEY CHECK (id = 1),
    url          TEXT NOT NULL,
    title        TEXT,
    extracted_at TEXT
);

CREATE TABLE IF NOT EXISTS items (
    video_id   TEXT PRIMARY KEY,
    position   INTEGER NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_items_status ON items(status, position);

CREATE TABLE IF NOT EXISTS genre_cache (
    key        TEXT PRIMARY KEY,
    genre      TEXT NOT NULL,
    fetched_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


@dataclass
class QueueStats:
    total: int = 0
    done: int = 0
    failed: int = 0
    pending: int = 0


class QueueStore:
    """Acesso serializado ao banco; usado pela thread de download e pela GUI."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            # WAL reduz o risco de corromper a fila se o processo morrer no meio.
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    # ---------------------------------------------------------------- playlist

    def get_playlist(self) -> Optional[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute("SELECT * FROM playlist WHERE id = 1")
            return cur.fetchone()

    def set_playlist(self, url: str, title: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO playlist (id, url, title, extracted_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET url=excluded.url, title=excluded.title, "
                "extracted_at=excluded.extracted_at",
                (url, title, _now()),
            )
            self._conn.commit()

    def clear_all(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM items")
            self._conn.execute("DELETE FROM playlist")
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

    # ------------------------------------------------------------------- itens

    def add_items(self, entries: Sequence[dict]) -> int:
        """Insere as faixas extraídas. Itens já conhecidos são preservados."""
        with self._lock:
            known = {
                row[0] for row in self._conn.execute("SELECT video_id FROM items").fetchall()
            }
            inserted = 0
            for position, entry in enumerate(entries):
                if entry["video_id"] not in known:
                    inserted += 1
                self._conn.execute(
                    "INSERT INTO items (video_id, position, url, title, status, attempts, updated_at) "
                    "VALUES (?, ?, ?, ?, 'pending', 0, ?) "
                    "ON CONFLICT(video_id) DO UPDATE SET "
                    "  position = excluded.position, "
                    "  title = COALESCE(NULLIF(excluded.title, ''), items.title)",
                    (entry["video_id"], position, entry["url"], entry.get("title") or "", _now()),
                )
            self._conn.commit()
        return inserted

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0])

    def stats(self) -> QueueStats:
        with self._lock:
            rows = self._conn.execute(
                "SELECT status, COUNT(*) AS n FROM items GROUP BY status"
            ).fetchall()
        stats = QueueStats()
        for row in rows:
            stats.total += row["n"]
            if row["status"] == STATUS_DONE:
                stats.done = row["n"]
            elif row["status"] == STATUS_FAILED:
                stats.failed = row["n"]
            else:
                stats.pending += row["n"]
        return stats

    def next_pending(self) -> Optional[QueueItem]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM items WHERE status = ? ORDER BY position LIMIT 1",
                (STATUS_PENDING,),
            ).fetchone()
        return self._row_to_item(row) if row else None

    def filepath_owner(self, stem: str) -> Optional[str]:
        """Quem já baixou um arquivo com este caminho (sem extensão).

        Sem a pasta de álbum, duas faixas do mesmo artista podem gerar o mesmo
        nome; sem esta checagem o yt-dlp pularia a segunda achando que já a
        tinha, e a faixa sumiria em silêncio.
        """
        padrao = stem.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        with self._lock:
            row = self._conn.execute(
                "SELECT video_id FROM items WHERE filepath LIKE ? ESCAPE '\\' LIMIT 1",
                (padrao + ".%",),
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
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE items SET status = ?, filepath = ?, genre = ?, artist = ?, album = ?, "
                "title = COALESCE(NULLIF(?, ''), title), last_error = NULL, updated_at = ? "
                "WHERE video_id = ?",
                (STATUS_DONE, filepath, genre, artist, album, title, _now(), video_id),
            )
            self._conn.commit()

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

    def requeue_failed(self) -> int:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE items SET status = ?, attempts = 0, last_error = NULL, updated_at = ? "
                "WHERE status = ?",
                (STATUS_PENDING, _now(), STATUS_FAILED),
            )
            self._conn.commit()
            return cur.rowcount

    def list_items(self, status: Optional[str] = None, limit: int = 0) -> List[QueueItem]:
        query = "SELECT * FROM items"
        params: list = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY position"
        if limit:
            query += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_item(row) for row in rows]

    def done_tree(self) -> List[tuple]:
        """(gênero, artista, álbum, quantidade) das faixas já baixadas."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT COALESCE(genre,'') g, COALESCE(artist,'') a, COALESCE(album,'') al, "
                "COUNT(*) n FROM items WHERE status = ? GROUP BY g, a, al ORDER BY g, a, al",
                (STATUS_DONE,),
            ).fetchall()
        return [(r["g"], r["a"], r["al"], r["n"]) for r in rows]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> QueueItem:
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
        )
