'Settings from the environment (systemd EnvironmentFile in production).'

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from memory_contract.environment import load_environment


def _read_secret(value: str) -> str:
    if not value:
        return ""
    p = Path(value)
    if not p.is_file():
        return value
    text = p.read_text(encoding="utf-8").strip()
    for line in text.splitlines():
        if "raw key" in line and ":" in line:
            return line.split(":", 1)[1].strip()
    return text.splitlines()[0].strip() if text else ""


class Settings(BaseModel):
    harness_base: str = "http://127.0.0.1:8080"
    harness_db: str = ".state/harness.db"
    blog_feed_url: str = ""
    harness_key: str = ""
    ocr_base: str = ""
    ocr_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8100
    artifact_scope: str = "reference"
    state_db: str = ".state/ingest/state.db"
    chromium_bin: str = "chromium-browser"
    fetch_max_bytes: int = 20 * 1024 * 1024
    fetch_timeout_s: float = 30.0
    render_timeout_s: float = 60.0
    chunk_tokens: int = 800
    chunk_overlap_tokens: int = 80
    git_allowlist: str = ""
    git_push_index: bool = False
    llm_base: str = ""
    llm_model: str = ""
    llm_key: str = ""
    seed_model_base: str = ""
    seed_model_key: str = ""
    seed_model_name: str = ""
    seed_model_temperature: float = 0.7
    seed_model_max_tokens: int = 8000
    seed_prompt_path: str = ""
    seed_facts_prompt_path: str = ""
    seed_key: str = ""
    seed_ledger: str = ""
    seed_outdir: str = ""
    embed_base: str = ""
    embed_key: str = ""
    emotion_floor: float = 0.55
    emotion_cache: str = ""
    seed_api_key: str = ""
    library_dir: str = ".state/library"
    allow_private_fetch: bool = False


def get_settings() -> Settings:
    load_environment()
    return Settings(
        harness_base=os.environ.get("HARNESS_BASE", "http://127.0.0.1:8080"),
        harness_db=os.environ.get("MEMORY_HARNESS_DB__PATH", ".state/harness.db"),
        blog_feed_url=os.environ.get("BLOG_FEED_URL", ""),
        harness_key=os.environ.get("INGEST_HARNESS_KEY", ""),
        ocr_base=os.environ.get("OCR_BASE", ""),
        ocr_key=os.environ.get("OCR_KEY", ""),
        host=os.environ.get("INGEST_HOST", "127.0.0.1"),
        port=int(os.environ.get("INGEST_PORT", "8100")),
        artifact_scope=os.environ.get("ARTIFACT_SCOPE", "reference"),
        state_db=os.environ.get(
            "INGEST_STATE_DB",
            ".state/ingest/state.db",
        ),
        chromium_bin=os.environ.get("CHROMIUM_BIN", "chromium-browser"),
        git_allowlist=os.environ.get("GIT_ALLOWLIST", ""),
        git_push_index=os.environ.get("GIT_PUSH_INDEX", "") == "1",
        llm_base=os.environ.get("LLM_BASE", ""),
        llm_model=os.environ.get("LLM_MODEL", ""),
        llm_key=os.environ.get("LLM_KEY", ""),
        allow_private_fetch=os.environ.get("INGEST_ALLOW_PRIVATE", "") == "1",
        seed_model_base=os.environ.get("SEED_MODEL_BASE")
        or os.environ.get("LLM_BASE", ""),
        seed_model_key=os.environ.get("SEED_MODEL_KEY") or os.environ.get("LLM_KEY", ""),
        seed_model_name=os.environ.get("SEED_MODEL_NAME")
        or os.environ.get("LLM_MODEL", ""),
        seed_model_temperature=float(os.environ.get("SEED_MODEL_TEMPERATURE", "0.7")),
        seed_model_max_tokens=int(os.environ.get("SEED_MODEL_MAX_TOKENS", "8000")),
        seed_prompt_path=os.environ.get("SEED_PROMPT_PATH", ""),
        seed_facts_prompt_path=os.environ.get("SEED_FACTS_PROMPT_PATH", ""),
        seed_key=_read_secret(os.environ.get("SEED_KEY", "")),
        seed_ledger=os.environ.get(
            "SEED_LEDGER", ".state/ingest/seed.db"
        ),
        seed_outdir=os.environ.get(
            "SEED_OUTDIR", ".state/drafts"
        ),
        embed_base=os.environ.get("EMBED_BASE", ""),
        embed_key=_read_secret(os.environ.get("EMBED_KEY", "")),
        emotion_floor=float(os.environ.get("EMOTION_FLOOR", "0.55")),
        seed_api_key=_read_secret(os.environ.get("SEED_API_KEY", "")),
        library_dir=os.environ.get("LIBRARY_DIR", ".state/library"),
        emotion_cache=os.environ.get(
            "EMOTION_CACHE",
            ".state/ingest/emotion_vectors.json",
        ),
    )
