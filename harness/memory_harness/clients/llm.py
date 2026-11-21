from __future__ import annotations

import re

import httpx

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class LlmUnavailable(Exception):
    'The language model service is unreachable or returned an error. Extraction queues.'


class LlmClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        *,
        max_tokens: int = 8192,
        timeout_s: float = 180.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._model = model
        self._max_tokens = max_tokens
        self._has_key = bool(api_key)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.0) -> str:
        'One chat completion. Returns clean content only (D51).'
        if not self._has_key:
            raise LlmUnavailable("no llm api key configured")
        try:
            r = self._client.post(
                "chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": self._max_tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LlmUnavailable(str(e)) from e
        body = r.json()
        content = body["choices"][0]["message"].get("content") or ""
        return _THINK_RE.sub("", content).strip()

    def chat_tools(
        self,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]] | None,
        temperature: float = 0.0,
        timeout_s: float | None = None,
    ) -> dict[str, object]:
        'One completion that MAY call tools. Returns the raw assistant message.'
        if not self._has_key:
            raise LlmUnavailable("no llm api key configured")
        try:
            r = self._client.post(
                "chat/completions",
                json={
                    "model": self._model,
                    "messages": messages,
                    **({"tools": tools, "tool_choice": "auto"} if tools else {}),
                    "temperature": temperature,
                    "max_tokens": self._max_tokens,
                    "chat_template_kwargs": {"enable_thinking": False},
                },
                timeout=timeout_s if timeout_s is not None else None,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LlmUnavailable(str(e)) from e
        msg = dict(r.json()["choices"][0]["message"])
        if isinstance(msg.get("content"), str):
            msg["content"] = _THINK_RE.sub("", msg["content"]).strip()
        return msg

    def close(self) -> None:
        self._client.close()
