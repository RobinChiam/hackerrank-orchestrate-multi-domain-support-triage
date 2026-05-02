from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from config import DEFAULT_EMBEDDING_DIMENSION


class GeminiAPIError(RuntimeError):
    pass


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        triage_model: str,
        response_model: str,
        embedding_model: str,
        timeout_seconds: int = 90,
    ) -> None:
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
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch_texts = texts[start : start + batch_size]
            batch_titles = (titles or [None] * len(texts))[start : start + batch_size]
            requests = []
            for text, title in zip(batch_texts, batch_titles):
                item: dict[str, Any] = {
                    "model": f"models/{self.embedding_model}",
                    "content": {"parts": [{"text": text}]},
                    "taskType": task_type,
                    "outputDimensionality": output_dimensionality,
                }
                if title and task_type == "RETRIEVAL_DOCUMENT":
                    item["title"] = title
                requests.append(item)

            payload = {"requests": requests}
            response = self._post(f"{self.embedding_model}:batchEmbedContents", payload)
            embeddings = response.get("embeddings", [])
            if len(embeddings) != len(batch_texts):
                raise GeminiAPIError(
                    f"Expected {len(batch_texts)} embeddings, got {len(embeddings)}"
                )
            for item in embeddings:
                values = item.get("values") or item.get("embedding", {}).get("values")
                if not values:
                    raise GeminiAPIError("Gemini embedding response did not include values.")
                vectors.append([float(value) for value in values])

        return vectors

    def _post(self, path_suffix: str, payload: dict[str, Any]) -> dict[str, Any]:
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
