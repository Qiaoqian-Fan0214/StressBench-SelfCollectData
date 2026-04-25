from __future__ import annotations

from typing import Any


def normalize_rle_for_decode(rle: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(rle)
    chunks = normalized.pop("counts_chunks", None)
    if chunks is not None:
        normalized["counts"] = "".join(chunks)
    normalized.pop("counts_format", None)
    normalized.pop("format", None)
    return normalized
