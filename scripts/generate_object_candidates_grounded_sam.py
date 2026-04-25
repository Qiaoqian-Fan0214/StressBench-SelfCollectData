from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

import numpy as np
from PIL import Image
from pycocotools import mask as mask_utils


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from annotation_server import dataset_rows, get_dataset_config, now_utc_iso  # noqa: E402


DEFAULT_OUTPUT_ROOT = REPO_ROOT / "annotation_hub" / "self_collection" / "object_candidates_free"
DEFAULT_LABEL_FILE = REPO_ROOT / "object_label_vocab" / "indoor_objects.txt"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def read_labels(path: Path) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        label = " ".join(line.split()).lower()
        if not label or label.startswith("#") or label in seen:
            continue
        labels.append(label)
        seen.add(label)
    return labels


def chunked(values: list[str], chunk_size: int) -> list[list[str]]:
    return [values[i : i + chunk_size] for i in range(0, len(values), chunk_size)]


def iter_items(dataset_id: str, subcategory: str | None, sample_id: str | None) -> list[dict[str, Any]]:
    dataset = get_dataset_config(dataset_id)
    rows = dataset_rows(dataset)
    if subcategory:
        rows = [row for row in rows if row["subcategory"] == subcategory]
    if sample_id:
        rows = [row for row in rows if row["sample_id"] == sample_id]
    return rows


def bbox_xywh_from_xyxy(bbox: list[float]) -> list[float]:
    x1, y1, x2, y2 = bbox
    return [x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)]


def clean_detected_label(raw_label: str, labels: list[str]) -> str:
    cleaned = " ".join(raw_label.lower().replace(".", " ").replace(",", " ").split())
    if cleaned.startswith("a "):
        cleaned = cleaned[2:]
    if cleaned.startswith("an "):
        cleaned = cleaned[3:]

    label_set = set(labels)
    if cleaned in label_set:
        return cleaned

    # GroundingDINO can return merged phrases such as "monitor a screen" when
    # neighboring prompts both match. Snap those back to the first vocab label.
    matches = []
    padded = f" {cleaned} "
    for label in labels:
        position = padded.find(f" {label} ")
        if position >= 0:
            matches.append((position, -len(label), label))
    if matches:
        return sorted(matches)[0][2]

    prefix_matches = [label for label in labels if label.startswith(f"{cleaned} ")]
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    return cleaned


def encode_mask(mask: np.ndarray) -> dict[str, Any]:
    binary = np.asfortranarray(mask.astype(np.uint8))
    rle = mask_utils.encode(binary)
    counts = rle.get("counts")
    if isinstance(counts, bytes):
        counts = counts.decode("utf-8")
    if isinstance(counts, str):
        rle["counts_chunks"] = [counts[index : index + 16] for index in range(0, len(counts), 16)]
        rle["counts_format"] = "compressed_chunks"
        rle.pop("counts", None)
    rle["format"] = "coco_rle"
    return rle


def load_models(args: argparse.Namespace):
    import torch
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor, SamModel, SamProcessor

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device.startswith("cuda") and args.fp16 else torch.float32

    detector_processor = AutoProcessor.from_pretrained(args.detector_model)
    detector = AutoModelForZeroShotObjectDetection.from_pretrained(args.detector_model, torch_dtype=dtype).to(device)
    detector.eval()

    sam_processor = SamProcessor.from_pretrained(args.sam_model)
    sam = SamModel.from_pretrained(args.sam_model, torch_dtype=dtype).to(device)
    sam.eval()

    return {
        "torch": torch,
        "device": device,
        "detector_processor": detector_processor,
        "detector": detector,
        "sam_processor": sam_processor,
        "sam": sam,
    }


