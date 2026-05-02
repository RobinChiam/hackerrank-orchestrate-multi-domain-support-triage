from __future__ import annotations

import math
import sqlite3
import threading
from array import array
from pathlib import Path
from typing import Sequence

from models import CorpusChunk, RetrievalHit


class SQLiteVectorStore:
    """SQLite-backed vector store with thread-local connections for safe concurrency."""

    def __init__(self, path: Path) -> None:
        """Prepare the database path and initialize the schema."""
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._initialize()

    def _initialize(self) -> None:
        """Create the required tables and indexes if they do not already exist."""
        connection = self._create_connection()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    company TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    path TEXT NOT NULL,
                    text TEXT NOT NULL,
                    snippet TEXT NOT NULL,
                    vector BLOB NOT NULL,
                    norm REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_company ON chunks(company);
                CREATE INDEX IF NOT EXISTS idx_chunks_category ON chunks(category);
                """
            )
            connection.commit()
        finally:
            connection.close()

    def get_meta(self, key: str) -> str | None:
        """Return a stored metadata value, if present."""
        row = self._get_connection().execute(
            "SELECT value FROM meta WHERE key = ?",
            (key,),
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        """Persist a single metadata key/value pair."""
        connection = self._get_connection()
        connection.execute(
            """
            INSERT INTO meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        connection.commit()

    def replace_all(
        self,
        chunks: list[CorpusChunk],
        vectors: list[list[float]],
        corpus_hash: str,
    ) -> None:
        """Replace the entire chunk table contents with freshly embedded corpus data."""
        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts do not match.")

        records = []
        for chunk, vector in zip(chunks, vectors):
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            records.append(
                (
                    chunk.chunk_id,
                    chunk.company,
                    chunk.category,
                    chunk.title,
                    chunk.path,
                    chunk.text,
                    chunk.snippet,
                    array("f", vector).tobytes(),
                    norm,
                )
            )

        connection = self._get_connection()
        with connection:
            connection.execute("DELETE FROM chunks")
            connection.executemany(
                """
                INSERT INTO chunks(
                    chunk_id, company, category, title, path, text, snippet, vector, norm
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                records,
            )
            connection.execute(
                """
                INSERT INTO meta(key, value)
                VALUES('corpus_hash', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (corpus_hash,),
            )

    def search(
        self,
        query_vector: list[float],
        *,
        company: str | None,
        product_area: str,
        top_k: int,
    ) -> list[RetrievalHit]:
        """Search the local store and return the top ranked retrieval hits."""
        clauses = []
        params: list[str] = []
        if company and company != "Unknown":
            clauses.append("company = ?")
            params.append(company)

        query = (
            "SELECT chunk_id, company, category, title, path, text, snippet, vector, norm FROM chunks"
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = self._get_connection().execute(query, params).fetchall()

        query_values = array("f", query_vector)
        query_norm = math.sqrt(sum(value * value for value in query_values)) or 1.0
        area_tokens = {token for token in product_area.split("_") if token}
        hits: list[RetrievalHit] = []

        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector"])
            similarity = self._cosine_similarity(
                query_values,
                vector,
                query_norm,
                float(row["norm"]),
            )
            category = str(row["category"])
            title = str(row["title"]).lower()
            path = str(row["path"]).lower()
            if area_tokens and any(
                token in category or token in title or token in path for token in area_tokens
            ):
                similarity += 0.08
            hits.append(
                RetrievalHit(
                    chunk_id=str(row["chunk_id"]),
                    company=str(row["company"]),
                    category=category,
                    title=str(row["title"]),
                    path=str(row["path"]),
                    text=str(row["text"]),
                    snippet=str(row["snippet"]),
                    score=similarity,
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    def _get_connection(self) -> sqlite3.Connection:
        """Return the current thread's SQLite connection, creating it lazily."""
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._create_connection()
            self._local.connection = connection
        return connection

    def _create_connection(self) -> sqlite3.Connection:
        """Open a SQLite connection configured for row access by column name."""
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def _cosine_similarity(
        left: Sequence[float],
        right: Sequence[float],
        left_norm: float,
        right_norm: float,
    ) -> float:
        """Compute cosine similarity for two vectors using a generator-based dot product."""
        dot = sum(left_value * right_value for left_value, right_value in zip(left, right))
        return dot / (left_norm * right_norm)
