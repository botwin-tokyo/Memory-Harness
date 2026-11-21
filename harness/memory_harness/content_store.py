from __future__ import annotations

import hashlib
from pathlib import Path


class ContentStore:
    def __init__(self, blob_dir: str | Path) -> None:
        self._dir = Path(blob_dir)

    def _path(self, ref: str) -> Path:
        return self._dir / ref[:2] / ref

    def put(self, data: bytes) -> str:
        'Store `data`, returning its content hash (the `content_ref`). Idempotent.'
        ref = hashlib.sha256(data).hexdigest()
        path = self._path(ref)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.rename(path)
        return ref

    def get(self, ref: str) -> bytes:
        'Read a blob by its content hash. Raises FileNotFoundError if absent.'
        return self._path(ref).read_bytes()

    def has(self, ref: str) -> bool:
        return self._path(ref).exists()
