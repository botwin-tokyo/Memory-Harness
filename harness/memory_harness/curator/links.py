from __future__ import annotations

from memory_harness.clients.embeddings import EmbeddingsClient, EmbeddingsUnavailable
from memory_harness.clients.llm import LlmClient, LlmUnavailable
from memory_harness.curator.main import Curator
from memory_harness.domain.scope import OPT_IN_SCOPES
from memory_contract import GROUNDS_LINK, LINK_TYPES

SIMILARITY_MERGE = 0.9
SIMILARITY_CLEAR = 0.5
SIMILARITY_NOISE = 0.2

_JUDGE_PROMPT = """Do these two memory statements meaningfully relate to each
other, such that a knowledge graph should link them? Answer with one word:
yes or no.

A: {a}
B: {b}"""


def _gloss(c: Curator, lineage_id: str) -> str:
    "The target's HEAD text via the API (history endpoint)."
    r = c.harness.get(f"/v1/history/{lineage_id}", headers=c.headers)
    if r.status_code != 200:
        return ""
    revs = r.json()["revisions"]
    head = next((v for v in revs if v["is_head"]), None)
    return str(head["text"]) if head else ""


def _transition(c: Curator, link_id: str, action: str) -> bool:
    r = c.harness.post(
        "/v1/link", json={"link_id": link_id, "action": action}, headers=c.headers
    )
    return r.status_code == 200


def curate_links(c: Curator) -> dict[str, list[str]]:
    'One curation pass over newly proposed links (feed cursor tracks them).'
    out: dict[str, list[str]] = {"activated": [], "rejected": [], "deferred": []}
    feed = c.harness.get(
        "/v1/changes",
        params={"since": c.cursor, "types": "link_proposed", "limit": 200},
        headers=c.headers,
    )
    if feed.status_code != 200:
        return out
    body = feed.json()
    c.cursor = int(body["next_cursor"])
    proposals = []
    for i in body["items"]:
        rec = i["record"]
        if not rec or rec["status"] != "proposed":
            continue
        if str(rec.get("scope") or "") in OPT_IN_SCOPES:
            out["deferred"].append(str(rec["link_id"]))
            continue
        if str(rec["link_type"]) not in LINK_TYPES:
            out["deferred"].append(str(rec["link_id"]))
            continue
        if str(rec["link_type"]) == GROUNDS_LINK:
            if _transition(c, str(rec["link_id"]), "promote"):
                out["activated"].append(str(rec["link_id"]))
            continue
        proposals.append(rec)

    groups: dict[tuple[str, str], list[dict]] = {}
    for rec in proposals:
        groups.setdefault((str(rec["src_lineage_id"]), str(rec["link_type"])), []).append(rec)

    for (src, _ltype), recs in groups.items():
        kept: list[tuple[dict, str]] = []
        for rec in recs:
            gloss = _gloss(c, str(rec["dst_lineage_id"]))
            dup = False
            for kept_rec, kept_gloss in kept:
                try:
                    sim = c.embeddings.similarity([gloss], [kept_gloss])[0][0]
                except EmbeddingsUnavailable:
                    sim = 0.0
                if sim > SIMILARITY_MERGE:
                    if _transition(c, str(rec["link_id"]), "reject"):
                        out["rejected"].append(str(rec["link_id"]))
                    dup = True
                    break
            if not dup:
                kept.append((rec, gloss))

        src_gloss = _gloss(c, src)
        for rec, gloss in kept:
            link_id = str(rec["link_id"])
            try:
                sim = c.embeddings.similarity([src_gloss], [gloss])[0][0]
            except EmbeddingsUnavailable:
                out["deferred"].append(link_id)
                continue
            if sim < SIMILARITY_NOISE:
                if _transition(c, link_id, "reject"):
                    out["rejected"].append(link_id)
            elif sim >= SIMILARITY_CLEAR:
                if _transition(c, link_id, "promote"):
                    out["activated"].append(link_id)
            else:
                try:
                    answer = c.llm.chat(
                        [{"role": "user", "content": _JUDGE_PROMPT.format(a=src_gloss, b=gloss)}]
                    ).lower()
                except LlmUnavailable:
                    out["deferred"].append(link_id)
                    continue
                if "yes" in answer:
                    if _transition(c, link_id, "promote"):
                        out["activated"].append(link_id)
                elif "no" in answer:
                    if _transition(c, link_id, "reject"):
                        out["rejected"].append(link_id)
                else:
                    out["deferred"].append(link_id)
    return out
