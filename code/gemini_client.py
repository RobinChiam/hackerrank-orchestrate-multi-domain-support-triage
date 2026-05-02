from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from config import DEFAULT_EMBEDDING_DIMENSION


class GeminiAPIError(RuntimeError):
    """Raised when a Gemini API request cannot be completed safely."""


class GeminiClient:
    """Minimal Gemini REST client built entirely on the Python standard library."""

    def __init__(
        self,
        api_key: str,
        triage_model: str,
        response_model: str,
        embedding_model: str,
        timeout_seconds: int = 90,
    ) -> None:
        """Store API configuration for structured generation and embeddings."""
        self.api_key = api_key
        self.triage_model = triage_model
        self.response_model = response_model
        self.embedding_model = embedding_model
        self.timeout_seconds = timeout_seconds
        self.base_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def generate_json(
        self,
        *,
        model: str,
        system_instruction: str,
        prompt: str,
        schema: dict[str, Any],
        temperature: float = 0.1,
    ) -> dict[str, Any]:
        """Generate JSON from Gemini using the provided response schema."""
        payload = {
            "system_instruction": {
                "parts": [{"text": system_instruction}],
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}],
                }
            ],
            "generationConfig": {
                "temperature": temperature,
                "responseMimeType": "application/json",
                "responseJsonSchema": schema,
            },
        }
        response = self._post(f"{model}:generateContent", payload)
        text = self._extract_text(response)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise GeminiAPIError(f"Gemini returned invalid JSON: {text}") from exc

    def embed_texts(
        self,
        texts: list[str],
        *,
        task_type: str,
        titles: list[str] | None = None,
        output_dimensionality: int = DEFAULT_EMBEDDING_DIMENSION,
        batch_size: int = 16,
    ) -> list[list[float]]:
        """Embed texts concurrently in batches while preserving input order."""
        if not texts:
            return []

        resolved_titles: list[str | None]
        if titles is None:
            resolved_titles = [None] * len(texts)
        else:
            resolved_titles = list(titles)
            if len(resolved_titles) != len(texts):
                raise ValueError("Text and title counts do not match.")

        batches = [
            (
                start,
                texts[start : start + batch_size],
                resolved_titles[start : start + batch_size],
            )
            for start in range(0, len(texts), batch_size)
        ]
        if len(batches) == 1:
            _, batch_texts, batch_titles = batches[0]
            return self._embed_batch(
                batch_texts,
                task_type=task_type,
                titles=batch_titles,
                output_dimensionality=output_dimensionality,
            )

        ordered_vectors: list[list[float] | None] = [None] * len(texts)
        max_workers = min(10, len(batches))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="gemini-embed") as executor:
            future_to_batch = {
                executor.submit(
                    self._embed_batch,
                    batch_texts,
                    task_type=task_type,
                    titles=batch_titles,
                    output_dimensionality=output_dimensionality,
                ): (start, len(batch_texts))
                for start, batch_texts, batch_titles in batches
            }

            for future in as_completed(future_to_batch):
                start, batch_length = future_to_batch[future]
                batch_vectors = future.result()
                if len(batch_vectors) != batch_length:
                    raise GeminiAPIError(
                        f"Expected {batch_length} embeddings, got {len(batch_vectors)}."
                    )
                ordered_vectors[start : start + batch_length] = batch_vectors

        if any(vector is None for vector in ordered_vectors):
            raise GeminiAPIError("One or more embedding batches did not return a result.")
        return [vector for vector in ordered_vectors if vector is not None]

    def _embed_batch(
        self,
        texts: list[str],
        *,
        task_type: str,
        titles: list[str | None],
        output_dimensionality: int,
    ) -> list[list[float]]:
        """Send one batch embedding request and normalize the response payload."""
        requests: list[dict[str, Any]] = []
        for text, title in zip(texts, titles):
            item: dict[str, Any] = {
                "model": f"models/{self.embedding_model}",
                "content": {"parts": [{"text": text}]},
                "taskType": task_type,
                "outputDimensionality": output_dimensionality,
            }
            if title and task_type == "RETRIEVAL_DOCUMENT":
                item["title"] = title
            requests.append(item)

        response = self._post(
            f"{self.embedding_model}:batchEmbedContents",
            {"requests": requests},
        )
        embeddings = response.get("embeddings", [])
        if len(embeddings) != len(texts):
            raise GeminiAPIError(f"Expected {len(texts)} embeddings, got {len(embeddings)}")

        vectors: list[list[float]] = []
        for item in embeddings:
            values = item.get("values") or item.get("embedding", {}).get("values")
            if not values:
                raise GeminiAPIError("Gemini embedding response did not include values.")
            vectors.append([float(value) for value in values])
        return vectors

    def _post(self, path_suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST a JSON request to Gemini with bounded retries for transient failures."""
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self.base_url}/{path_suffix}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
            },
        )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                details = exc.read().decode("utf-8", errors="ignore")
                if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2**attempt)
                    last_error = exc
                    continue
                raise GeminiAPIError(
                    f"Gemini API request failed with HTTP {exc.code}: {details}"
                ) from exc
            except urllib.error.URLError as exc:
                if attempt < 2:
                    time.sleep(2**attempt)
                    last_error = exc
                    continue
                raise GeminiAPIError(f"Gemini API request failed: {exc}") from exc
        raise GeminiAPIError(f"Gemini API request failed after retries: {last_error}")

    @staticmethod
    def _extract_text(response: dict[str, Any]) -> str:
        """Extract the primary text fragment from a Gemini generateContent response."""
        candidates = response.get("candidates", [])
        if not candidates:
            raise GeminiAPIError(f"Gemini returned no candidates: {response}")
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        fragments = [part.get("text", "") for part in parts if part.get("text")]
        text = "".join(fragments).strip()
        if not text:
            raise GeminiAPIError(f"Gemini returned an empty text payload: {response}")
        return text
