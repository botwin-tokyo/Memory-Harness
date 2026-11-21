'Snapping an invented emotion to the real 127-catalog.'

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

import httpx

log = logging.getLogger("memory_ingest.seed.emotions")

DEFAULT_FLOOR = 0.55
FAMILY_BONUS = 0.03


class Embedder(Protocol):
    'Just enough of an embed client to be swapped for a stub in tests.'

    def embed(self, texts: Sequence[str], task: str = ...) -> list[list[float]]: ...


class Chooser(Protocol):
    'Picks catalog names for labels, or None where nothing fits.'

    def choose(
        self, labels: Sequence[str], catalog: Sequence[dict]
    ) -> dict[str, str | None]: ...


@dataclass
class Match:
    name: str
    score: float
    family: str
    method: str = "model"

    @property
    def exact(self) -> bool:
        return self.method == "exact"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.name} ({self.method} {self.score:.2f})"


class NodeEmbedder:

    def __init__(self, base_url: str, api_key: str,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-API-Key": api_key},
            timeout=httpx.Timeout(120.0, connect=15.0),
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def embed(self, texts: Sequence[str],
              task: str = "retrieval.passage") -> list[list[float]]:
        r = self._client.post(
            "/embed",
            json={"texts": list(texts), "task": task, "normalize": True},
        )
        r.raise_for_status()
        return [[float(x) for x in v] for v in r.json()["embeddings"]]


class ModelChooser:
    'The seed model, constrained to the catalog.'

    _LINE = re.compile(r"^\s*[-*]?\s*(?P<label>.+?)\s*=>\s*(?P<name>[A-Za-z /-]+)\s*$")

    def __init__(self, model: object, *, attempts: int = 3,
                 backoff: float = 20.0) -> None:
        self._model = model
        self._attempts = max(1, attempts)
        self._backoff = max(0.0, backoff)

    @staticmethod
    def _listing(catalog: Sequence[dict]) -> str:
        by_family: dict[str, list[str]] = {}
        for e in catalog:
            by_family.setdefault(str(e.get("family", "")), []).append(str(e["name"]))
        return "\n".join(f"{fam}: {', '.join(names)}"
                         for fam, names in by_family.items())

    def prompt_for(self, labels: Sequence[str], catalog: Sequence[dict]) -> str:
        return (
            "Normalise each label to the closest emotion in the catalogue.\n\n"
            f"CATALOGUE (the only permitted answers, grouped by family):\n"
            f"{self._listing(catalog)}\n\n"
            "LABELS:\n" + "\n".join(f"- {label}" for label in labels) + "\n\n"
            "For each label output one line: label => NAME\n"
            "NAME must be copied exactly from the catalogue. If the label is not "
            "an emotion at all (gibberish, a number, an object, a filename), "
            "output label => NONE.\nOutput only those lines."
        )

    def choose(
        self, labels: Sequence[str], catalog: Sequence[dict]
    ) -> dict[str, str | None]:
        wanted = {label.strip().lower(): label for label in labels}
        out: dict[str, str | None] = {label: None for label in labels}
        if not wanted:
            return out
        valid = {str(e["name"]).lower(): str(e["name"]) for e in catalog}

        raw = ""
        prompt = self.prompt_for(labels, catalog)
        for attempt in range(self._attempts):
            try:
                raw = self._model.complete(prompt)  # type: ignore[attr-defined]
                break
            except Exception as exc:  # noqa: BLE001 - any outage degrades alike
                log.warning("emotion chooser attempt %d/%d failed: %s",
                            attempt + 1, self._attempts, exc)
                if attempt + 1 < self._attempts:
                    time.sleep(self._backoff * (attempt + 1))
        if not raw:
            log.error("emotion chooser unavailable — %d label(s) left unresolved",
                      len(labels))
            return out

        for line in raw.splitlines():
            m = self._LINE.match(line)
            if not m:
                continue
            label = wanted.get(m.group("label").strip().strip("\"'").lower())
            if label is None:
                continue
            out[label] = valid.get(m.group("name").strip().lower())
        return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(y * y for y in b)) or 1e-9
    return dot / (na * nb)


