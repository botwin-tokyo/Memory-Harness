'`guid` → the lineages we wrote, so a re-send revises in place.'

from __future__ import annotations

import json

from memory_ingest.state import StateDB


class BlogLedger:
    def __init__(self, path: str) -> None:
        self._db = StateDB(path)
        self._db._con.execute(
            "CREATE TABLE IF NOT EXISTS blog_posts ("
            " guid TEXT PRIMARY KEY,"
            " date TEXT NOT NULL,"
            " url TEXT NOT NULL DEFAULT '',"
            " title TEXT NOT NULL DEFAULT '',"
            " hub_lineage TEXT NOT NULL,"
            " artifact_lineage TEXT NOT NULL,"
            " chunk_lineages TEXT NOT NULL DEFAULT '[]',"
            " content_hash TEXT NOT NULL DEFAULT '',"
            " updated_at TEXT NOT NULL DEFAULT '',"
            " same_day TEXT NOT NULL DEFAULT '')"
        )
        self._db._con.commit()

    def close(self) -> None:
        self._db.close()

    def get(self, guid: str) -> dict | None:
        row = self._db._con.execute(
            "SELECT hub_lineage, artifact_lineage, chunk_lineages, content_hash,"
            " same_day FROM blog_posts WHERE guid=?", (guid,)).fetchone()
        if row is None:
            return None
        return {
            "hub_lineage": row[0],
            "artifact_lineage": row[1],
            "chunk_lineages": json.loads(row[2]),
            "content_hash": row[3],
            "same_day": row[4],
        }

    def put(self, guid: str, *, date: str, url: str, title: str,
            hub_lineage: str, artifact_lineage: str,
            chunk_lineages: list[str], content_hash: str,
            updated_at: str = "", same_day: str = "") -> None:
        self._db._con.execute(
            "INSERT INTO blog_posts(guid, date, url, title, hub_lineage,"
            " artifact_lineage, chunk_lineages, content_hash, updated_at, same_day)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(guid) DO UPDATE SET"
            "  date=excluded.date, url=excluded.url, title=excluded.title,"
            "  hub_lineage=excluded.hub_lineage,"
            "  artifact_lineage=excluded.artifact_lineage,"
            "  chunk_lineages=excluded.chunk_lineages,"
            "  content_hash=excluded.content_hash,"
            "  updated_at=excluded.updated_at,"
            "  same_day=CASE WHEN excluded.same_day != '' THEN excluded.same_day"
            "               ELSE blog_posts.same_day END",
            (guid, date, url, title, hub_lineage, artifact_lineage,
             json.dumps(chunk_lineages), content_hash, updated_at, same_day))
        self._db._con.commit()

    def entries(self) -> list[dict]:
        return [{"guid": r[0], "date": r[1], "url": r[2], "title": r[3],
                 "hub_lineage": r[4], "artifact_lineage": r[5],
                 "chunks": len(json.loads(r[6])), "content_hash": r[7],
                 "updated_at": r[8], "same_day": r[9]}
                for r in self._db._con.execute(
                    "SELECT guid, date, url, title, hub_lineage, artifact_lineage,"
                    " chunk_lineages, content_hash, updated_at, same_day"
                    " FROM blog_posts ORDER BY date DESC, guid")]
