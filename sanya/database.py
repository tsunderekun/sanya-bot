import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from .repository import Repository
from .schema import initialize


class Database:
    """One connection, owned by one thread; each repository call runs in full there."""

    def __init__(self, path: str | Path):
        self.path = path
        self._worker = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sanya-sqlite"
        )

    def _open(self):
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            journal = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if journal.lower() != "wal":
                raise RuntimeError(f"SQLite journal mode is {journal}, expected WAL")
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                initialize(conn)
        except BaseException:
            conn.close()
            raise
        self._repo = Repository(conn)

    async def __aenter__(self):
        try:
            await self._submit(self._open)
        except BaseException:
            await self.__aexit__(None, None, None)
            raise
        return self

    async def __aexit__(self, *_):
        def close():
            if hasattr(self, "_repo"):
                self._repo.connection.close()

        try:
            await self._submit(close)
        finally:
            await asyncio.to_thread(self._worker.shutdown)

    async def _submit(self, operation):
        return await asyncio.get_running_loop().run_in_executor(self._worker, operation)

    async def run(self, method, /, *args, **kwargs):
        return await self._submit(partial(method, self._repo, *args, **kwargs))
