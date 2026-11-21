from __future__ import annotations

import logging
import re

import httpx

from memory_ingest.config import Settings

log = logging.getLogger("memory_ingest.llm")

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

INPUT_CAP_BYTES = 6 * 1024

_PROMPT = (
    "You are indexing a code repository. Summarize what this file is for in "
    "ONE line (max ~20 words). No preamble, no quotes, no trailing period "
    "needed — just the line.\n\nFile: {path}\n---\n{head}\n---"
)


class LlmUnavailable(RuntimeError):
    pass


class Summarizer:
    def __init__(
        self, settings: Settings, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._model = settings.llm_model
        self._has_key = bool(settings.llm_key)
        self._client = httpx.Client(
            base_url=settings.llm_base.rstrip("/"),
            headers={"Authorization": f"Bearer {settings.llm_key}"},
            timeout=httpx.Timeout(240.0, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _chat_once(self, prompt: str) -> str:
        r = self._client.post(
            "chat/completions",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 2048,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
        r.raise_for_status()
        content = r.json()["choices"][0]["message"].get("content") or ""
        return _THINK_RE.sub("", content).strip()

    def summarize(self, path: str, text: str) -> str:
        if not self._has_key:
            raise LlmUnavailable("LLM_KEY is not configured")
        head = text[:INPUT_CAP_BYTES]
        prompt = _PROMPT.format(path=path, head=head)
        try:
            try:
                out = self._chat_once(prompt)
            except httpx.TimeoutException:
                log.warning("summary timed out for %s — one patient retry", path)
                out = self._chat_once(prompt)
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"model service: {exc!r}") from exc
        return " ".join(out.split())[:300] or "(no summary)"
