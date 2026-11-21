'Pydantic request/response models (the API routes). Steps 1.3+.'

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class EmotionOut(BaseModel):
    'One row of the 127-emotion reference vocabulary (D50).'

    emotion_id: int
    name: str
    family: str
    description: str


class WhoAmIOut(BaseModel):

    principal_id: str
    node: str
    trust_ceiling: str
    allowed_scopes: list[str]
    home_scope: str
    learning_eligible_default: bool
    secret_mode: str
    link_authority: str


class EventIn(BaseModel):

    content: str | None = None
    content_ref: str | None = None
    event_type: str
    about: list[str] = []
    tags: list[str] = []
    scope: str | None = None
    source_class: Literal["self", "tool_output", "external"] = "self"
    salience_hint: Literal["low", "high"] | None = None
    emotion: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    idempotency_key: str


class EventOut(BaseModel):
    event_id: str
    deduped: bool
    embedding: Literal["queued", "done", "unavailable"]
    redacted: bool


class RememberIn(BaseModel):

    type: str
    body: str
    emotion: str | None = None
    scope: str | None = None
    about: list[str] = []
    tags: list[str] = []
    memory_id: str | None = None
    source_class: Literal["self", "tool_output", "external"] = "self"
    confidence: float | None = None
    valid_from: int | None = None
    evidence_refs: list[str] = []
    subject_class: Literal["self", "companion", "person", "world"] | None = None
    subject: str | None = None
    category: str | None = None
    item: str | None = None


class RememberOut(BaseModel):
    lineage_id: str
    revision: int
    ref: str
    superseded: str | None


class CoreIn(BaseModel):

    op: Literal["append", "replace", "remove"]
    slot_key: str
    value: str | None = None
    priority: int = 100
    pinned: bool = False
    owner_scope: str | None = None
    subject_class: Literal["self", "companion", "person", "world"] | None = None
    about: list[str] = []
    tags: list[str] = []


class CoreSlotOut(BaseModel):
    slot_id: str
    slot_key: str
    owner_scope: str
    value_text: str
    priority: int
    pinned: bool
    token_estimate: int


class CoreOut(BaseModel):
    slot: CoreSlotOut | None
    demoted: list[str] = []


class RecallIn(BaseModel):

    query: str
    token_budget: int = 8000
    scope_filter: list[str] | None = None
    type_filter: list[str] | None = None
    since: int | None = None
    debug: bool = False
    include_links: bool = True


class LinkRef(BaseModel):

    link_type: str
    target_lineage_id: str
    gloss: str
    proposed: bool = False


class RecallItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    kind: str
    id: str
    lineage_id: str
    revision: int
    text: str
    confidence_band: Literal["high", "medium", "low"]
    source_trust: str
    scope: str
    provenance: list[str]
    usage: Literal["safe_to_act", "confirm_first", "answer_only"]
    disputed: bool
    links_out: list[LinkRef] = []
    relevance_band: str = "unranked"
    subject_class: str = Field("world", serialization_alias="subjectClass")
    perspective: str = "neutral"


class IdentityVoice(BaseModel):

    display: str
    pronouns: str | None
    perspective: str


class RecallOut(BaseModel):
    goal: str | None
    core: list[RecallItem]
    preferences: list[RecallItem]
    facts: list[RecallItem]
    episodes: list[RecallItem]
    procedures: list[RecallItem] = []
    identities: dict[str, IdentityVoice] = {}
    open_questions: list[str]
    token_estimate: int
    debug: dict[str, object] | None = None


class ChangeItem(BaseModel):
    'One feed item: the event envelope + the resolved current record state.'

    seq: int
    event_id: str
    event_type: str
    recorded_at: int
    scope: str
    source_trust: str
    created_by_agent: str
    created_on_node: str
    learning_eligible: bool
    redacted: bool
    emotion_id: int
    content: str | None
    summary: str | None
    about: list[str]
    tags: list[str]
    target_type: str | None
    target_id: str | None
    record: dict[str, object] | None


class Tombstone(BaseModel):
    target_type: str | None
    target_id: str | None


class ChangesOut(BaseModel):
    items: list[ChangeItem]
    tombstones: list[Tombstone]
    next_cursor: int


class LibrarySearchIn(BaseModel):

    query: str
    book: str | None = None
    limit: int = 8


class ForgetIn(BaseModel):

    id: str | None = None
    selector: dict[str, str] | None = None
    reason: str


class ForgetOut(BaseModel):
    retracted: int


class LinkIn(BaseModel):

    src_lineage_id: str | None = None
    dst_lineage_id: str | None = None
    link_type: str | None = None
    weight: float = 1.0
    link_id: str | None = None
    action: Literal["promote", "reject", "retract"] | None = None


class LinkOut(BaseModel):
    link_id: str
    link_type: str
    target: str
    status: str


class ExploreIn(BaseModel):

    start_id: str
    depth: int = 3
    link_types: list[str] | None = None
    budget: int = 2000
    direction: Literal["out", "in", "both"] = "both"


class ExploreItem(BaseModel):
    kind: str
    id: str
    lineage_id: str
    revision: int
    hop: int
    text: str
    source_trust: str
    scope: str
    usage: str
    links_out: list[LinkRef] = []


class ExploreOut(BaseModel):
    start_id: str
    depth: int
    items: list[ExploreItem]
    token_estimate: int


class BranchIn(BaseModel):

    link_type: str
    target: str
    direction: Literal["out", "in"] = "out"


