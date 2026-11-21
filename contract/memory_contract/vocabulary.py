from __future__ import annotations

from typing import Literal

SubjectClass = Literal["self", "companion", "person", "world"]
SUBJECT_CLASSES: tuple[str, ...] = ("self", "companion", "person", "world")

MemoryType = Literal[
    "fact", "preference", "episode", "note", "procedure", "credential", "email", "contact"
]
MEMORY_TYPES: tuple[str, ...] = (
    "fact", "preference", "episode", "note", "procedure", "credential", "email", "contact",
)

LINK_TYPES: tuple[str, ...] = (
    "elaborates", "relates_to", "example_of", "part_of", "caused_by",
    "detailed_by", "contradicts", "follows", "modifies", "knows", "same_day",
    "in_reply_to",
)
GROUNDS_LINK = "detailed_by"
PART_OF_LINK = "part_of"
KNOWS_LINK = "knows"

ARTIFACT_TAG = "artifact"
CHUNK_TAG = "artifact-chunk"
REPO_TAG = "repo"
REPO_INDEX_TAG = "repo-index"
GIT_FILE_TAG = "git-file"
COMMIT_TAG = "commit"
ARTIFACT_TAGS: tuple[str, ...] = (
    ARTIFACT_TAG, CHUNK_TAG, REPO_INDEX_TAG, GIT_FILE_TAG,
)

PREFERENCE_DEFAULT_CATEGORY = "general"
PREFERENCE_DEFAULT_ITEM = "general"


def slugify_item(text: str, *, max_len: int = 48) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:max_len] or PREFERENCE_DEFAULT_ITEM


CONVERSATION_TAG = "conversation"
TURN_TAG = "turn"

ConversationSource = Literal["hermes", "openclaw"]
CONVERSATION_SOURCES: tuple[str, ...] = ("hermes", "openclaw")

TurnRole = Literal["user", "assistant", "tool", "system"]
TURN_ROLES: tuple[str, ...] = ("user", "assistant", "tool", "system")


def conversation_tags(
    source: str, session_id: str, *, agent: str | None = None,
    project: str | None = None,
) -> list[str]:
    "The hub's tags. One place, so the writer and the MCP reader agree."
    tags = [CONVERSATION_TAG, f"source:{source}", f"session:{session_id}"]
    if agent:
        tags.append(f"agent:{agent}")
    if project:
        tags.append(f"project:{project}")
    return tags


def turn_tags(role: str, index: int) -> list[str]:
    return [TURN_TAG, CHUNK_TAG, f"role:{role}", f"turn:{index}"]


JOURNAL_TAG = "journal"
JOURNAL_SECTION_TAG = "journal-section"

JournalType = Literal["morning", "evening", "full-day"]
JOURNAL_TYPES: tuple[str, ...] = ("morning", "evening", "full-day")

JOURNAL_SECTIONS: tuple[str, ...] = (
    "daily_snapshot", "intent_focus", "risks_friction", "actions_commitments",
    "wins_progress", "reflection", "emotional_context", "tomorrow_setup",
)

JOURNAL_METRICS: tuple[str, ...] = ("mood", "energy", "sleep", "stress")


def journal_lineage_key(date: str, journal_type: str) -> str:
    return f"{JOURNAL_TAG}:{date}:{journal_type}"


def journal_tags(date: str, journal_type: str, *,
                 metrics: dict[str, int] | None = None,
                 tone: str | None = None,
                 status_tags: list[str] | None = None) -> list[str]:
    "The hub's tags."
    tags = [JOURNAL_TAG, f"date:{date}", f"type:{journal_type}"]
    for name in JOURNAL_METRICS:
        value = (metrics or {}).get(name)
        if value is not None:
            tags.append(f"{name}:{value}")
    if tone:
        tags.append(f"tone:{tone}")
    tags.extend(status_tags or [])
    return tags


