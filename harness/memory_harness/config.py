from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from memory_contract.environment import load_environment
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

DEFAULT_CONFIG_PATH = Path("config.toml")
CONFIG_PATH_ENV = "MEMORY_HARNESS_CONFIG"


class ServerSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8080
    cors_origins: list[str] = []


class DbSettings(BaseModel):
    path: str = ".state/harness.db"
    blob_dir: str = ".state/blobs"
    busy_timeout_ms: int = 5000
    inline_body_max_bytes: int = 4096


class EmbeddingsSettings(BaseModel):

    base_url: str = ""
    api_key: str = ""
    timeout_s: float = 30.0
    dim: int = 1024
    max_batch: int = 128


class LlmSettings(BaseModel):

    base_url: str = ""
    model: str = ""
    api_key: str = ""
    max_tokens: int = 8192
    timeout_s: float = 180.0


class RecallWeights(BaseModel):
    relevance: float = 2.0
    freshness: float = 0.3
    confidence: float = 0.4
    source_trust: float = 0.3
    vector_leg: float = 0.5


class RecallSettings(BaseModel):
    default_token_budget: int = 8000
    max_preferences: int = 12
    max_facts: int = 20
    max_episodes: int = 6
    fact_budget_floor: float = 0.5
    knn_k: int = 200
    max_procedures: int = 1
    staleness_penalty: float = 0.1
    dispute_penalty: float = 0.3
    weights: RecallWeights = Field(default_factory=RecallWeights)


class SecretsSettings(BaseModel):
    mode_default: Literal["store-but-dont-train", "train-raw"] = "store-but-dont-train"
    entropy_threshold: float = 4.5


class CoreSettings(BaseModel):
    shared_budget_tokens: int = 800
    agent_budget_tokens: int = 800


class GraphSettings(BaseModel):
    expand_hops: int = 2
    hop_decay: float = 0.5
    explore_max_depth: int = 5


class Settings(BaseSettings):
    server: ServerSettings = Field(default_factory=ServerSettings)
    db: DbSettings = Field(default_factory=DbSettings)
    embeddings: EmbeddingsSettings = Field(default_factory=EmbeddingsSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    recall: RecallSettings = Field(default_factory=RecallSettings)
    secrets: SecretsSettings = Field(default_factory=SecretsSettings)
    core: CoreSettings = Field(default_factory=CoreSettings)
    graph: GraphSettings = Field(default_factory=GraphSettings)

    model_config = SettingsConfigDict(
        env_prefix="MEMORY_HARNESS_",
        env_nested_delimiter="__", env_file=".env", env_ignore_empty=True,
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        toml_file = settings_cls.model_config.get("toml_file")
        sources: list[PydanticBaseSettingsSource] = [init_settings, env_settings, dotenv_settings]
        if toml_file is not None and Path(str(toml_file)).is_file():
            sources.append(TomlConfigSettingsSource(settings_cls, toml_file=toml_file))
        sources.append(file_secret_settings)
        return tuple(sources)


def load_settings(path: str | Path) -> Settings:
    'Load Settings from an explicit TOML file (env vars still override).'

    class _TomlSettings(Settings):
        model_config = SettingsConfigDict(
            env_prefix="MEMORY_HARNESS_",
            env_nested_delimiter="__", env_file=".env", env_ignore_empty=True,
            toml_file=str(path) if Path(path).is_file() else None,
            extra="ignore",
        )

    return _TomlSettings()  # type: ignore[call-arg]


@lru_cache
def get_settings() -> Settings:
    'Process-wide settings: $MEMORY_HARNESS_CONFIG, else the node default path.'
    load_environment()
    path = os.environ.get(CONFIG_PATH_ENV, str(DEFAULT_CONFIG_PATH))
    return load_settings(path)