class IntakeIn(BaseModel):

    type: str
    body: str
    emotion: str | None = None
    scope: str | None = None
    about: list[str] = []
    tags: list[str] = []
    memory_id: str | None = None
    source_class: Literal["self", "tool_output", "external"] = "self"
    confidence: float | None = None
    valid_from: int | None = None
    subject_class: Literal["self", "companion", "person", "world"] | None = None
    subject: str | None = None
    category: str | None = None
    item: str | None = None
    learning_eligible: bool | None = None
    branches: list[BranchIn] = []


class IntakeOut(BaseModel):
    lineage_id: str
    revision: int
    ref: str
    superseded: str | None
    links: list[dict[str, str]]


class ConflictFactOut(BaseModel):
    fact_id: str
    lineage_id: str
    gloss: str
    object_json: str
    confidence: float
    valid_from: int
    valid_to: int | None
    created_by_agent: str


class ConflictGroupOut(BaseModel):

    subject_ref: str
    predicate: str
    scope: str
    source_trust: str
    facts: list[ConflictFactOut]


class DisputeQueueOut(BaseModel):
    groups: list[ConflictGroupOut]


class ResolveIn(BaseModel):

    winner_fact_id: str
    loser_fact_id: str


class ResolveOut(BaseModel):
    winner_fact_id: str
    loser_fact_id: str
    winner_status: str
    loser_status: str


class GraphNodeOut(BaseModel):

    model_config = ConfigDict(populate_by_name=True)

    id: str
    lineage_id: str = Field(alias="lineageId")
    is_head: bool = Field(alias="isHead")
    type: str
    kind: str = "memory"
    emotion_id: int = Field(alias="emotionId")
    scope: str
    source_trust: str = Field(alias="sourceTrust")
    status: str
    confidence: float
    subject_class: str = Field("world", alias="subjectClass")

    revision: int = 0
    emotion_family: str = Field("", alias="emotionFamily")
    emotion_name: str = Field("", alias="emotionName")
    about: list[str] = []
    tags: list[str] = []
    body_preview: str = Field("", alias="bodyPreview")
    created_by_agent: str = Field("", alias="createdByAgent")
    created_on_node: str = Field("", alias="createdOnNode")
    updated_at: int = Field(0, alias="updatedAt")


class GraphLinkOut(BaseModel):
    id: str
    source: str
    target: str
    link_type: str = Field(alias="linkType")
    status: str
    weight: float


class AskIn(BaseModel):

    text: str
    token_budget: int = 8000
    client_ask_id: str | None = None


class AskOut(BaseModel):
    answer: str
    mood: str
    mood_family: str = Field(serialization_alias="moodFamily")
    sources: list[str] = []
    tools_used: list[str] = Field(default_factory=list, serialization_alias="toolsUsed")

    model_config = ConfigDict(populate_by_name=True)


class GraphNodeMinimal(BaseModel):

    model_config = ConfigDict(populate_by_name=True)

    id: str
    lineage_id: str = Field(alias="lineageId")
    is_head: bool = Field(alias="isHead")
    type: str
    kind: str
    emotion_id: int = Field(alias="emotionId")
    scope: str
    source_trust: str = Field(alias="sourceTrust")
    status: str
    confidence: float
    subject_class: str = Field(alias="subjectClass")


class GraphSnapshotOut(BaseModel):
    nodes: list[GraphNodeOut]
    links: list[GraphLinkOut]
    nodes_total: int


class GraphSnapshotMinimalOut(BaseModel):
    nodes: list[GraphNodeMinimal]
    links: list[GraphLinkOut]
    nodes_total: int
    cursor: int = 0


class GraphDeltaOut(BaseModel):

    nodes: list[GraphNodeMinimal]
    links: list[GraphLinkOut]
    removed: list[str]
    cursor: int
    full_resync: bool = False


class RevisionOut(BaseModel):

    id: str
    revision: int
    status: str
    is_head: bool
    scope: str
    source_trust: str
    confidence: float
    emotion_id: int
    text: str
    valid_from: int | None
    valid_to: int | None
    recorded_at: int | None
    created_by_agent: str


class HistoryOut(BaseModel):
    lineage_id: str
    type: str
    revisions: list[RevisionOut]


class SearchIn(BaseModel):

    query: str
    types: list[str] | None = None
    tags_all: list[str] | None = None
    tags_any: list[str] | None = None
    limit: int = 20


class SearchHit(BaseModel):
    lineage_id: str
    type: str
    score: float
    text_excerpt: str
    tags: list[str]


class SearchOut(BaseModel):
    query: str
    results: list[SearchHit]
    count: int


class TypesOut(BaseModel):

    types: list[str]


class EntityIn(BaseModel):

    kind: Literal[
        "person", "module", "repo", "document", "manual", "collection", "topic", "artifact"
    ]
    name: str
    slug: str | None = None
    description: str | None = None
    pronouns: str | None = None
    aliases: list[str] = []


class EntityOut(BaseModel):
    id: str
    display: str
    subject_class: str


class SourceIn(BaseModel):

    target: str
    query: str | None = None
    offset: int = 0
    limit: int | None = None
    window: int = 1
    token_budget: int = 1500


class SourceCitation(BaseModel):
    title: str
    ref: str
    sha256: str
    kind: str
    route: str
    artifact_lineage: str


class SourceChunk(BaseModel):
    index: int
    text: str
    relevance: float | None = None


class SourceOut(BaseModel):
    source: SourceCitation
    grounded_memory: str | None = None
    mode: Literal["navigation", "search"]
    chunks: list[SourceChunk]
    total_chunks: int
    returned: int
    truncated: bool
    token_estimate: int
    note: str | None = None
