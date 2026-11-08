"""Text -> vector. Two embedders behind one interface:

- QwenEmbedder: Qwen3-Embedding-0.6B (Apache-2.0, 1,024 dims) on the local GPU, CPU fallback.
  Queries get a one-sentence task instruction; documents don't (that's how Qwen3-Embedding was
  trained, and it's worth 1-5% on retrieval benchmarks per the model card).
- FakeEmbedder: hashed bag-of-words, deterministic, no model download. Used by CI and tests.
"""

from __future__ import annotations

import hashlib
import math
import re
import threading
from typing import Protocol

from triagebench.config import settings

QUERY_INSTRUCTION = (
    "Instruct: Given an error from a failing CI job, retrieve past CI failures with the same "
    "root cause\nQuery: "
)


class Embedder(Protocol):
    dim: int
    name: str

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class QwenEmbedder:
    def __init__(self, model_name: str | None = None) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        self.name = model_name or settings().embed_model
        device = "cuda" if torch.cuda.is_available() else "cpu"
        kwargs = {"torch_dtype": torch.float16} if device == "cuda" else {}
        self._model = SentenceTransformer(self.name, device=device, model_kwargs=kwargs)
        self._model.max_seq_length = 2048  # chunks are short; this caps memory on a 6 GB GPU
        get_dim = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self.dim = int(get_dim() or settings().embed_dim)
        self._lock = threading.Lock()  # one GPU, many eval threads

    def _encode(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            vecs = self._model.encode(
                texts, batch_size=8, normalize_embeddings=True, show_progress_bar=False
            )
        return [list(map(float, v)) for v in vecs]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._encode([QUERY_INSTRUCTION + text])[0]


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{1,}|\d{3,}")


class FakeEmbedder:
    """Hashed bag of words. Similar wording -> similar vectors, which is all the tests need."""

    def __init__(self, dim: int | None = None) -> None:
        self.dim = dim or settings().embed_dim
        self.name = "fake-hash"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in _TOKEN.findall(text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def get_embedder(fake: bool = False) -> Embedder:
    return FakeEmbedder() if fake else QwenEmbedder()
