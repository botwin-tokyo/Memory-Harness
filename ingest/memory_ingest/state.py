from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class StateDB:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS sources ("
            " source_ref TEXT PRIMARY KEY,"
            " text_sha256 TEXT NOT NULL,"
            " entity_id TEXT NOT NULL,"
            " parent_lineage TEXT NOT NULL,"
            " chunk_lineages TEXT NOT NULL)"
        )
        self._con.commit()

    def close(self) -> None:
        self._con.close()

    def get(self, source_ref: str) -> dict | None:
        row = self._con.execute(
            "SELECT text_sha256, entity_id, parent_lineage, chunk_lineages"
            " FROM sources WHERE source_ref=?",
            (source_ref,),
        ).fetchone()
        if row is None:
            return None
        return {
            "text_sha256": row[0],
            "entity_id": row[1],
            "parent_lineage": row[2],
            "chunk_lineages": json.loads(row[3]),
        }

    def put(
        self,
        source_ref: str,
        *,
        text_sha256: str,
        entity_id: str,
        parent_lineage: str,
        chunk_lineages: list[str],
    ) -> None:
        self._con.execute(
            "INSERT INTO sources(source_ref, text_sha256, entity_id, parent_lineage,"
            " chunk_lineages) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(source_ref) DO UPDATE SET text_sha256=excluded.text_sha256,"
            " entity_id=excluded.entity_id, parent_lineage=excluded.parent_lineage,"
            " chunk_lineages=excluded.chunk_lineages",
            (source_ref, text_sha256, entity_id, parent_lineage, json.dumps(chunk_lineages)),
        )
        self._con.commit()


class GitState(StateDB):

    def __init__(self, path: str) -> None:
        super().__init__(path)
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS git_summaries ("
            " repo TEXT NOT NULL, path TEXT NOT NULL, sha TEXT NOT NULL,"
            " summary TEXT NOT NULL, PRIMARY KEY (repo, path, sha))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS git_repos ("
            " repo TEXT PRIMARY KEY, head_sha TEXT NOT NULL,"
            " index_revision INTEGER NOT NULL, repo_lineage TEXT)"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS git_commits ("
            " repo TEXT NOT NULL, sha TEXT NOT NULL, lineage TEXT NOT NULL,"
            " PRIMARY KEY (repo, sha))"
        )
        self._con.execute(
            "CREATE TABLE IF NOT EXISTS git_files ("
            " repo TEXT NOT NULL, path TEXT NOT NULL,"
            " content_sha TEXT NOT NULL, parent_lineage TEXT NOT NULL,"
            " chunk_lineages TEXT NOT NULL, PRIMARY KEY (repo, path))"
        )
        self._con.commit()


    def summary_get(self, repo: str, path: str, sha: str) -> str | None:
        row = self._con.execute(
            "SELECT summary FROM git_summaries WHERE repo=? AND path=? AND sha=?",
            (repo, path, sha),
        ).fetchone()
        return None if row is None else str(row[0])

    def summary_put(self, repo: str, path: str, sha: str, summary: str) -> None:
        self._con.execute(
            "INSERT INTO git_summaries(repo, path, sha, summary) VALUES (?,?,?,?)"
            " ON CONFLICT(repo, path, sha) DO UPDATE SET summary=excluded.summary",
            (repo, path, sha, summary),
        )
        self._con.commit()


    def repo_get(self, repo: str) -> dict | None:
        row = self._con.execute(
            "SELECT head_sha, index_revision, repo_lineage FROM git_repos WHERE repo=?",
            (repo,),
        ).fetchone()
        if row is None:
            return None
        return {"head_sha": row[0], "index_revision": row[1], "repo_lineage": row[2]}

    def repo_put(
        self, repo: str, *, head_sha: str, index_revision: int,
        repo_lineage: str | None,
    ) -> None:
        self._con.execute(
            "INSERT INTO git_repos(repo, head_sha, index_revision, repo_lineage)"
            " VALUES (?,?,?,?) ON CONFLICT(repo) DO UPDATE SET"
            " head_sha=excluded.head_sha, index_revision=excluded.index_revision,"
            " repo_lineage=excluded.repo_lineage",
            (repo, head_sha, index_revision, repo_lineage),
        )
        self._con.commit()


    def git_file_get(self, repo: str, path: str) -> dict | None:
        row = self._con.execute(
            "SELECT content_sha, parent_lineage, chunk_lineages FROM git_files"
            " WHERE repo=? AND path=?",
            (repo, path),
        ).fetchone()
        if row is None:
            return None
        return {"content_sha": row[0], "parent_lineage": row[1],
                "chunk_lineages": json.loads(row[2])}

    def git_file_put(
        self, repo: str, path: str, *, content_sha: str,
        parent_lineage: str, chunk_lineages: list[str],
    ) -> None:
        self._con.execute(
            "INSERT INTO git_files(repo, path, content_sha, parent_lineage,"
            " chunk_lineages) VALUES (?,?,?,?,?)"
            " ON CONFLICT(repo, path) DO UPDATE SET content_sha=excluded.content_sha,"
            " parent_lineage=excluded.parent_lineage,"
            " chunk_lineages=excluded.chunk_lineages",
            (repo, path, content_sha, parent_lineage, json.dumps(chunk_lineages)),
        )
        self._con.commit()

    def git_file_paths(self, repo: str) -> list[str]:
        return [str(r[0]) for r in self._con.execute(
            "SELECT path FROM git_files WHERE repo=?", (repo,)
        )]

    def git_file_delete(self, repo: str, path: str) -> None:
        self._con.execute(
            "DELETE FROM git_files WHERE repo=? AND path=?", (repo, path)
        )
        self._con.commit()


    def commit_get(self, repo: str, sha: str) -> str | None:
        row = self._con.execute(
            "SELECT lineage FROM git_commits WHERE repo=? AND sha=?", (repo, sha)
        ).fetchone()
        return None if row is None else str(row[0])

    def commit_put(self, repo: str, sha: str, lineage: str) -> None:
        self._con.execute(
            "INSERT INTO git_commits(repo, sha, lineage) VALUES (?,?,?)"
            " ON CONFLICT(repo, sha) DO UPDATE SET lineage=excluded.lineage",
            (repo, sha, lineage),
        )
        self._con.commit()
