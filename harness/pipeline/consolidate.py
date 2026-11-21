from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from memory_harness.clients.llm import LlmClient

REPETITION_THRESHOLD = 5
SESSION_MIN_EVENTS = 3

_EPISODE_PROMPT = """Stitch these captured events into ONE episode for a memory
harness: what happened, the outcome, and the lesson. Collapse chatter.

Output ONLY JSON: {{"summary": "<2-4 sentences>", "outcome": "<one sentence>",
"emotion": "<exactly one of the vocabulary>"}}

Vocabulary: {vocab}

EVENTS:
{events}"""

_PROMOTE_PROMPT = """This statement was observed {n} times in agent traffic.
Write it as one crisp durable fact and pick its emotion.

Output ONLY JSON: {{"body": "<one clear statement>", "emotion": "<exactly one
of the vocabulary>"}}

Vocabulary: {vocab}

STATEMENT: {statement}"""

_DISTILL_PROMPT = """This is a build journal from a completed project. Distill
the durable LESSONS as 2-5 facts a future agent should know (environment,
gotchas, recovery steps, preferences). Each fact gets an emotion.

Output ONLY a JSON array: [{{"body": "<one clear lesson>", "emotion": "<exactly
one of the vocabulary>", "confidence": 0.7, "about": ["<subject>"], "tags": ["<topic>"]}}]

Vocabulary: {vocab}

JOURNAL:
\"\"\"
{journal}
\"\"\""""


@dataclass
class ConsolidateResult:
    events_seen: int = 0
    own_events_skipped: int = 0
    episodes_created: int = 0
    facts_promoted: int = 0
    facts_distilled: int = 0
    next_cursor: int = 0


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return f"{prefix}-{digest}"


def _json_from(text: str, array: bool = False):  # type: ignore[no-untyped-def]
    pattern = r"\[.*\]" if array else r"\{.*\}"
    m = re.search(pattern, text, re.DOTALL)
    if m is None:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def _vocab(harness: httpx.Client, headers: dict[str, str]) -> str:
    rows = harness.get("/v1/emotions", headers=headers).json()
    return ", ".join(e["name"] for e in rows)


def _remember(harness: httpx.Client, headers: dict[str, str], payload: dict) -> dict | None:
    r = harness.post("/v1/remember", json=payload, headers=headers)
    return r.json() if r.status_code == 200 else None


def _link(harness: httpx.Client, headers: dict[str, str], src: str, dst: str, ltype: str) -> bool:
    r = harness.post("/v1/link", json={
        "src_lineage_id": src, "dst_lineage_id": dst, "link_type": ltype,
    }, headers=headers)
    return r.status_code == 200


