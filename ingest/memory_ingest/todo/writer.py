'Todo mirroring: a day snapshot → its subtree. Idempotent; a re-send REVISES.'

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from memory_contract import (
    SAME_DAY_LINK,
    todo_day_key,
    todo_tags,
    todo_item_tags,
)

from memory_ingest.journal import wikilinks as wl
from memory_ingest.state import StateDB
from memory_ingest.todo.model import TodoDay
from memory_ingest.writer import HarnessWriter

log = logging.getLogger("memory_ingest.todo.writer")

TODO_SCOPE = "shared"


@dataclass
class TodoResult:
    key: str
    date: str
    hub_lineage: str = ""
    minted: bool = False
    items_written: int = 0
    items_retired: int = 0
    journal_linked: bool = False
    links_made: int = 0
    tombstoned: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key, "date": self.date, "hub": self.hub_lineage,
            "minted": self.minted, "items_written": self.items_written,
            "items_retired": self.items_retired,
            "journal_linked": self.journal_linked,
            "links_made": self.links_made,
            "tombstoned": self.tombstoned,
        }


class TodoLedger:
    '`todo_day:{date}` → the lineages we wrote, so a re-send revises in place.'

    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS todo_days ("
            " key TEXT PRIMARY KEY, date TEXT NOT NULL,"
            " hub_lineage TEXT NOT NULL,"
            " item_lineages TEXT NOT NULL DEFAULT '{}',"
            " updated_at TEXT NOT NULL DEFAULT '')"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    def get(self, key: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT hub_lineage, item_lineages FROM todo_days WHERE key=?",
            (key,)).fetchone()
        if row is None:
            return None
        return {"hub_lineage": row[0], "items": json.loads(row[1])}

    def put(self, key: str, *, date: str, hub_lineage: str,
            items: dict[str, str], updated_at: str = "") -> None:
        self._db._con.execute(
            "INSERT INTO todo_days(key, date, hub_lineage, item_lineages,"
            " updated_at) VALUES (?,?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET hub_lineage=excluded.hub_lineage,"
            " item_lineages=excluded.item_lineages,"
            " updated_at=excluded.updated_at",
            (key, date, hub_lineage, json.dumps(items), updated_at))
        self._db._con.commit()

    def delete(self, key: str) -> None:
        self._db._con.execute("DELETE FROM todo_days WHERE key=?", (key,))
        self._db._con.commit()

    def entries(self) -> list[dict]:
        return [{"key": r[0], "date": r[1], "hub_lineage": r[2],
                 "updated_at": r[3]}
                for r in self._db._con.execute(
                    "SELECT key, date, hub_lineage, updated_at"
                    " FROM todo_days ORDER BY date DESC")]


class TodoWriter:
    "Mints or revises one day's hub and its item nodes."

    def __init__(self, writer: HarnessWriter, ledger: TodoLedger,
                 propose_link: object = None) -> None:
        self.writer = writer
        self.ledger = ledger
        self.propose_link = propose_link

    def _entity(self, day: TodoDay) -> str:
        'One collection entity per DAY, so a day is walkable as a unit.'
        slug = f"todo-{day.date}"
        return self.writer.register_entity(
            "collection", f"Todo {day.date}", slug,
            f"todo list: {day.date}", aliases=[slug],
        )

    def write(self, day: TodoDay) -> TodoResult:
        key = todo_day_key(day.date)
        prior = self.ledger.get(key)

        if day.deleted:
            return self._tombstone(key, day, prior)

        result = TodoResult(key=key, date=day.date, minted=prior is None)
        entity = self._entity(day)
        hub = self.writer.write_parent(
            body=day.hub_body(),
            entity_id=entity,
            scope=TODO_SCOPE,
            tags=todo_tags(day.date, done=day.done_count, total=len(day.items)),
            memory_id=prior["hub_lineage"] if prior else None,
            subject_class="companion",
        )
        result.hub_lineage = hub

        known: dict[str, str] = dict(prior["items"]) if prior else {}
        written: dict[str, str] = {}
        for item in day.ordered:
            state = f"completed {item.completed_at}" if item.completed else "open"
            lineage = self.writer.write_leaf(
                body=f"[todo-item] {item.text}\n{state}",
                entity_id=entity, scope=TODO_SCOPE,
                tags=todo_item_tags(day.date, completed=item.completed,
                                    position=item.position),
                parent_lineage=hub,
                memory_id=known.get(item.id),
                subject_class="companion",
            )
            written[item.id] = lineage
            result.items_written += 1

        for gone in set(known) - {i.id for i in day.items}:
            try:
                self.writer.forget(known[gone],
                                   "removed from todo day snapshot")
                result.items_retired += 1
            except Exception as exc:  # noqa: BLE001 - a retire must not lose a day
                log.warning("could not retire %s: %s", known[gone][:13], exc)
                written[gone] = known[gone]

        result.journal_linked = self._link_same_day(hub, day.date)
        result.links_made = self._match_items(day, written,
                                              own={hub, *written.values()})

        self.ledger.put(key, date=day.date, hub_lineage=hub, items=written,
                        updated_at=day.updated_at)
        log.info("todo %s: hub %s, %d items, %d retired, journal_linked=%s",
                 key, hub[:13], result.items_written, result.items_retired,
                 result.journal_linked)
        return result

    def _tombstone(self, key: str, day: TodoDay, prior: dict | None) -> TodoResult:
        'The day was deleted at the source. Retire everything it made.'
        result = TodoResult(key=key, date=day.date, tombstoned=True,
                            hub_lineage=prior["hub_lineage"] if prior else "")
        if prior is None:
            return result
        for lineage in [prior["hub_lineage"], *prior["items"].values()]:
            try:
                self.writer.forget(lineage, f"todo day {day.date} deleted")
            except Exception as exc:  # noqa: BLE001
                log.warning("tombstone could not retire %s: %s",
                            lineage[:13], exc)
        self.ledger.delete(key)
        log.info("todo %s: tombstoned", key)
        return result

    def _link_same_day(self, hub: str, date: str) -> bool:
        "todo hub → that day's journal hub. Never fails the day."
        try:
            journal = self.writer.find_hub_by_date("journal", date)
            if not journal or journal == hub:
                return False
            self.writer.link_once(hub, journal, SAME_DAY_LINK)
            return True
        except Exception as exc:  # noqa: BLE001 - a link must not lose a day
            log.warning("same_day link for %s failed: %s", date, exc)
            return False

    def _match_items(self, day: TodoDay, items: dict[str, str],
                     own: set[str]) -> int:
        'Item text → edges, two channels, both evidence rather than guesses.'
        made = self._match_wikilinks(day, items, own)
        made += self._match_names(day, items, own)
        return made

    def _match_wikilinks(self, day: TodoDay, items: dict[str, str],
                         own: set[str]) -> int:
        links = wl.extract({i.id: i.text for i in day.items})
        if not links:
            return 0
        made = 0
        for res in wl.resolve(links, lambda topic: [
            h for h in self.writer.search(topic)
            if str(h.get("lineage_id")) not in own
        ]):
            src = items.get(res.link.section)
            if src is None or res.target_lineage is None:
                continue
            try:
                if res.outcome == "linked":
                    self.writer.link_once(src, res.target_lineage, "relates_to")
                    made += 1
                elif res.outcome == "proposed" and self.propose_link is not None:
                    self.propose_link(  # type: ignore[operator]
                        src, res.target_lineage, res.link.context)
            except Exception as exc:  # noqa: BLE001 - a link must not lose a day
                log.warning("todo wikilink %r could not be applied: %s",
                            res.link.text, exc)
        return made

    def _match_names(self, day: TodoDay, items: dict[str, str],
                     own: set[str]) -> int:
        try:
            hubs = [h for h in self.writer.named_hubs()
                    if h["lineage_id"] not in own and len(h["name"]) >= 3]
        except Exception as exc:  # noqa: BLE001 - matching must not lose a day
            log.warning("named-hub lookup failed for %s: %s", day.date, exc)
            return 0
        if not hubs:
            return 0
        made = 0
        for item in day.items:
            src = items.get(item.id)
            if src is None or "[[" in item.text:
                continue
            hits = {h["lineage_id"] for h in hubs if _names(item.text, h["name"])}
            if len(hits) != 1:
                if len(hits) > 1:
                    log.info("todo item %r names several things; not guessing",
                             item.text[:60])
                continue
            try:
                if self.writer.link_once(src, hits.pop(), "relates_to"):
                    made += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("todo name link could not be applied: %s", exc)
        return made


def _names(text: str, name: str) -> bool:
    '`text` mentions `name`, on word boundaries.'
    import re

    sensitive = len(name) < 5 and " " not in name
    pat = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)",
                     0 if sensitive else re.IGNORECASE)
    return pat.search(text) is not None
