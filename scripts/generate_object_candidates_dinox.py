from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from annotation_server import dataset_rows, get_dataset_config, now_utc_iso  # noqa: E402


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "annotation_hub" / "self_collection" / "object_candidates"
TOKEN_ENV_NAMES = ("DINOX_API_TOKEN", "DDS_API_TOKEN", "DEEPDATASPACE_API_TOKEN")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def get_token() -> str | None:
    for name in TOKEN_ENV_NAMES:
        token = os.environ.get(name)
        if token:
            return token
    return None


def normalize_label(raw_name: str, label_map: dict[str, str]) -> str:
    cleaned = " ".join(raw_name.lower().strip().split())
    return label_map.get(cleaned, cleaned)


def bbox_xywh_from_xyxy(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = bbox
    return [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]


def json_safe_mask(mask: Any) -> Any:
    if isinstance(mask, dict):
        safe = dict(mask)
        counts = safe.get("counts")
        if isinstance(counts, bytes):
            safe["counts"] = counts.decode("utf-8")
        return safe
    return mask


def iter_items(dataset_id: str, subcategory: str | None, sample_id: str | None) -> list[dict[str, Any]]:
    dataset = get_dataset_config(dataset_id)
    rows = dataset_rows(dataset)
    if subcategory:
        rows = [row for row in rows if row["subcategory"] == subcategory]
    if sample_id:
        rows = [row for row in rows if row["sample_id"] == sample_id]
    return rows


def run_dinox(image_path: Path, args: argparse.Namespace) -> list[dict[str, Any]]:
    from dds_cloudapi_sdk import Client, Config
    from dds_cloudapi_sdk.image_resizer import image_to_base64
    from dds_cloudapi_sdk.tasks.v2_task import V2Task

    token = get_token()
    if not token:
        envs = ", ".join(TOKEN_ENV_NAMES)
        raise RuntimeError(f"Missing DINO-X API token. Set one of: {envs}")

    prompt: dict[str, str] = {"type": "universal"}
    if args.text_prompt:
        prompt = {"type": "text", "text": args.text_prompt}

    api_body = {
        "model": args.model,
        "image": image_to_base64(str(image_path)),
        "prompt": prompt,
        "targets": ["bbox", "mask"],
        "mask_format": "coco_rle",
        "bbox_threshold": args.bbox_threshold,
        "iou_threshold": args.iou_threshold,
    }

    client = Client(Config(token))
    task = V2Task(api_path="/v2/task/dinox/detection", api_body=api_body)
    client.run_task(task)
    result = task.result or {}
    objects = result.get("objects", [])
    if not isinstance(objects, list):
        raise RuntimeError(f"Unexpected DINO-X response objects type: {type(objects).__name__}")
    return objects


def build_payload(
    item: dict[str, Any],
    image_path: Path,
    width: int,
    height: int,
    raw_objects: list[dict[str, Any]],
    label_map: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    for idx, obj in enumerate(raw_objects):
        raw_name = str(obj.get("category", "")).strip()
        bbox = [float(v) for v in obj.get("bbox", [])]
        if len(bbox) != 4:
            continue

        normalized_name = normalize_label(raw_name, label_map)
        objects.append(
            {
                "object_id": f"{item['sample_id']}_obj_{idx:03d}",
                "name": normalized_name,
                "raw_name": raw_name,
                "score": float(obj.get("score", 0.0)),
                "bbox_xyxy": bbox,
                "bbox_xywh": bbox_xywh_from_xyxy(bbox),
                "mask": {
                    "format": "coco_rle",
                    "rle": json_safe_mask(obj.get("mask")),
                },
                "source_index": idx,
            }
        )

    return {
        "sample_id": item["sample_id"],
        "image_id": item["sample_id"],
        "rgb": item["rgb"],
        "image_path": str(image_path.relative_to(REPO_ROOT)),
        "width": width,
        "height": height,
        "created_at": now_utc_iso(),
        "generator": {
            "name": "DINO-X",
            "model": args.model,
            "api_path": "/v2/task/dinox/detection",
            "prompt_type": "text" if args.text_prompt else "universal",
            "text_prompt": args.text_prompt,
            "targets": ["bbox", "mask"],
            "mask_format": "coco_rle",
            "bbox_threshold": args.bbox_threshold,
            "iou_threshold": args.iou_threshold,
            "label_map": str(args.label_map) if args.label_map else None,
        },
        "objects": objects,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate per-image object candidate JSON with DINO-X.")
    parser.add_argument("--dataset-id", default="self_collection")
    parser.add_argument("--subcategory", help="Only process one subcategory.")
    parser.add_argument("--sample-id", help="Only process one sample id.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--label-map", type=Path, help="Optional raw-label to canonical-label JSON map.")
    parser.add_argument("--model", default="DINO-X-1.0")
    parser.add_argument("--text-prompt", help="Optional dot-separated text prompt. Omit for prompt-free mode.")
    parser.add_argument("--bbox-threshold", type=float, default=0.25)
    parser.add_argument("--iou-threshold", type=float, default=0.8)
    parser.add_argument("--limit", type=int, help="Maximum number of images to process.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between API calls.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="List planned work without calling DINO-X.")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    label_map = read_json(args.label_map) if args.label_map else {}
    if not isinstance(label_map, dict):
        raise ValueError("--label-map must point to a JSON object")
    label_map = {
        " ".join(str(k).lower().strip().split()): " ".join(str(v).lower().strip().split())
        for k, v in label_map.items()
    }

    items = iter_items(args.dataset_id, args.subcategory, args.sample_id)
    if args.limit is not None:
        items = items[: args.limit]

    if not items:
        print("No matching images found.")
        return 1

    planned = 0
    written = 0
    skipped = 0
    failed = 0

    for item in items:
        image_path = REPO_ROOT / item["rgb"]
        output_path = args.output_root / item["subcategory"] / f"{item['sample_id']}.json"
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue

        planned += 1
        if args.dry_run:
            print(f"DRY {item['sample_id']} {item['rgb']} -> {output_path.relative_to(REPO_ROOT)}")
            continue

        try:
            from PIL import Image

            with Image.open(image_path) as image:
                width, height = image.size
            raw_objects = run_dinox(image_path, args)
            payload = build_payload(item, image_path, width, height, raw_objects, label_map, args)
            atomic_write_json(output_path, payload)
            written += 1
            print(f"WROTE {output_path.relative_to(REPO_ROOT)} objects={len(payload['objects'])}")
            if args.sleep > 0:
                time.sleep(args.sleep)
        except Exception as exc:
            failed += 1
            print(f"ERROR {item['sample_id']} {item['rgb']}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                return 2

    print(f"done planned={planned} written={written} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