def journal_section_tags(date: str, journal_type: str, section: str) -> list[str]:
    return [JOURNAL_SECTION_TAG, CHUNK_TAG, f"date:{date}",
            f"type:{journal_type}", f"section:{section}"]


TODO_TAG = "todo"
TODO_ITEM_TAG = "todo-item"

SAME_DAY_LINK = "same_day"


def todo_day_key(date: str) -> str:
    return f"todo_day:{date}"


def todo_item_key(date: str, item_id: str) -> str:
    return f"todo_item:{date}:{item_id}"


def todo_tags(date: str, *, done: int, total: int) -> list[str]:
    return [TODO_TAG, f"date:{date}", f"done:{done}", f"total:{total}"]


def todo_item_tags(date: str, *, completed: bool, position: int) -> list[str]:
    return [TODO_ITEM_TAG, f"date:{date}",
            f"completed:{'true' if completed else 'false'}",
            f"position:{position}"]


BLOG_TAG = "blog"
BLOG_ARTICLE_TAG = "blog-article"
BLOG_CHUNK_TAG = "blog-chunk"

BLOG_SCOPE = "shared"


def blog_lineage_key(guid: str) -> str:
    return f"{BLOG_TAG}:{guid}"


def blog_article_lineage_key(guid: str) -> str:
    return f"{BLOG_ARTICLE_TAG}:{guid}"


def blog_tags(guid: str, *, date: str, slug: str | None = None,
              category: str | None = None,
              topics: list[str] | None = None) -> list[str]:
    "The hub's tags."
    tags = [BLOG_TAG, f"date:{date}", f"guid:{guid}"]
    if slug:
        tags.append(f"slug:{slug}")
    if category:
        tags.append(f"category:{slugify_item(category)}")
    for topic in topics or []:
        slugged = slugify_item(topic)
        if slugged:
            tags.append(f"topic:{slugged}")
    return tags


def blog_article_tags(guid: str, *, date: str,
                      slug: str | None = None) -> list[str]:
    tags = [ARTIFACT_TAG, BLOG_ARTICLE_TAG, f"date:{date}", f"guid:{guid}"]
    if slug:
        tags.append(f"slug:{slug}")
    return tags


def blog_chunk_tags(guid: str, index: int) -> list[str]:
    return [BLOG_CHUNK_TAG, CHUNK_TAG, f"guid:{guid}", f"position:{index}"]


CONTACTS_TAG = "memory-v2-contacts"
CONTACT_TAG = "contact"
CONTACT_DIRECTORY_TAG = "contact-directory"
CONTACT_PORTRAIT_TAG = "contact-portrait"

EMAIL_TAG = "memory-v2-email"
EMAIL_MAILBOX_TAG = "email-mailbox"
EMAIL_MESSAGE_TAG = "email-message"
EMAIL_PASSAGE_TAG = "email-passage"
EMAIL_BACKUP_TAG = "email-backup"

IN_REPLY_TO_LINK = "in_reply_to"

NO_EMBED_TAG = "no-embed"

EMAIL_PASSAGE_CHARS = 3200
EMAIL_PASSAGE_STEP = 2880

PERSON_TAG = "person"


def person_slug(name: str) -> str:
    '`Sample Person` → `sample-person`. ONE canonical spelling.'
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug[:60] or "unknown"


def person_entity_id(slug: str) -> str:
    return f"{PERSON_TAG}:{slug}"


def person_tags(slug: str, *, display: str | None = None) -> list[str]:
    "A person hub's tags."
    tags = [PERSON_TAG, f"person:{slug}"]
    if display:
        tags.append(f"name:{display}")
    return tags


NodeKind = Literal["memory", "book", "journal", "blog", "email", "conversation",
                   "person", "todo", "artifact", "repo", "commit", "entity"]