def catalog_fingerprint(catalog: Sequence[dict]) -> str:
    payload = "|".join(
        f"{e.get('emotion_id')}:{e.get('name')}:{e.get('description', '')}"
        for e in sorted(catalog, key=lambda e: int(e.get("emotion_id", 0)))
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class EmotionMatcher:
    'Nearest real emotion to whatever the model wrote.'

    def __init__(
        self,
        catalog: Sequence[dict],
        embedder: Embedder,
        *,
        chooser: Chooser | None = None,
        cache_path: str | Path | None = None,
        floor: float = DEFAULT_FLOOR,
    ) -> None:
        self.catalog = list(catalog)
        self.floor = floor
        self.chooser = chooser
        self._embedder = embedder
        self._by_lower = {str(e["name"]).lower(): e for e in self.catalog}
        self._cache_path = Path(cache_path) if cache_path else None
        self._name_vectors: list[list[float]] = []
        self._desc_vectors: list[list[float]] = []
        self.embed_calls = 0
        self._load_or_build()


    def _load_or_build(self) -> None:
        fp = catalog_fingerprint(self.catalog)
        if self._cache_path and self._cache_path.is_file():
            try:
                blob = json.loads(self._cache_path.read_text(encoding="utf-8"))
                names, descs = blob.get("names", []), blob.get("descriptions", [])
                if (blob.get("fingerprint") == fp
                        and len(names) == len(self.catalog)
                        and len(descs) == len(self.catalog)):
                    self._name_vectors = [[float(x) for x in v] for v in names]
                    self._desc_vectors = [[float(x) for x in v] for v in descs]
                    log.info("emotion catalog vectors loaded from cache (%d)",
                             len(self._name_vectors))
                    return
            except (ValueError, OSError, TypeError):
                log.warning("emotion vector cache unreadable — rebuilding")

        names = [str(e["name"]) for e in self.catalog]
        descs = [f"{e['name']}: {e.get('description') or ''}" for e in self.catalog]
        self._name_vectors = self._embedder.embed(names, "retrieval.passage")
        self._desc_vectors = self._embedder.embed(descs, "retrieval.passage")
        self.embed_calls += 1
        if self._cache_path:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"fingerprint": fp, "names": self._name_vectors,
                            "descriptions": self._desc_vectors}),
                encoding="utf-8",
            )
        log.info("embedded %d catalog emotions", len(self._name_vectors))


    def exact(self, name: str) -> Match | None:
        entry = self._by_lower.get(name.strip().lower())
        if entry is None:
            return None
        return Match(str(entry["name"]), 1.0, str(entry.get("family", "")),
                     method="exact")

    def scores(self, name: str, *, family_hint: str | None = None) -> list[float]:
        'Blended similarity of `name` to every catalog entry, in order.'
        probe = self._embedder.embed([name], "retrieval.query")[0]
        self.embed_calls += 1
        out: list[float] = []
        for entry, nvec, dvec in zip(self.catalog, self._name_vectors,
                                     self._desc_vectors):
            by_name, by_desc = cosine(probe, nvec), cosine(probe, dvec)
            score = max(by_name, by_desc) * 0.5 + (by_name + by_desc) * 0.25
            if family_hint and str(entry.get("family", "")) == family_hint:
                score += FAMILY_BONUS
            out.append(score)
        return out

    def is_emotion(self, name: str, *, family_hint: str | None = None) -> float:
        clean = name.strip()
        if not clean:
            return 0.0
        return max(self.scores(clean, family_hint=family_hint), default=0.0)

    def nearest(self, name: str, *, family_hint: str | None = None) -> Match | None:
        'The catalog emotion this word means, or None if it means none.'
        clean = name.strip()
        if not clean:
            return None
        hit = self.exact(clean)
        if hit:
            return hit

        scored = self.scores(clean, family_hint=family_hint)
        best_i = max(range(len(scored)), key=scored.__getitem__) if scored else None
        if best_i is None or scored[best_i] < self.floor:
            return None

        if self.chooser is not None:
            picked = self.chooser.choose([clean], self.catalog).get(clean)
            entry = self._by_lower.get((picked or "").lower())
            if entry is None:
                return None
            return Match(str(entry["name"]), scored[best_i],
                         str(entry.get("family", "")), method="model")

        entry = self.catalog[best_i]
        return Match(str(entry["name"]), scored[best_i],
                     str(entry.get("family", "")), method="vector")

    def resolve_many(
        self, names: Sequence[str], *, family_hints: dict[str, str] | None = None
    ) -> dict[str, Match | None]:
        "Batch form: one chooser call for a whole chapter's strays."
        hints = family_hints or {}
        out: dict[str, Match | None] = {}
        pending: list[str] = []
        gate: dict[str, tuple[int, float]] = {}

        for raw in names:
            clean = raw.strip()
            if not clean or clean in out or clean in gate:
                continue
            hit = self.exact(clean)
            if hit:
                out[clean] = hit
                continue
            scored = self.scores(clean, family_hint=hints.get(clean))
            best_i = max(range(len(scored)), key=scored.__getitem__)
            if scored[best_i] < self.floor:
                out[clean] = None
                continue
            gate[clean] = (best_i, scored[best_i])
            pending.append(clean)

        if not pending:
            return out

        if self.chooser is None:
            for label in pending:
                idx, score = gate[label]
                entry = self.catalog[idx]
                out[label] = Match(str(entry["name"]), score,
                                   str(entry.get("family", "")), method="vector")
            return out

        picks = self.chooser.choose(pending, self.catalog)
        for label in pending:
            chosen = self._by_lower.get((picks.get(label) or "").lower())
            out[label] = None if chosen is None else Match(
                str(chosen["name"]), gate[label][1],
                str(chosen.get("family", "")), method="model")
        return out


def build_matcher(
    catalog: Sequence[dict],
    *,
    base_url: str,
    api_key: str,
    chooser: Chooser | None = None,
    cache_path: str | Path | None = None,
    floor: float = DEFAULT_FLOOR,
) -> EmotionMatcher:
    'The production wiring: the real embed node + an on-disk vector cache.'
    return EmotionMatcher(
        catalog,
        NodeEmbedder(base_url, api_key),
        chooser=chooser,
        cache_path=cache_path,
        floor=floor,
    )
