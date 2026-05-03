from __future__ import annotations

import os
from pathlib import Path

from config import DATA_DIR, DEFAULT_EMBEDDING_DIMENSION
from corpus import compute_corpus_hash, load_corpus_chunks
from gemini_client import GeminiClient
from models import RetrievalHit
from terminal_ui import VectorIndexBuildAnimation
from vector_store import SQLiteVectorStore


class RetrievalEngine:
    def __init__(
        self,
        *,
        client: GeminiClient,
        store: SQLiteVectorStore,
        data_dir: Path = DATA_DIR,
        verbose: bool = True,
    ) -> None:
        self.client = client
        self.store = store
        self.data_dir = data_dir
        self.verbose = verbose

    # ── Fast Existence & Freshness Checks ────────────────────────────────

    def index_exists(self) -> bool:
        """Return True if the SQLite index file exists and contains a valid corpus_hash.

        This is a lightweight O(1) check — no file I/O beyond a single SQLite
        query — suitable for fast startup gating.
        """
        if not self.store.path.exists():
            return False
        try:
            stored_hash = self.store.get_meta("corpus_hash")
            return stored_hash is not None and len(stored_hash) > 0
        except Exception:
            return False

    def _max_corpus_mtime(self) -> float:
        """Return the maximum mtime across all .md files in the data directory.

        Returns 0.0 if the data directory is missing or contains no markdown files.
        """
        if not self.data_dir.exists():
            return 0.0
        max_mtime = 0.0
        for md_path in self.data_dir.rglob("*.md"):
            try:
                mtime = os.path.getmtime(md_path)
                if mtime > max_mtime:
                    max_mtime = mtime
            except OSError:
                continue
        return max_mtime

    def is_index_stale(self) -> bool:
        """Check whether the corpus files have been modified since the last index build.

        Compares the current maximum mtime of the .md corpus files against the
        timestamp stored in the SQLite meta table at build time.  This takes
        milliseconds compared to the full-hash approach that reads and hashes
        every file's contents.

        Returns True (stale) if the index does not exist, has no stored
        timestamp, or the corpus has been modified since the last build.
        """
        if not self.index_exists():
            return True
        stored_ts = self.store.get_meta("corpus_mtime")
        if stored_ts is None:
            return True
        try:
            stored_mtime = float(stored_ts)
        except (ValueError, TypeError):
            return True
        return self._max_corpus_mtime() > stored_mtime

    # ── Index Build ──────────────────────────────────────────────────────

    def ensure_index(self, force_rebuild: bool = False) -> None:
        """Build the vector index if needed.

        Fast path (default):  Uses the mtime check to skip the heavy
        hash-and-embed pipeline when no corpus files have been touched.

        Slow path (force_rebuild=True or mtime indicates staleness):
        Loads all corpus chunks, computes the full content hash, and
        rebuilds the embeddings only if the hash has actually changed.
        """
        # Fast path — skip entirely when mtime proves nothing changed.
        if not force_rebuild and self.index_exists() and not self.is_index_stale():
            if self.verbose:
                print("Vector index is current (mtime check).")
            return

        # Slow path — load, hash, and rebuild if the content hash differs.
        chunks = load_corpus_chunks(self.data_dir)
        corpus_hash = compute_corpus_hash(chunks, self.client.embedding_model)
        current_hash = self.store.get_meta("corpus_hash")
        if not force_rebuild and current_hash == corpus_hash:
            # Content hash matches — persist the mtime so future checks are fast.
            self.store.set_meta("corpus_mtime", str(self._max_corpus_mtime()))
            if self.verbose:
                print("Vector index is current.")
            return

        animation = VectorIndexBuildAnimation(
            total_chunks=len(chunks),
            enabled=self.verbose,
        )
        animation.start("Embedding corpus chunks")
        try:
            vectors = self.client.embed_texts(
                [chunk.embedding_text for chunk in chunks],
                task_type="RETRIEVAL_DOCUMENT",
                titles=[chunk.title for chunk in chunks],
                output_dimensionality=DEFAULT_EMBEDDING_DIMENSION,
                on_progress=lambda completed, _total: animation.update(
                    phase="Embedding corpus chunks",
                    completed_chunks=completed,
                ),
            )
            animation.update(
                phase="Writing SQLite index",
                completed_chunks=len(chunks),
            )
            self.store.replace_all(chunks, vectors, corpus_hash)
            # Persist the mtime snapshot so future ensure_index calls can use
            # the fast path without hashing every file.
            self.store.set_meta("corpus_mtime", str(self._max_corpus_mtime()))
        except Exception:
            animation.stop()
            raise
        animation.stop(
            success_message=f"Vector index build complete ({len(chunks)} chunks)."
        )

    def search(
        self,
        *,
        company: str,
        product_area: str,
        subject: str,
        issue: str,
        top_k: int,
    ) -> list[RetrievalHit]:
        query = (
            f"Company: {company}\n"
            f"Product area hint: {product_area}\n"
            f"Subject: {subject}\n"
            f"Issue: {issue}"
        )
        vectors = self.client.embed_texts(
            [query],
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=DEFAULT_EMBEDDING_DIMENSION,
        )
        hits = self.store.search(
            vectors[0],
            company=company if company != "Unknown" else None,
            product_area=product_area,
            top_k=top_k,
        )
        if hits or company == "Unknown":
            return hits

        return self.store.search(
            vectors[0],
            company=None,
            product_area=product_area,
            top_k=top_k,
        )
