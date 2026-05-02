from __future__ import annotations

import hashlib
import re
from pathlib import Path

from models import CorpusChunk


FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
TITLE_RE = re.compile(r'^title:\s*"?(.*?)"?\s*$', re.MULTILINE)
LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
IMAGE_RE = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
HTML_TAG_RE = re.compile(r"</?[^>]+>")
SEPARATOR_RE = re.compile(r"\n-{4,}\n")
WHITESPACE_RE = re.compile(r"[ \t]+")
BLANKS_RE = re.compile(r"\n{3,}")


def _infer_company(relative_path: Path) -> str:
    company = relative_path.parts[0].lower()
    mapping = {
        "claude": "Claude",
        "hackerrank": "HackerRank",
        "visa": "Visa",
    }
    return mapping.get(company, "Unknown")


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "general_support"


def _extract_title(text: str, fallback: str) -> str:
    match = TITLE_RE.search(text)
    if match:
        return WHITESPACE_RE.sub(" ", match.group(1)).strip()
    for line in text.splitlines():
        if line.startswith("# "):
            return WHITESPACE_RE.sub(" ", line[2:]).strip()
    return fallback


def _strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def _clean_markdown(text: str) -> str:
    text = _strip_frontmatter(text)
    text = IMAGE_RE.sub(" ", text)
    text = LINK_RE.sub(r"\1", text)
    text = HTML_TAG_RE.sub(" ", text)
    text = text.replace("\\\n", "\n")
    text = SEPARATOR_RE.sub("\n", text)
    text = WHITESPACE_RE.sub(" ", text)
    text = BLANKS_RE.sub("\n\n", text)
    return text.strip()


def _chunk_text(text: str, max_chars: int = 3500, overlap_chars: int = 300) -> list[str]:
    if len(text) <= max_chars:
        return [text]

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
            continue

        if current:
            chunks.append(current)
        if len(paragraph) <= max_chars:
            current = paragraph
            continue

        start = 0
        while start < len(paragraph):
            end = min(start + max_chars, len(paragraph))
            chunks.append(paragraph[start:end].strip())
            if end >= len(paragraph):
                current = ""
                break
            start = max(0, end - overlap_chars)
        else:
            current = ""

    if current:
        chunks.append(current)

    return [chunk for chunk in chunks if chunk]


def load_corpus_chunks(data_dir: Path) -> list[CorpusChunk]:
    chunks: list[CorpusChunk] = []
    for path in sorted(data_dir.rglob("*.md")):
        relative = path.relative_to(data_dir)
        raw_text = path.read_text(encoding="utf-8", errors="ignore")
        title = _extract_title(raw_text, fallback=path.stem)
        cleaned = _clean_markdown(raw_text)
        if not cleaned:
            continue

        company = _infer_company(relative)
        parent_parts = relative.parts[1:-1]
        category = _slugify(parent_parts[-1] if parent_parts else "general_support")
        article_chunks = _chunk_text(cleaned)

        for index, chunk_text in enumerate(article_chunks):
            chunk_key = f"{relative.as_posix()}::{index}"
            chunk_id = hashlib.sha1(chunk_key.encode("utf-8")).hexdigest()
            snippet = chunk_text[:320].replace("\n", " ").strip()
            embedding_text = (
                f"Company: {company}\n"
                f"Category: {category}\n"
                f"Title: {title}\n"
                f"Path: {relative.as_posix()}\n\n"
                f"{chunk_text}"
            )
            chunks.append(
                CorpusChunk(
                    chunk_id=chunk_id,
                    company=company,
                    category=category,
                    title=title,
                    path=relative.as_posix(),
                    text=chunk_text,
                    snippet=snippet,
                    embedding_text=embedding_text,
                )
            )

    return chunks


def compute_corpus_hash(chunks: list[CorpusChunk], embedding_model: str) -> str:
    digest = hashlib.sha256()
    digest.update(embedding_model.encode("utf-8"))
    for chunk in chunks:
        digest.update(chunk.chunk_id.encode("utf-8"))
        digest.update(chunk.embedding_text.encode("utf-8"))
    return digest.hexdigest()
