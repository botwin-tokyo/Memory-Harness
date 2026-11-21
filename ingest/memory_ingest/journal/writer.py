'A journal entry → its subtree. Idempotent; a re-send is a REVISION.'

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from memory_contract import (
    JOURNAL_SECTIONS,
    journal_lineage_key,
    journal_section_tags,
    journal_tags,
)

from memory_ingest.journal.model import JournalEntry
from memory_ingest.journal import wikilinks as wl
from memory_ingest.state import StateDB
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.journal.writer")

JOURNAL_SCOPE = "shared"

DEFAULT_EMOTION = "Neutrality"


@dataclass
class JournalResult:
    key: str
    date: str
    journal_type: str
    hub_lineage: str = ""
    minted: bool = False
    sections_written: int = 0
    sections_skipped: int = 0
    emotion: str = DEFAULT_EMOTION
    wikilinks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "date": self.date, "journal_type": self.journal_type,
            "hub": self.hub_lineage, "minted": self.minted,
            "sections_written": self.sections_written,
            "sections_skipped": self.sections_skipped,
            "emotion": self.emotion, "wikilinks": self.wikilinks,
        }


class JournalLedger:
    '`journal key` → the lineages we wrote, so a re-send revises in place.'

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS journal_entries ("
            " key TEXT PRIMARY KEY, date TEXT NOT NULL, journal_type TEXT NOT NULL,"
            " hub_lineage TEXT NOT NULL,"
            " section_lineages TEXT NOT NULL DEFAULT '{}',"
            " updated_at TEXT NOT NULL DEFAULT '')"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    def get(self, key: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT hub_lineage, section_lineages FROM journal_entries WHERE key=?",
            (key,)).fetchone()
        if row is None:
            return None
        return {"hub_lineage": row[0], "sections": json.loads(row[1])}

    def put(self, key: str, *, date: str, journal_type: str, hub_lineage: str,
            sections: dict[str, str], updated_at: str = "") -> None:
        self._db._con.execute(
            "INSERT INTO journal_entries(key, date, journal_type, hub_lineage,"
            " section_lineages, updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET hub_lineage=excluded.hub_lineage,"
            " section_lineages=excluded.section_lineages,"
            " updated_at=excluded.updated_at",
            (key, date, journal_type, hub_lineage, json.dumps(sections), updated_at))
        self._db._con.commit()

    def entries(self) -> list[dict]:
        return [{"key": r[0], "date": r[1], "journal_type": r[2],
                 "hub_lineage": r[3], "updated_at": r[4]}
                for r in self._db._con.execute(
                    "SELECT key, date, journal_type, hub_lineage, updated_at"
                    " FROM journal_entries ORDER BY date DESC")]


class JournalWriter:
    "Mints or revises one entry's hub and its section chunks."

    def __init__(self, writer: HarnessWriter, ledger: JournalLedger,
                 emotion_resolver: object = None,
                 propose_link: object = None) -> None:
        self.writer = writer
        self.ledger = ledger
        self.propose_link = propose_link
        self.resolve_emotion = emotion_resolver

    def _entity(self, entry: JournalEntry) -> str:
        'One collection entity per DAY, so a day is walkable as a unit.'
        slug = f"journal-{entry.date}"
        return self.writer.register_entity(
            "collection", f"Journal {entry.date}", slug,
            f"journal: {entry.date}", aliases=[slug],
        )

    def _emotion_for(self, entry: JournalEntry) -> str:
        text = entry.emotional_context
        if not text or self.resolve_emotion is None:
            return DEFAULT_EMOTION
        try:
            got = self.resolve_emotion(text)  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - a journal must land regardless
            log.warning("emotion resolution failed for %s: %s", entry.date, exc)
            return DEFAULT_EMOTION
        return str(got) if got else DEFAULT_EMOTION

    def write(self, entry: JournalEntry) -> JournalResult:
        key = journal_lineage_key(entry.date, entry.journal_type)
        prior = self.ledger.get(key)
        result = JournalResult(key=key, date=entry.date,
                               journal_type=entry.journal_type,
                               minted=prior is None)
        result.emotion = self._emotion_for(entry)
        entity = self._entity(entry)

        hub = self.writer.write_parent(
            body=entry.hub_body(),
            entity_id=entity,
            scope=JOURNAL_SCOPE,
            tags=journal_tags(entry.date, entry.journal_type,
                              metrics=entry.metrics, tone=entry.overall_tone,
                              status_tags=entry.status_tags),
            memory_id=prior["hub_lineage"] if prior else None,
            emotion=result.emotion,
            subject_class="companion",
        )
        result.hub_lineage = hub

        known: dict[str, str] = dict(prior["sections"]) if prior else {}
        written: dict[str, str] = {}
        for index, name in enumerate(JOURNAL_SECTIONS):
            body = entry.sections.get(name, "")
            if not body:
                result.sections_skipped += 1
                if name in known:
                    written[name] = known[name]
                continue
            lineage = self.writer.write_chunk(
                body=body, entity_id=entity, scope=JOURNAL_SCOPE,
                parent_lineage=hub, index=index,
                memory_id=known.get(name),
                extra_tags=journal_section_tags(entry.date, entry.journal_type, name),
            )
            written[name] = lineage
            result.sections_written += 1

        self._wikilinks(entry, hub, result, own={hub, *written.values()})

        self.ledger.put(key, date=entry.date, journal_type=entry.journal_type,
                        hub_lineage=hub, sections=written,
                        updated_at=entry.updated_at)
        log.info("journal %s: hub %s, %d sections written, %d skipped, emotion %s",
                 key, hub[:13], result.sections_written, result.sections_skipped,
                 result.emotion)
        return result

    def _wikilinks(self, entry: JournalEntry, hub: str,
                   result: JournalResult, own: set[str] | None = None) -> None:
        'Resolve `[[Topic]]` and act on it. Never fails the entry.'
        links = wl.extract(entry.sections)
        if not links:
            return
        mine = own or {hub}
        for res in wl.resolve(links, lambda topic: [
            h for h in self.writer.search(topic)
            if str(h.get("lineage_id")) not in mine
        ]):
            result.wikilinks.append(res.link.text)
            if res.target_lineage is None or res.target_lineage in mine:
                continue
            try:
                if res.outcome == "linked":
                    self.writer.link_once(hub, res.target_lineage, "relates_to")
                elif res.outcome == "proposed" and self.propose_link is not None:
                    self.propose_link(  # type: ignore[operator]
                        hub, res.target_lineage, res.link.context)
            except Exception as exc:  # noqa: BLE001 - a link must not lose a day
                log.warning("wikilink %r could not be applied: %s",
                            res.link.text, exc)
