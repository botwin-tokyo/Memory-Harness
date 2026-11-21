from __future__ import annotations

from typing import Any

import httpx

import memory_contract as contract

NEUTRALITY = contract.NEUTRALITY_EMOTION_NAME


class HarnessWriteError(RuntimeError):
    pass


class HarnessWriter:
    def __init__(
        self, base_url: str, key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(path, json=payload)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise HarnessWriteError(f"harness {r.status_code} on {path}: {detail}")
        return r.json()  # type: ignore[no-any-return]

    def register_entity(
        self, kind: str, name: str, slug: str, description: str, aliases: list[str]
    ) -> str:
        out = self._post("/v1/entities", {
            "kind": kind, "name": name, "slug": slug,
            "description": description, "aliases": aliases,
        })
        return str(out["id"])

    def write_parent(
        self,
        *,
        body: str,
        entity_id: str,
        scope: str,
        tags: list[str],
        memory_id: str | None = None,
        learning_eligible: bool | None = None,
        emotion: str = NEUTRALITY,
        subject_class: str = "world",
    ) -> str:
        'The artifact hub node. Returns lineage.'
        payload: dict[str, Any] = {
            "type": "note", "body": body, "emotion": emotion,
            "subject_class": subject_class, "about": [entity_id],
            "scope": scope, "tags": tags,
        }
        if memory_id is not None:
            payload["memory_id"] = memory_id
        if learning_eligible is False:
            payload["learning_eligible"] = False
        return str(self._post("/v1/intake", payload)["lineage_id"])

    def write_chunk(
        self,
        *,
        body: str,
        entity_id: str,
        scope: str,
        parent_lineage: str,
        index: int,
        memory_id: str | None = None,
        extra_tags: list[str] | None = None,
        learning_eligible: bool | None = None,
    ) -> str:
        'One verbatim chunk note, `part_of` the parent, in one transaction.'
        payload: dict[str, Any] = {
            "type": "note", "body": body, "emotion": NEUTRALITY,
            "subject_class": "world", "about": [entity_id],
            "scope": scope,
            "tags": list(dict.fromkeys(
                [contract.CHUNK_TAG, f"chunk:{index}", *(extra_tags or [])])),
        }
        if memory_id is not None:
            payload["memory_id"] = memory_id
        else:
            payload["branches"] = [
                {"link_type": contract.PART_OF_LINK, "target": parent_lineage,
                 "direction": "out"}
            ]
        if learning_eligible is False:
            payload["learning_eligible"] = False
        return str(self._post("/v1/intake", payload)["lineage_id"])

    def write_memory(self, payload: dict[str, Any]) -> str:
        'A real (emotion-bearing) memory, not a neutral artifact.'
        return str(self._post("/v1/intake", payload)["lineage_id"])

    def write_leaf(
        self,
        *,
        body: str,
        entity_id: str,
        scope: str,
        tags: list[str],
        parent_lineage: str,
        memory_id: str | None = None,
        emotion: str = NEUTRALITY,
        subject_class: str = "world",
    ) -> str:
        payload: dict[str, Any] = {
            "type": "note", "body": body, "emotion": emotion,
            "subject_class": subject_class, "about": [entity_id],
            "scope": scope, "tags": list(dict.fromkeys(tags)),
        }
        if memory_id is not None:
            payload["memory_id"] = memory_id
        else:
            payload["branches"] = [
                {"link_type": contract.PART_OF_LINK, "target": parent_lineage,
                 "direction": "out"}
            ]
        return str(self._post("/v1/intake", payload)["lineage_id"])

    def find_hub_by_date(self, kind: str, date: str) -> str | None:
        'The lineage of the hub of `kind` tagged `date:{date}`, or None.'
        r = self._client.get("/v1/graph",
                             params={"kinds": kind, "fields": "full"})
        if r.status_code >= 400:
            return None
        want = f"date:{date}"
        for n in r.json().get("nodes") or []:
            if want in (n.get("tags") or []):
                return str(n.get("lineageId") or "") or None
        return None


    def ground(self, memory_lineage: str, artifact_lineage: str) -> dict[str, Any]:
        'The grounding edge: memory → detailed_by → artifact (the law).'
        return self.link(memory_lineage, artifact_lineage, contract.GROUNDS_LINK)

    def search(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        'Memories matching `query`, best first: [{lineage_id, text, kind}].'
        pack = self._post("/v1/recall", {"query": query, "token_budget": 2000})
        out: list[dict[str, Any]] = []
        for bucket in ("facts", "episodes", "preferences", "notes"):
            for item in pack.get(bucket) or []:
                out.append({"lineage_id": str(item["lineage_id"]),
                            "text": str(item.get("text") or ""),
                            "kind": bucket})
                if len(out) >= limit:
                    return out
        return out

    def named_hubs(self) -> list[dict]:
        r = self._client.get("/v1/graph", params={"fields": "full"})
        if r.status_code >= 400:
            return []
        out: list[dict] = []
        for n in r.json().get("nodes") or []:
            first = str(n.get("bodyPreview") or "").splitlines()[:1]
            if not first:
                continue
            head, lineage = first[0], str(n.get("lineageId") or "")
            for prefix, kind in (("[person] ", "person"), ("[repo] ", "repo")):
                if head.startswith(prefix) and lineage:
                    name = head[len(prefix):].strip()
                    if name:
                        out.append({"lineage_id": lineage, "name": name,
                                    "kind": kind})
                        if kind == "repo" and "/" in name:
                            out.append({"lineage_id": lineage,
                                        "name": name.rsplit("/", 1)[1],
                                        "kind": kind})
                    break
        return out

    def links_from(self, lineage_id: str) -> set[tuple[str, str]]:
        r = self._client.post("/v1/explore", json={"start_id": lineage_id,
                                                   "depth": 1, "direction": "out"})
        if r.status_code >= 400:
            return set()
        out: set[tuple[str, str]] = set()
        for item in r.json().get("items", []):
            if str(item.get("lineage_id")) != lineage_id:
                continue
            for link in item.get("links_out", []) or []:
                out.add((str(link.get("target_lineage_id")),
                         str(link.get("link_type"))))
        return out

    def link_once(self, src: str, dst: str, link_type: str) -> bool:
        'Create the edge unless it already exists. True if it was created.'
        if (dst, link_type) in self.links_from(src):
            return False
        self.link(src, dst, link_type)
        return True

    def link(self, src: str, dst: str, link_type: str) -> dict[str, Any]:
        'A typed edge between two existing lineages (active under this key).'
        return self._post("/v1/link", {
            "src_lineage_id": src,
            "dst_lineage_id": dst,
            "link_type": link_type,
        })

    def lineage_is_live(self, lineage_id: str) -> bool:
        'Is this lineage still a visible, active node in the graph?'
        try:
            r = self._client.get(
                "/v1/graph", params={"ids": lineage_id, "limit": 1})
        except httpx.HTTPError:
            return True
        if r.status_code >= 400:
            return True
        return bool(r.json().get("nodes"))

    def graph(self, limit: int = 2000) -> dict[str, Any]:
        r = self._client.get("/v1/graph", params={"limit": limit})
        if r.status_code >= 400:
            raise HarnessWriteError(f"harness {r.status_code} on /v1/graph: {r.text[:200]}")
        return r.json()  # type: ignore[no-any-return]

    def transition_link(self, link_id: str, action: str) -> dict[str, Any]:
        'Curator path: promote/reject a proposed edge.'
        return self._post("/v1/link", {"link_id": link_id, "action": action})

    def forget(self, lineage_id: str, reason: str) -> dict[str, Any]:
        return self._post("/v1/forget", {
            "selector": {"type": "note", "lineage_id": lineage_id},
            "reason": reason,
        })
