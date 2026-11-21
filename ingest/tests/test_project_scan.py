from __future__ import annotations

from pathlib import Path

import pytest

from memory_ingest.project import DEFAULT_IGNORE, build_index, scan_project


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "episode-01"
    (root / "docs").mkdir(parents=True)
    (root / "assets").mkdir()
    (root / "node_modules" / "junk").mkdir(parents=True)
    (root / ".git").mkdir()
    (root / "README.md").write_text("# Episode 01\n\nThe opening.")
    (root / "docs" / "outline.txt").write_text("Beat one. Beat two.")
    (root / "docs" / "script.docx").write_bytes(b"PK\x03\x04fake docx")
    (root / "assets" / "slide.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (root / "assets" / "deck.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "node_modules" / "junk" / "x.js").write_text("nope")
    (root / ".git" / "config").write_text("nope")
    (root / ".hidden").write_text("nope")
    return root


def test_scan_finds_the_real_tree_and_skips_the_junk(tree: Path) -> None:
    m = scan_project(tree, project="episode-01")
    paths = {f.relpath for f in m.files}
    assert paths == {
        "README.md", "docs/outline.txt", "docs/script.docx",
        "assets/slide.png", "assets/deck.pdf",
    }
    assert not any("node_modules" in p for p in paths)
    assert not any(p.startswith(".") for p in paths)
    dirs = {d.relpath for d in m.dirs}
    assert dirs == {"", "docs", "assets"}
    assert next(d for d in m.dirs if d.relpath == "docs").parent == ""


def test_classification_matches_the_contract(tree: Path) -> None:
    kinds = {f.relpath: f.kind for f in scan_project(tree).files}
    assert kinds["README.md"] == "text"
    assert kinds["docs/outline.txt"] == "text"
    assert kinds["docs/script.docx"] == "word"
    assert kinds["assets/deck.pdf"] == "pdf"
    assert kinds["assets/slide.png"] == "placeholder"


def test_the_manifest_sha_is_stable_and_content_sensitive(tree: Path) -> None:
    first = scan_project(tree).sha
    assert scan_project(tree).sha == first

    (tree / "docs" / "outline.txt").write_text("Beat one. Beat two. Beat three.")
    assert scan_project(tree).sha != first

    (tree / "docs" / "outline.txt").write_text("Beat one. Beat two.")
    assert scan_project(tree).sha == first


def test_a_new_file_changes_the_sha(tree: Path) -> None:
    before = scan_project(tree).sha
    (tree / "docs" / "notes.md").write_text("late addition")
    assert scan_project(tree).sha != before


def test_custom_ignore_list(tree: Path) -> None:
    m = scan_project(tree, ignore=(*DEFAULT_IGNORE, "assets"))
    assert not any(f.relpath.startswith("assets") for f in m.files)
    assert "assets" not in {d.relpath for d in m.dirs}


def test_index_renders_the_annotated_tree(tree: Path) -> None:
    m = scan_project(tree, project="episode-01")
    index = build_index(m)
    assert "# PROJECT — episode-01" in index
    assert f"manifest-sha: {m.sha}" in index
    assert "directories: 3 · files: 5" in index
    assert "docs/" in index and "assets/" in index
    assert "outline.txt  [text," in index
    assert "slide.png  [placeholder," in index
    assert "(not read — placeholder)" in index
    lines = index.splitlines()
    docs_at = next(i for i, l in enumerate(lines) if l.strip() == "docs/")
    assert lines[docs_at + 1].startswith("    ")


def test_scan_refuses_a_non_directory(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("x")
    with pytest.raises(NotADirectoryError):
        scan_project(f)
