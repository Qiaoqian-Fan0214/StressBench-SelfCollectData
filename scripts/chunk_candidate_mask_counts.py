from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def chunk_counts(value: str, chunk_size: int) -> list[str]:
    return [value[index : index + chunk_size] for index in range(0, len(value), chunk_size)]


def convert_rle(rle: dict[str, Any], chunk_size: int) -> bool:
    if "counts_chunks" in rle:
        rle["counts_format"] = "compressed_chunks"
        rle["format"] = "coco_rle"
        rle.pop("counts", None)
        return False

    counts = rle.get("counts")
    if not isinstance(counts, str):
        return False

    rle["counts_chunks"] = chunk_counts(counts, chunk_size)
    rle["counts_format"] = "compressed_chunks"
    rle["format"] = "coco_rle"
    rle.pop("counts", None)
    return True


def convert_file(path: Path, chunk_size: int) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    converted = 0
    for obj in data.get("objects", []):
        rle = obj.get("mask", {}).get("rle")
        if isinstance(rle, dict) and convert_rle(rle, chunk_size):
            converted += 1
    if converted:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return converted


def main() -> int:
    parser = argparse.ArgumentParser(description="Split compressed COCO RLE counts into short JSON chunks.")
    parser.add_argument("root", type=Path, nargs="?", default=Path("annotation_hub/self_collection/object_candidates_free"))
    parser.add_argument("--chunk-size", type=int, default=16)
    args = parser.parse_args()

    files = sorted(args.root.glob("*/*.json"))
    converted_files = 0
    converted_masks = 0
    for index, path in enumerate(files, 1):
        converted = convert_file(path, args.chunk_size)
        if converted:
            converted_files += 1
            converted_masks += converted
        if index % 100 == 0:
            print(f"processed={index}/{len(files)} converted_files={converted_files} converted_masks={converted_masks}", flush=True)
    print(f"done files={len(files)} converted_files={converted_files} converted_masks={converted_masks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