def consolidate(
    harness: httpx.Client,
    key: str,
    llm: LlmClient,
    *,
    cursor: int = 0,
    repetition_threshold: int = REPETITION_THRESHOLD,
) -> ConsolidateResult:
    'One consolidation pass. HTTP only.'
    headers = {"Authorization": f"Bearer {key}"}
    result = ConsolidateResult()
    vocab = _vocab(harness, headers)
    own = harness.get("/v1/whoami", headers=headers).json()["principal_id"]

    feed = harness.get(
        "/v1/changes", params={"since": cursor, "limit": 500}, headers=headers
    ).json()
    result.next_cursor = int(feed["next_cursor"])
    items = feed["items"]
    own_events = [i for i in items if i["created_by_agent"] == own and i["target_type"]]
    result.own_events_skipped = len(own_events)
    inputs = [
        i for i in items if not (i["created_by_agent"] == own and i["target_type"])
    ]
    result.events_seen = len(inputs)

    capture = [
        i for i in inputs
        if i["event_type"] in ("user_msg", "assistant_msg", "tool_call", "tool_result")
        and i["content"]
    ]

    by_content: dict[str, list[dict]] = {}
    for i in capture:
        by_content.setdefault(" ".join(str(i["content"]).split()), []).append(i)
    for content, group in by_content.items():
        if len(group) < repetition_threshold:
            continue
        polished = _json_from(
            llm.chat([{"role": "user", "content": _PROMOTE_PROMPT.format(
                n=len(group), statement=content, vocab=vocab)}])
        )
        if not polished or not polished.get("body") or not polished.get("emotion"):
            continue
        out = _remember(harness, headers, {
            "type": "fact", "body": polished["body"], "emotion": polished["emotion"],
            "confidence": 0.85, "memory_id": _stable_id("con", content),
            "evidence_refs": [i["event_id"] for i in group],
            "about": group[0]["about"], "tags": group[0]["tags"],
        })
        if out:
            result.facts_promoted += 1

    by_session: dict[str, list[dict]] = {}
    for i in capture:
        by_session.setdefault(f"{i['scope']}|{i['created_by_agent']}", []).append(i)
    for window, group in by_session.items():
        if len(group) < SESSION_MIN_EVENTS:
            continue
        texts = "\n".join(f"- {i['content'][:200]}" for i in group[:20])
        ep = _json_from(
            llm.chat([{"role": "user", "content": _EPISODE_PROMPT.format(
                events=texts, vocab=vocab)}])
        )
        if not ep or not ep.get("summary") or not ep.get("emotion"):
            continue
        body = f"{ep['summary']}\nOutcome: {ep.get('outcome', 'n/a')}"
        out = _remember(harness, headers, {
            "type": "episode", "body": body, "emotion": ep["emotion"],
            "memory_id": _stable_id("con-ep", window, texts[:200]),
            "evidence_refs": [i["event_id"] for i in group],
            "scope": group[0]["scope"],
        })
        if out:
            result.episodes_created += 1

    for i in inputs:
        if i["event_type"] != "episode_created" or not i["record"]:
            continue
        journal_lineage = str(i["record"]["lineage_id"])
        journal_body = str(i["record"]["summary"])
        lessons = _json_from(
            llm.chat([{"role": "user", "content": _DISTILL_PROMPT.format(
                journal=journal_body[:6000], vocab=vocab)}]),
            array=True,
        )
        if not isinstance(lessons, list):
            continue
        for n, lesson in enumerate(lessons):
            if not isinstance(lesson, dict) or not lesson.get("body") or not lesson.get("emotion"):
                continue
            out = _remember(harness, headers, {
                "type": "fact", "body": lesson["body"], "emotion": lesson["emotion"],
                "confidence": float(lesson.get("confidence", 0.7)),
                "memory_id": _stable_id("dist", journal_lineage, str(n), str(lesson["body"])),
                "about": lesson.get("about", []), "tags": lesson.get("tags", []),
                "scope": i["record"]["scope"],
            })
            if out and _link(harness, headers, out["lineage_id"], journal_lineage, "detailed_by"):
                pass
            if out:
                result.facts_distilled += 1
    return result


def main() -> None:
    import os

    ap = argparse.ArgumentParser(description="Consolidation client (Pipeline PC).")
    ap.add_argument("--base-url", default="http://127.0.0.1:8080")
    ap.add_argument("--key-file", required=True)
    ap.add_argument("--cursor-file", default=None)
    args = ap.parse_args()

    key = ""
    for line in Path(args.key_file).read_text().splitlines():
        if line.startswith("raw key"):
            key = line.split(": ", 1)[1].strip()
    if not key:
        print(f"no raw key in {args.key_file}", file=sys.stderr)
        sys.exit(1)
    cursor = 0
    if args.cursor_file and Path(args.cursor_file).exists():
        cursor = int(Path(args.cursor_file).read_text().strip() or "0")

    from memory_harness.config import get_settings

    settings = get_settings()
    llm = LlmClient(
        settings.llm.base_url, settings.llm.model,
        os.environ.get("MEMORY_HARNESS_LLM__API_KEY", settings.llm.api_key),
        max_tokens=settings.llm.max_tokens, timeout_s=settings.llm.timeout_s,
    )
    with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
        result = consolidate(client, key, llm, cursor=cursor)
    if args.cursor_file:
        Path(args.cursor_file).write_text(str(result.next_cursor))
    print(json.dumps(result.__dict__, indent=2))


if __name__ == "__main__":
    main()
