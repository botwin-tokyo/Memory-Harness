'Dataclasses: Principal, Fact, Event, CorePack... Steps 1.4+.'

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    "The caller's identity, derived server-side from the API key's policy row."

    principal_key_id: str
    principal_id: str
    node: str
    allowed_scopes: list[str]
    trust_ceiling: str
    home_scope: str
    learning_eligible_default: bool = True
    secret_mode: str = "store-but-dont-train"
    link_authority: str = "propose"
    private_authority: bool = False
    read_only: bool = False


MEMORY_TYPES = ("fact", "preference", "episode", "note", "procedure", "credential",
                "email", "contact")


@dataclass(frozen=True)
class MemoryWrite:

    type: str
    body: str
    scope: str
    emotion_id: int
    source_trust: str
    created_by_agent: str
    created_on_node: str
    learning_eligible: bool = True
    lineage_id: str | None = None
    about: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    confidence: float | None = None
    valid_from: int | None = None
    evidence_refs: list[str] = field(default_factory=list)
    subject_class: str | None = None
    subject: str | None = None
    subject_ref: str | None = None
    predicate: str | None = None
    object_json: str | None = None
    fact_type: str | None = None
    owner_id: str | None = None
    category: str | None = None
    item: str | None = None
    strength: str | None = None
    explicitness: str | None = None
    episode_type: str | None = None
    name: str | None = None


@dataclass(frozen=True)
class MemoryResult:
    'Outcome of write_memory: the row written (or the dispute collapsed onto).'

    row_id: str
    lineage_id: str
    revision: int
    is_head: bool
    status: str
    disputed: bool
    superseded_id: str | None

    @property
    def ref(self) -> str:
        'Display form: lineage_id.revision (e.g. mem_7f3a.3).'
        return f"{self.lineage_id}.{self.revision}"
