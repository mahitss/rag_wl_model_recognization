"""Deterministic page-local character chunking with word-boundary preservation."""

from collections.abc import Iterator


def chunk_page_text(
    text: str,
    *,
    page_number: int,
    chunk_size: int,
    overlap: int,
) -> Iterator[dict[str, int | str]]:
    if chunk_size <= 0 or overlap < 0 or overlap >= chunk_size:
        raise ValueError("Chunk size and overlap are invalid")

    start = 0
    chunk_index = 0
    while start < len(text):
        max_end = min(start + chunk_size, len(text))
        end = max_end
        if max_end < len(text):
            floor = start + chunk_size // 2
            boundaries = [text.rfind(mark, floor, max_end) for mark in (" ", "\n", "\t")]
            boundary = max(boundaries)
            if boundary > start:
                end = boundary

        raw_chunk = text[start:end]
        leading = len(raw_chunk) - len(raw_chunk.lstrip())
        trailing = len(raw_chunk.rstrip())
        chunk_text = raw_chunk.strip()
        if chunk_text:
            actual_start = start + leading
            actual_end = start + trailing
            yield {
                "page_number": page_number,
                "chunk_index": chunk_index,
                "char_start": actual_start,
                "char_end": actual_end,
                "text": chunk_text,
            }
            chunk_index += 1

        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        while (
            next_start < end
            and next_start > 0
            and not text[next_start - 1].isspace()
            and not text[next_start].isspace()
        ):
            next_start += 1
        start = next_start
