from __future__ import annotations

import json
import logging
import re

import httpx

from memory_ingest.config import Settings

log = logging.getLogger("memory_ingest.seed.model")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class SeedModelUnavailable(RuntimeError):
    pass


def strip_think(text: str) -> str:
    out = _THINK_RE.sub("", text)
    if "<think>" in out:
        out = out.split("<think>", 1)[0]
    return out.strip()


class SeedModel:
    def __init__(
        self, settings: Settings, transport: httpx.BaseTransport | None = None
    ) -> None:
        self.name = settings.seed_model_name
        self.temperature = settings.seed_model_temperature
        self.max_tokens = settings.seed_model_max_tokens
        self._has_key = bool(settings.seed_model_key)
        self._client = httpx.Client(
            base_url=settings.seed_model_base.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.seed_model_key}"},
            timeout=httpx.Timeout(1800.0, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _chat_once(self, prompt: str) -> str:
        parts: list[str] = []
        with self._client.stream(
            "POST",
            "chat/completions",
            json={
                "model": self.name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "stream": True,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue
                piece = delta.get("content")
                if piece:
                    parts.append(piece)
        return strip_think("".join(parts))

    def complete(self, prompt: str) -> str:
        'One generation, with a single patient retry on timeout.'
        if not self._has_key:
            raise SeedModelUnavailable(
                "no seed model key configured (SEED_MODEL_KEY / LLM_KEY)"
            )
        try:
            try:
                return self._chat_once(prompt)
            except httpx.TimeoutException:
                log.warning("seed model timed out — one patient retry")
                return self._chat_once(prompt)
        except httpx.HTTPError as exc:
            raise SeedModelUnavailable(f"seed model: {exc!r}") from exc
