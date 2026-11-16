"""Local embeddings. fastembed runs the model on ONNX Runtime inside the process: no key, no network at query time."""
from __future__ import annotations

from pathlib import Path

from fastembed import TextEmbedding

# BGE models expect this prefix on short queries that look for passages; passages are embedded as they are.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5", cache_dir: Path | None = None) -> None:
        self.model_name = model_name
        self.model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir) if cache_dir else None)
        self.dim = len(next(iter(self.model.embed(["dimension probe"]))))

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self.model.embed(texts, batch_size=32)]

    def query(self, text: str) -> list[float]:
        return next(iter(self.model.embed([QUERY_PREFIX + text]))).tolist()