def node_kind(tags: list[str] | tuple[str, ...]) -> NodeKind:
    t = set(tags or ())
    if t & set(COLLAPSED_TAGS):
        return "artifact"
    if BOOK_TAG in t:
        return "book"
    if JOURNAL_TAG in t:
        return "journal"
    if BLOG_TAG in t:
        return "blog"
    if EMAIL_MESSAGE_TAG in t or EMAIL_MAILBOX_TAG in t:
        return "email"
    if CONVERSATION_TAG in t:
        return "conversation"
    if PERSON_TAG in t:
        return "person"
    if TODO_ITEM_TAG in t or TODO_TAG in t:
        return "todo"
    if COMMIT_TAG in t:
        return "commit"
    if REPO_TAG in t:
        return "repo"
    if t & set(ARTIFACT_TAGS):
        return "artifact"
    if "collection" in t:
        return "entity"
    return "memory"


BOOK_TAG = "book"
BOOK_CHUNK_TAG = "book-chunk"

COLLAPSED_TAGS: tuple[str, ...] = (
    BOOK_CHUNK_TAG, JOURNAL_SECTION_TAG, TURN_TAG, BLOG_CHUNK_TAG,
    EMAIL_PASSAGE_TAG, EMAIL_BACKUP_TAG,
)


LIBRARY_SCOPE = "library"

BOOK_CHUNK_TOKENS = 1100


def book_tags(slug: str, *, author: str | None = None) -> list[str]:
    "The book hub's tags."
    tags = [BOOK_TAG, f"book:{slug}"]
    if author:
        tags.append(f"author:{slugify_item(author, max_len=60)}")
    return tags


def book_chunk_tags(slug: str, page: int) -> list[str]:
    return [BOOK_CHUNK_TAG, CHUNK_TAG, f"book:{slug}", f"page:{page}"]


EPISODIC_HUB_TAG = "episodic-memory-hub"


def episodic_hub_tags(chapter: str, *, title: str | None = None) -> list[str]:
    "The hub's tags. One place, so writer and reader agree."
    tags = [EPISODIC_HUB_TAG, SEED_TAG, f"chapter:{chapter}"]
    if title:
        tags.append(f"title:{slugify_item(title, max_len=60)}")
    return tags


SEED_TAG = "seed"


PROJECT_TAG = "project"
DIRECTORY_TAG = "directory"
DOCUMENT_TAG = "document"
FILE_PLACEHOLDER_TAG = "file"
PLACEHOLDER_TAG = "placeholder"

TEXT_SUFFIXES: tuple[str, ...] = (
    ".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".yaml", ".yml",
    ".log", ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".toml", ".ini",
    ".cfg", ".sql", ".html", ".css", ".c", ".h", ".cpp", ".rs", ".go", ".java",
)
WORD_SUFFIXES: tuple[str, ...] = (".docx",)
PDF_SUFFIXES: tuple[str, ...] = (".pdf",)

FileKind = Literal["text", "word", "pdf", "placeholder"]


def classify_file(path: str) -> FileKind:
    'What the ingester will do with this file. One place, one line to extend.'
    import posixpath

    suffix = posixpath.splitext(path.lower())[1]
    if suffix in TEXT_SUFFIXES:
        return "text"
    if suffix in WORD_SUFFIXES:
        return "word"
    if suffix in PDF_SUFFIXES:
        return "pdf"
    return "placeholder"


NEUTRALITY_EMOTION_NAME = "Neutrality"
NEUTRALITY_EMOTION_ID = 127

EMOTION_FAMILIES: tuple[str, ...] = (
    "joy", "love", "interest", "surprise", "fear", "anger", "sadness", "neutral",
)

_FAMILY_BANDS: tuple[tuple[int, str], ...] = (
    (18, "joy"), (36, "love"), (54, "interest"), (72, "surprise"),
    (90, "fear"), (108, "anger"), (126, "sadness"),
)


def family_of_emotion(emotion_id: int) -> str:
    'Emotion id → family, by the fixed vocabulary bands.'
    for ceiling, family in _FAMILY_BANDS:
        if emotion_id <= ceiling:
            return family
    return "neutral"
