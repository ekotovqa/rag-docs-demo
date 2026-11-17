from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Everything the app reads from the environment. `.env` is read when present."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    anthropic_api_key: str | None = None
    database_url: str = "postgresql://ragdemo:ragdemo@localhost:54329/ragdemo"

    ragdemo_model: str = "claude-opus-5-5"
    ragdemo_effort: str = "medium"
    ragdemo_fallbacks: bool = True
    ragdemo_top_k: int = 6
    ragdemo_cors_origins: str = "https://ekotovqa.github.io"  # the front door that wakes the app; comma-separated
    ragdemo_embed_model: str = "BAAI/bge-small-en-v1.5"
    ragdemo_corpus_dir: Path = Path("data/corpus")
    ragdemo_eval_dir: Path = Path("data/eval")
    ragdemo_model_cache: Path | None = None  # where fastembed / OCR models are cached


@lru_cache
def settings() -> Settings:
    return Settings()
