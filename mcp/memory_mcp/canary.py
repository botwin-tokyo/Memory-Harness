from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

WRITE_TOOLS = frozenset({
    "remember", "intake", "link", "forget", "attach", "ingest", "git_index",
    "ingest_repo", "ingest_project", "ingest_conversations",
    "link_note_to_project", "link_conversation_to_project",
    "link_book_to_memory",
    "write_private_memory", "forget_private_memory",
})


@dataclass
class Probe:
    tool: str
    status: str
    detail: str = ""


@dataclass
class McpCanaryResult:
    probes: list[Probe] = field(default_factory=list)
    unprobed: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[Probe]:
        return [p for p in self.probes if p.status == "fail"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": not self.failed,
            "passed": sum(1 for p in self.probes if p.status == "pass"),
            "failed": len(self.failed),
            "skipped": sum(1 for p in self.probes if p.status == "skip"),
            "unprobed_write_tools": sorted(self.unprobed),
            "probes": [
                {"tool": p.tool, "status": p.status, "detail": p.detail}
                for p in self.probes
            ],
        }


def plan(tool_names: list[str], anchors: dict[str, str]) -> list[tuple[str, dict]]:
    '(tool, arguments) for every read-only tool worth calling.'
    out: list[tuple[str, dict]] = []
    for name in tool_names:
        if name in WRITE_TOOLS:
            continue
        if name == "recall":
            out.append((name, {"query": anchors.get("query", "Companion"),
                               "token_budget": 700}))
        elif name == "explore" and anchors.get("lineage"):
            out.append((name, {"start_id": anchors["lineage"], "depth": 1}))
        elif name == "history" and anchors.get("lineage"):
            out.append((name, {"lineage_id": anchors["lineage"]}))
        elif name == "quote_source" and anchors.get("artifact"):
            out.append((name, {"target": anchors["artifact"], "max_tokens": 300}))
        elif name == "fetch_file" and anchors.get("repo") and anchors.get("path"):
            out.append((name, {"repo": anchors["repo"], "path": anchors["path"]}))
        elif name == "search_library":
            out.append((name, {"query": anchors.get("book_query", "reference"),
                               "limit": 2}))
        elif name == "read_book_passage" and anchors.get("book"):
            out.append((name, {"book": anchors["book"],
                               "query": anchors.get("book_query", "reference"),
                               "limit": 1}))
        elif name == "get_book" and anchors.get("book"):
            out.append((name, {"book": anchors["book"]}))
        elif name == "project_tree" and anchors.get("project"):
            out.append((name, {"project": anchors["project"]}))
        elif name == "find_in_project" and anchors.get("project"):
            out.append((name, {"project": anchors["project"], "query": "the"}))
        elif name == "get_conversation" and anchors.get("session"):
            out.append((name, {"session": anchors["session"]}))
        elif name == "find_in_conversation" and anchors.get("session"):
            out.append((name, {"query": "the"}))
        elif name in ("search_private_memory",):
            out.append((name, {"query": "anything", "limit": 1}))
        elif name in ("emotions", "list_books", "list_projects",
                      "list_conversations", "list_private_memories"):
            out.append((name, {}))
    return out


def classify(tool: str, is_error: bool, text: str) -> Probe:
    'One tool result → a verdict.'
    if not is_error:
        return Probe(tool, "pass", text[:110].replace("\n", " "))
    low = text.lower()
    if "no access to" in low or "403" in low:
        return Probe(tool, "fail", f"scope: {text[:110]}")
    if "seed key" in low or "401" in low:
        return Probe(tool, "fail", f"auth: {text[:110]}")
    if "not found" in low or "404" in low or "no such" in low:
        return Probe(tool, "skip", f"anchor stale: {text[:90]}")
    return Probe(tool, "fail", text[:130].replace("\n", " "))
