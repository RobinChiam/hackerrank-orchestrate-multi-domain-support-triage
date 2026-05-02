from __future__ import annotations

import math
import sqlite3
from array import array
from pathlib import Path

from models import CorpusChunk, RetrievalHit


class SQLiteVectorStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self._initialize()

    def _initialize(self) -> None:
        self.connection.executescript(
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
        self.connection.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return None if row is None else str(row["value"])

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO meta(key, value)
            VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )
        self.connection.commit()

    def replace_all(self, chunks: list[CorpusChunk], vectors: list[list[float]], corpus_hash: str) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("Chunk and vector counts do not match.")

        with self.connection:
            self.connection.execute("DELETE FROM chunks")
            for chunk, vector in zip(chunks, vectors):
                norm = math.sqrt(sum(value * value for value in vector)) or 1.0
                blob = array("f", vector).tobytes()
                self.connection.execute(
                    """
                    INSERT INTO chunks(
                        chunk_id, company, category, title, path, text, snippet, vector, norm
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk.chunk_id,
                        chunk.company,
                        chunk.category,
                        chunk.title,
                        chunk.path,
                        chunk.text,
                        chunk.snippet,
                        blob,
                        norm,
                    ),
                )
            self.connection.execute(
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
        rows = self.connection.execute(query, params).fetchall()

        query_norm = math.sqrt(sum(value * value for value in query_vector)) or 1.0
        area_tokens = {token for token in product_area.split("_") if token}
        hits: list[RetrievalHit] = []

        for row in rows:
            vector = array("f")
            vector.frombytes(row["vector"])
            similarity = self._cosine_similarity(query_vector, vector, query_norm, row["norm"])
            category = str(row["category"])
            title = str(row["title"]).lower()
            path = str(row["path"]).lower()
            if area_tokens and any(token in category or token in title or token in path for token in area_tokens):
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

    @staticmethod
    def _cosine_similarity(
        left: list[float], right: array, left_norm: float, right_norm: float
    ) -> float:
        dot = 0.0
        for left_value, right_value in zip(left, right):
            dot += left_value * float(right_value)
        return dot / (left_norm * right_norm)