def detect_objects(image: Image.Image, labels: list[str], models: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    torch = models["torch"]
    processor = models["detector_processor"]
    detector = models["detector"]
    device = models["device"]

    candidates: list[dict[str, Any]] = []
    for label_chunk in chunked(labels, args.labels_per_chunk):
        text_labels = [label_chunk]
        inputs = processor(images=image, text=text_labels, return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = detector(**inputs)

        results = processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            threshold=args.box_threshold,
            text_threshold=args.text_threshold,
            target_sizes=[image.size[::-1]],
        )[0]

        result_labels = results.get("text_labels", results.get("labels", []))
        for box, score, label in zip(results["boxes"], results["scores"], result_labels):
            raw_name = str(label).lower().strip()
            name = clean_detected_label(raw_name, labels)
            candidates.append(
                {
                    "name": name,
                    "raw_name": raw_name,
                    "score": float(score.detach().cpu().item()),
                    "bbox_xyxy": [float(x) for x in box.detach().cpu().tolist()],
                }
            )

    return nms_candidates(candidates, models, args)


def nms_candidates(candidates: list[dict[str, Any]], models: dict[str, Any], args: argparse.Namespace) -> list[dict[str, Any]]:
    if not candidates:
        return []

    torch = models["torch"]
    from torchvision.ops import nms

    boxes = torch.tensor([c["bbox_xyxy"] for c in candidates], dtype=torch.float32)
    scores = torch.tensor([c["score"] for c in candidates], dtype=torch.float32)
    keep = nms(boxes, scores, args.nms_threshold).detach().cpu().tolist()
    kept_candidates = [candidates[i] for i in keep]
    kept_candidates.sort(key=lambda candidate: candidate["score"], reverse=True)
    return kept_candidates[: args.max_objects]


def segment_boxes(image: Image.Image, candidates: list[dict[str, Any]], models: dict[str, Any], args: argparse.Namespace) -> list[np.ndarray]:
    if not candidates:
        return []

    torch = models["torch"]
    processor = models["sam_processor"]
    sam = models["sam"]
    device = models["device"]
    boxes = [candidate["bbox_xyxy"] for candidate in candidates]

    masks: list[np.ndarray] = []
    for box_chunk in chunked(boxes, args.sam_boxes_per_chunk):
        inputs = processor(images=image, input_boxes=[box_chunk], return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = sam(**inputs, multimask_output=False)
        processed = processor.image_processor.post_process_masks(
            outputs.pred_masks.detach().cpu(),
            inputs["original_sizes"].detach().cpu(),
            inputs["reshaped_input_sizes"].detach().cpu(),
        )[0]

        processed = processed.squeeze(1) if processed.ndim == 4 else processed
        masks.extend([(mask.detach().cpu().numpy() > args.mask_threshold) for mask in processed])

    return masks


def build_payload(
    item: dict[str, Any],
    image_path: Path,
    width: int,
    height: int,
    candidates: list[dict[str, Any]],
    masks: list[np.ndarray],
    labels: list[str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    ranked = sorted(zip(candidates, masks), key=lambda pair: pair[0]["score"], reverse=True)
    for idx, (candidate, mask) in enumerate(ranked[: args.max_objects]):
        bbox = candidate["bbox_xyxy"]
        objects.append(
            {
                "object_id": f"{item['sample_id']}_obj_{idx:03d}",
                "name": candidate["name"],
                "raw_name": candidate["raw_name"],
                "score": candidate["score"],
                "bbox_xyxy": bbox,
                "bbox_xywh": bbox_xywh_from_xyxy(bbox),
                "mask": {
                    "format": "coco_rle",
                    "rle": encode_mask(mask),
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
            "name": "local-grounded-sam",
            "detector_model": args.detector_model,
            "segmenter_model": args.sam_model,
            "label_file": str(args.label_file),
            "label_count": len(labels),
            "box_threshold": args.box_threshold,
            "text_threshold": args.text_threshold,
            "nms_threshold": args.nms_threshold,
            "max_objects": args.max_objects,
            "selection": "top_score_after_nms",
            "mask_format": "coco_rle",
        },
        "objects": objects,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate object candidates with local GroundingDINO + SAM.")
    parser.add_argument("--dataset-id", default="self_collection")
    parser.add_argument("--subcategory", help="Only process one subcategory.")
    parser.add_argument("--sample-id", help="Only process one sample id.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--label-file", type=Path, default=DEFAULT_LABEL_FILE)
    parser.add_argument("--detector-model", default="IDEA-Research/grounding-dino-tiny")
    parser.add_argument("--sam-model", default="facebook/sam-vit-base")
    parser.add_argument("--device", help="Override device, e.g. cuda or cpu.")
    parser.add_argument("--fp16", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--box-threshold", type=float, default=0.28)
    parser.add_argument("--text-threshold", type=float, default=0.25)
    parser.add_argument("--nms-threshold", type=float, default=0.6)
    parser.add_argument("--mask-threshold", type=float, default=0.0)
    parser.add_argument("--labels-per-chunk", type=int, default=20)
    parser.add_argument("--sam-boxes-per-chunk", type=int, default=24)
    parser.add_argument("--max-objects", type=int, default=15)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    labels = read_labels(args.label_file)
    if not labels:
        raise ValueError(f"No labels found in {args.label_file}")

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
    models = None

    for item in items:
        image_path = REPO_ROOT / item["rgb"]
        output_path = args.output_root / item["subcategory"] / f"{item['sample_id']}.json"
        if output_path.exists() and not args.overwrite:
            skipped += 1
            continue

        planned += 1
        if args.dry_run:
            print(f"DRY {item['sample_id']} {item['rgb']} -> {output_path.relative_to(REPO_ROOT)} labels={len(labels)}")
            continue

        try:
            if models is None:
                models = load_models(args)
                print(f"Loaded models on {models['device']}")

            image = Image.open(image_path).convert("RGB")
            width, height = image.size
            candidates = detect_objects(image, labels, models, args)
            masks = segment_boxes(image, candidates, models, args)
            payload = build_payload(item, image_path, width, height, candidates, masks, labels, args)
            atomic_write_json(output_path, payload)
            written += 1
            print(f"WROTE {output_path.relative_to(REPO_ROOT)} objects={len(payload['objects'])}")
        except Exception as exc:
            failed += 1
            print(f"ERROR {item['sample_id']} {item['rgb']}: {exc}", file=sys.stderr)
            if not args.continue_on_error:
                return 2

    print(f"done planned={planned} written={written} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
