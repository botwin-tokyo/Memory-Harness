'Verbatim chunking: ~800 tokens with ~80 overlap, page/section breaks first.'

from __future__ import annotations

CHARS_PER_TOKEN = 4


def chunk_text(
    text: str, chunk_tokens: int = 800, overlap_tokens: int = 80
) -> list[str]:
    if "\f" in text:
        chunks: list[str] = []
        for page in text.split("\f"):
            chunks.extend(chunk_text(page, chunk_tokens, overlap_tokens))
        return chunks

    max_chars = chunk_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if end < len(text):
            window = text[start:end]
            cut = -1
            for sep in ("\n\n", "\n", " "):
                found = window.rfind(sep, max_chars // 2)
                if found != -1:
                    cut = found
                    break
            if cut != -1:
                end = start + cut
        piece = text[start:end].strip("\n ")
        if piece:
            chunks.append(piece)
        if end >= len(text):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks
