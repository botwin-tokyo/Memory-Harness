from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from memory_ingest.seed.parse import SeedRecord
from memory_ingest.state import StateDB


class SeedWriteError(RuntimeError):
    pass


@dataclass
class SeedOutcome:
    n: int
    action: str
    lineage_id: str
    revision: int
    title: str


class MemoryWriter:

    def __init__(
        self, base_url: str, seed_key: str, transport: httpx.BaseTransport | None = None
    ) -> None:
        if not seed_key:
            raise SeedWriteError(
                "Set SEED_KEY to a user-tier harness credential"
            )
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {seed_key}"},
            timeout=httpx.Timeout(60.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def whoami(self) -> dict[str, Any]:
        r = self._client.get("/v1/whoami")
        if r.status_code >= 400:
            raise SeedWriteError(f"whoami {r.status_code}: {r.text[:200]}")
        return r.json()  # type: ignore[no-any-return]

    def emotions(self) -> list[dict[str, Any]]:
        r = self._client.get("/v1/emotions")
        if r.status_code >= 400:
            raise SeedWriteError(f"emotions {r.status_code}: {r.text[:200]}")
        return r.json()  # type: ignore[no-any-return]

    def links_from(self, lineage_id: str) -> set[tuple[str, str]]:
        r = self._client.post("/v1/explore", json={"start_id": lineage_id, "depth": 1,
                                                   "direction": "out"})
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
        r = self._client.post("/v1/link", json={
            "src_lineage_id": src, "dst_lineage_id": dst, "link_type": link_type})
        if r.status_code >= 400:
            raise SeedWriteError(f"link {r.status_code}: {r.text[:200]}")
        return True

    def remember(self, payload: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post("/v1/remember", json=payload)
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise SeedWriteError(f"remember {r.status_code}: {detail}")
        return r.json()  # type: ignore[no-any-return]


def payload_for(rec: SeedRecord, chapter: str, memory_id: str | None) -> dict[str, Any]:
    tags = list(dict.fromkeys([*rec.tags, "seed", f"chapter:{chapter}"]))
    if rec.beat:
        tags.append(f"beat:{rec.beat}")
    if rec.anchor:
        tags.append("anchor")
    body: dict[str, Any] = {
        "type": rec.type,
        "body": rec.body,
        "emotion": rec.emotion_resolved or rec.emotion_name,
        "subject_class": rec.subject_class,
        "source_class": "self",
        "scope": "shared",
        "about": rec.about,
        "tags": tags,
    }
    if rec.anchor:
        body["confidence"] = 0.95
    if rec.category:
        body["category"] = rec.category
    if rec.item:
        body["item"] = rec.item
    if memory_id:
        body["memory_id"] = memory_id
    return body


class SeedLedger:
    '`seed:<chapter>:<n>` → what we minted, so a re-run revises or skips.'

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS seed_records ("
            " key TEXT PRIMARY KEY, lineage_id TEXT NOT NULL,"
            " content_hash TEXT NOT NULL, revision INTEGER NOT NULL,"
            " chapter TEXT, beat TEXT, track TEXT)"
        )
        cols = {r[1] for r in self._db._con.execute("PRAGMA table_info(seed_records)")}
        for col in ("chapter", "beat", "track"):
            if col not in cols:
                self._db._con.execute(f"ALTER TABLE seed_records ADD COLUMN {col} TEXT")
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def key(chapter: str, rec: SeedRecord) -> str:
        if rec.type == "preference" and rec.category:
            item = rec.item or "general"
            return f"seed:{chapter}:pref:{rec.category}:{item}"
        track = "ep" if rec.type == "episode" else "fact"
        return f"seed:{chapter}:{track}:{rec.n}"

    def get(self, key: str) -> dict[str, Any] | None:
        row = self._db._con.execute(
            "SELECT lineage_id, content_hash, revision FROM seed_records WHERE key=?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        return {"lineage_id": row[0], "content_hash": row[1], "revision": row[2]}

    def put(
        self, key: str, *, lineage_id: str, content_hash: str, revision: int,
        chapter: str = "", beat: str | None = None, track: str = "",
    ) -> None:
        self._db._con.execute(
            "INSERT INTO seed_records(key, lineage_id, content_hash, revision,"
            " chapter, beat, track) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET"
            " lineage_id=excluded.lineage_id, content_hash=excluded.content_hash,"
            " revision=excluded.revision, chapter=excluded.chapter,"
            " beat=excluded.beat, track=excluded.track",
            (key, lineage_id, content_hash, revision, chapter, beat, track),
        )
        self._db._con.commit()

    def beats(self, chapter: str) -> dict[str, list[dict[str, str]]]:
        out: dict[str, list[dict[str, str]]] = {}
        for lineage, beat, track in self._db._con.execute(
            "SELECT lineage_id, beat, track FROM seed_records"
            " WHERE chapter=? AND beat IS NOT NULL AND beat<>''",
            (chapter,),
        ):
            out.setdefault(str(beat), []).append(
                {"lineage_id": str(lineage), "track": str(track or "")}
            )
        return out

    def dump(self) -> str:
        rows = self._db._con.execute(
            "SELECT key, lineage_id, revision FROM seed_records ORDER BY key"
        ).fetchall()
        return json.dumps([{"key": r[0], "lineage": r[1], "rev": r[2]} for r in rows], indent=2)


def plan_and_write(
    records: list[SeedRecord],
    *,
    chapter: str,
    writer: MemoryWriter | None,
    ledger: SeedLedger,
    dry_run: bool,
) -> list[SeedOutcome]:
    'Decide mint / revise / skip per record and (unless dry) do it.'
    out: list[SeedOutcome] = []
    for rec in records:
        if not rec.ok:
            continue
        key = SeedLedger.key(chapter, rec)
        prior = ledger.get(key)
        target: str | None = rec.revises
        action = "mint"
        if rec.revises:
            action = "revise"
        elif prior:
            if prior["content_hash"] == rec.content_hash:
                out.append(SeedOutcome(rec.n, "skip", prior["lineage_id"],
                                       prior["revision"], rec.title))
                continue
            action, target = "revise", prior["lineage_id"]

        if dry_run or writer is None:
            out.append(SeedOutcome(rec.n, action, target or "(new)",
                                   (prior["revision"] + 1) if prior else 1, rec.title))
            continue

        result = writer.remember(payload_for(rec, chapter, target))
        lineage, revision = str(result["lineage_id"]), int(result["revision"])
        track = "ep" if rec.type == "episode" else (
            "pref" if rec.type == "preference" else "fact")
        ledger.put(key, lineage_id=lineage, content_hash=rec.content_hash,
                   revision=revision, chapter=chapter, beat=rec.beat, track=track)
        out.append(SeedOutcome(rec.n, action, lineage, revision, rec.title))
    return out
