import asyncio
import sqlite3

import pytest

from memory_harness import ids, time as mhtime
from memory_harness.db import meta
from memory_harness.db.connection import open_reader, open_writer
from memory_harness.db.writer import WriteQueue


@pytest.fixture
def db_path(tmp_path):  # type: ignore[no-untyped-def]
    return tmp_path / "t.db"


@pytest.fixture
def wq(db_path):  # type: ignore[no-untyped-def]
    con = open_writer(db_path)
    wq = WriteQueue(con)
    yield wq
    wq.close()


async def test_readers_see_committed_write(wq, db_path) -> None:  # type: ignore[no-untyped-def]
    await wq.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    await wq.execute("INSERT INTO t(v) VALUES ('hello')")

    reader = open_reader(db_path)
    rows = reader.execute("SELECT v FROM t").fetchall()
    reader.close()
    assert [r[0] for r in rows] == ["hello"]


async def test_queued_writes_serialize_no_locked(wq) -> None:  # type: ignore[no-untyped-def]
    await wq.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, n INTEGER)")

    async def write_n(n: int) -> None:
        await wq.execute("INSERT INTO t(n) VALUES (?)", (n,))

    await asyncio.gather(*(write_n(n) for n in range(50)))
    rows = await wq.execute("SELECT COUNT(*) FROM t")
    assert rows[0][0] == 50


async def test_foreign_keys_on(wq) -> None:  # type: ignore[no-untyped-def]
    await wq.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    await wq.execute(
        "CREATE TABLE child (id INTEGER PRIMARY KEY, pid INTEGER NOT NULL REFERENCES parent(id))"
    )
    with pytest.raises(sqlite3.IntegrityError):
        await wq.execute("INSERT INTO child(pid) VALUES (999)")


async def test_tx_commit_and_rollback(wq) -> None:  # type: ignore[no-untyped-def]
    await wq.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")

    def good(cur: sqlite3.Cursor) -> int:
        cur.execute("INSERT INTO t(v) VALUES ('a')")
        cur.execute("INSERT INTO t(v) VALUES ('b')")
        return 2

    assert await wq.tx(good) == 2

    def bad(cur: sqlite3.Cursor) -> None:
        cur.execute("INSERT INTO t(v) VALUES ('c')")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await wq.tx(bad)

    rows = await wq.execute("SELECT v FROM t ORDER BY id")
    assert [r[0] for r in rows] == ["a", "b"]


async def test_meta_roundtrip(wq) -> None:  # type: ignore[no-untyped-def]
    await wq.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def txn(cur: sqlite3.Cursor) -> None:
        meta.set(cur, "embed_dim", "1024")
        meta.set(cur, "embed_model_id", "BAAI/bge-m3")

    await wq.tx(txn)

    def read(cur: sqlite3.Cursor) -> tuple[str | None, str | None, str | None]:
        return meta.get(cur, "embed_dim"), meta.get(cur, "embed_model_id"), meta.get(cur, "nope")

    assert await wq.tx(read) == ("1024", "BAAI/bge-m3", None)

    await wq.tx(lambda cur: meta.set(cur, "embed_dim", "512"))
    assert await wq.tx(lambda cur: meta.get(cur, "embed_dim")) == "512"


def test_uuid7_time_sortable() -> None:
    a, b = ids.uuid7(), ids.uuid7()
    assert a < b
    assert len(a) == 36


def test_now_ms() -> None:
    t1 = mhtime.now_ms()
    assert isinstance(t1, int)
    assert mhtime.now_ms() >= t1
    assert mhtime.monotonic_ms() > 0
