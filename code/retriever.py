from __future__ import annotations

from pathlib import Path

from config import DATA_DIR, DEFAULT_EMBEDDING_DIMENSION
from corpus import compute_corpus_hash, load_corpus_chunks
from gemini_client import GeminiClient
from models import RetrievalHit
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

    def ensure_index(self, force_rebuild: bool = False) -> None:
        chunks = load_corpus_chunks(self.data_dir)
        corpus_hash = compute_corpus_hash(chunks, self.client.embedding_model)
        current_hash = self.store.get_meta("corpus_hash")
        if not force_rebuild and current_hash == corpus_hash:
            if self.verbose:
                print("Vector index is current.")
            return

        if self.verbose:
            print(f"Building vector index for {len(chunks)} corpus chunks...")
        vectors = self.client.embed_texts(
            [chunk.embedding_text for chunk in chunks],
            task_type="RETRIEVAL_DOCUMENT",
            titles=[chunk.title for chunk in chunks],
            output_dimensionality=DEFAULT_EMBEDDING_DIMENSION,
        )
        self.store.replace_all(chunks, vectors, corpus_hash)
        if self.verbose:
            print("Vector index build complete.")

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
