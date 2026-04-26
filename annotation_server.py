from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = ROOT / "annotation_hub" / "datasets.json"
LOCAL_STATE_ROOT = ROOT / ".annotation_state"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
STRESS_OPTIONS = {
    "M": ["dark_absorptive", "low_contrast_blend", "complex_texture", "transparent", "specular_confusion"],
    "V": ["extreme_viewpoint", "truncated_out_of_frame", "large_scale", "small_scale"],
    "G": ["occlusion", "non_rigid_deform", "stacked_layout", "cluttered_layout"],
    "L": ["global_overexposure", "local_overexposure", "global_underexposure", "local_underexposure"],
}
DATASET_CACHE: dict[str, dict[str, object]] = {}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def atomic_write_json(path: Path, payload: dict) -> None:
    ensure_parent(path)
    with NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    os.replace(temp_name, path)


def append_event(dataset_id: str, payload: dict) -> None:
    state_dir = LOCAL_STATE_ROOT / dataset_id
    state_dir.mkdir(parents=True, exist_ok=True)
    event_path = state_dir / "events.jsonl"
    with event_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False))
        handle.write("\n")


def parse_registry() -> dict:
    data = read_json(REGISTRY_PATH)
    if not isinstance(data, dict) or not isinstance(data.get("datasets"), list):
        raise ValueError("annotation_hub/datasets.json must contain a datasets array")
    return data


def get_dataset_config(dataset_id: str) -> dict:
    registry = parse_registry()
    for dataset in registry["datasets"]:
        if dataset.get("id") == dataset_id:
            return dataset
    raise KeyError(f"Unknown dataset: {dataset_id}")


def safe_slug(value: object) -> str:
    raw = str(value or "").strip()
    slug = re.sub(r"[^0-9A-Za-z_]+", "_", raw).strip("_")
    return slug or "item"


def natural_sort_key(value: str) -> tuple:
    parts = re.split(r"(\d+)", value)
    out: list[object] = []
    for part in parts:
        out.append(int(part) if part.isdigit() else part.lower())
    return tuple(out)


def sample_id_for(dataset_id: str, subcategory: str, filename: str) -> str:
    stem = safe_slug(Path(filename).stem)
    digest = hashlib.sha1(f"{dataset_id}/{subcategory}/{filename}".encode("utf-8")).hexdigest()[:8]
    return f"{safe_slug(dataset_id)}_{safe_slug(subcategory)}_{stem}_{digest}"


def build_dataset_rows(dataset: dict) -> list[dict]:
    dataset_id = str(dataset["id"])
    source_dir = ROOT / str(dataset.get("source_dir") or ".")
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Missing source dir: {source_dir}")

    rows: list[dict] = []
    for subcategory in dataset.get("subcategories") or []:
        sub_dir = source_dir / str(subcategory)
        if not sub_dir.is_dir():
            raise FileNotFoundError(f"Missing subcategory dir: {sub_dir}")

        image_paths = sorted(
            [path for path in sub_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES],
            key=lambda path: natural_sort_key(path.name),
        )

        for image_path in image_paths:
            rows.append(
                {
                    "sample_id": sample_id_for(dataset_id, str(subcategory), image_path.name),
                    "subcategory": str(subcategory),
                    "source_split": dataset.get("provenance_split", "self_collected"),
                    "question": None,
                    "instruction": None,
                    "answer": None,
                    "rgb": f"{subcategory}/{image_path.name}",
                    "depth_image": None,
                    "mask": None,
                    "task": None,
                    "gt": None,
                    "official_id": image_path.name,
                    "category_label_zh": "第一阶段：Stress 标注",
                    "source_filename": image_path.name,
                    "annotation_entries": [],
                }
            )
    sample_ids = [row["sample_id"] for row in rows]
    if len(sample_ids) != len(set(sample_ids)):
        raise ValueError(f"Duplicate sample_id generated for dataset: {dataset_id}")
    return rows


def dataset_rows(dataset: dict) -> list[dict]:
    dataset_id = str(dataset["id"])
    cached = DATASET_CACHE.get(dataset_id)
    if cached:
        return list(cached["rows"])
    rows = build_dataset_rows(dataset)
    DATASET_CACHE[dataset_id] = {
        "rows": rows,
        "index": {row["sample_id"]: row for row in rows},
    }
    return list(rows)


def dataset_index(dataset: dict) -> dict[str, dict]:
    dataset_id = str(dataset["id"])
    cached = DATASET_CACHE.get(dataset_id)
    if not cached:
        dataset_rows(dataset)
        cached = DATASET_CACHE[dataset_id]
    return dict(cached["index"])


def sample_record_dir(dataset: dict, item: dict) -> Path:
    return ROOT / dataset["record_root"] / item["subcategory"]


def sample_record_paths(dataset: dict, item: dict) -> list[Path]:
    record_dir = sample_record_dir(dataset, item)
    if not record_dir.is_dir():
        return []
    paths = list(record_dir.glob(f"{item['sample_id']}*.json"))
    return sorted(paths, key=lambda path: natural_sort_key(path.name))


def source_sample_id_from_record(record: dict) -> str | None:
    if not isinstance(record, dict):
        return None
    source_sample_id = record.get("source_sample_id")
    if source_sample_id:
        return str(source_sample_id)
    annotation = record.get("annotation")
    if isinstance(annotation, dict) and annotation.get("source_sample_id"):
        return str(annotation["source_sample_id"])
    sample_id = str(record.get("sample_id") or "")
    if "__" in sample_id:
        return sample_id.split("__", 1)[0]
    return sample_id or None


def cursor_path(dataset_id: str) -> Path:
    return LOCAL_STATE_ROOT / dataset_id / "cursor.json"


def read_cursor(dataset_id: str) -> dict | None:
    path = cursor_path(dataset_id)
    if not path.is_file():
        return None
    return read_json(path)


def load_record_map(dataset: dict) -> dict[str, dict]:
    root = ROOT / dataset["record_root"]
    index = dataset_index(dataset)
    result: dict[str, dict] = {}
    if not root.exists():
        return result
    for path in root.rglob("*.json"):
        try:
            record = read_json(path)
        except Exception:
            continue
        sample_id = source_sample_id_from_record(record)
        if sample_id not in index or record.get("status") not in {"annotated", "discarded"}:
            continue
        bucket = result.setdefault(
            sample_id,
            {
                "status": "unprocessed",
                "updated_at": None,
                "annotated_count": 0,
                "discarded_count": 0,
            },
        )
        updated_at = record.get("updated_at")
        if updated_at and (not bucket["updated_at"] or str(updated_at) > str(bucket["updated_at"])):
            bucket["updated_at"] = updated_at
        if record["status"] == "annotated":
            bucket["annotated_count"] += 1
        elif record["status"] == "discarded":
            bucket["discarded_count"] += 1
    for sample_id, bucket in result.items():
        bucket["status"] = "annotated" if bucket["annotated_count"] else "discarded"
    return result


def remove_sample_records(dataset: dict, item: dict) -> None:
    for path in sample_record_paths(dataset, item):
        if path.is_file():
            path.unlink()


def validate_entries(payload: object) -> list[dict]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("entries must be a non-empty array")
    normalized: list[dict] = []
    seen_entry_ids: set[str] = set()
    for raw_entry in payload:
        if not isinstance(raw_entry, dict):
            raise ValueError("each entry must be an object")
        entry_id = safe_slug(raw_entry.get("entry_id"))
        if not entry_id:
            raise ValueError("entry_id is required")
        if entry_id in seen_entry_ids:
            raise ValueError(f"Duplicate entry_id: {entry_id}")
        seen_entry_ids.add(entry_id)

        task = str(raw_entry.get("task") or "").strip()
        template_id = str(raw_entry.get("template_id") or "").strip()
        question = str(raw_entry.get("question") or "").strip()
        instruction = str(raw_entry.get("instruction") or question).strip()
        outputs = raw_entry.get("outputs")
        editor_state = raw_entry.get("editor_state") if isinstance(raw_entry.get("editor_state"), dict) else {}
        if not task:
            raise ValueError(f"entry {entry_id}: task is required")
        if not template_id:
            raise ValueError(f"entry {entry_id}: template_id is required")
        if not question:
            raise ValueError(f"entry {entry_id}: question is required")
        if not instruction:
            raise ValueError(f"entry {entry_id}: instruction is required")
        if not isinstance(outputs, list) or not outputs:
            raise ValueError(f"entry {entry_id}: outputs must be a non-empty array")

        normalized_outputs: list[dict] = []
        for output in outputs:
            if not isinstance(output, dict):
                raise ValueError(f"entry {entry_id}: each output must be an object")
            gt = output.get("gt")
            if not isinstance(gt, dict) or not gt.get("type"):
                raise ValueError(f"entry {entry_id}: each output must include gt.type")
            raw_variant = output.get("variant")
            normalized_outputs.append(
                {
                    "variant": safe_slug(raw_variant) if raw_variant else None,
                    "question": str(output.get("question") or question).strip(),
                    "instruction": str(output.get("instruction") or output.get("question") or instruction).strip(),
                    "gt": gt,
                    "answer": output.get("answer"),
                }
            )

        normalized.append(
            {
                "entry_id": entry_id,
                "task": task,
                "template_id": template_id,
                "question": question,
                "instruction": instruction,
                "stress": validate_stress(raw_entry.get("stress")),
                "outputs": normalized_outputs,
                "editor_state": editor_state,
            }
        )
    return normalized


def build_annotation_record(
    dataset: dict,
    item: dict,
    entry: dict,
    output: dict,
    updated_at: str,
) -> dict:
    suffix = f"_{output['variant']}" if output.get("variant") else ""
    sample_id = f"{item['sample_id']}__{entry['entry_id']}{suffix}"
    return {
        "sample_id": sample_id,
        "source_sample_id": item["sample_id"],
        "status": "annotated",
        "updated_at": updated_at,
        "annotation": {
            "sample_id": sample_id,
            "source_sample_id": item["sample_id"],
            "entry_id": entry["entry_id"],
            "task": entry["task"],
            "template_id": entry["template_id"],
            "question": entry["question"],
            "instruction": entry["instruction"],
            "answer": output.get("answer"),
            "rgb": item["rgb"],
            "gt": output["gt"],
            "output_variant": output.get("variant"),
            "output_question": output.get("question"),
            "output_instruction": output.get("instruction"),
            "editor_state": entry.get("editor_state") or {},
            "stress": entry["stress"],
            "provenance": {
                "dataset": dataset.get("provenance_dataset", dataset["title"]),
                "subcategory": item["subcategory"],
                "split": dataset.get("provenance_split", "self_collected"),
            },
        },
    }


def build_discard_record(dataset: dict, item: dict, updated_at: str) -> dict:
    sample_id = f"{item['sample_id']}__discard"
    return {
        "sample_id": sample_id,
        "source_sample_id": item["sample_id"],
        "status": "discarded",
        "updated_at": updated_at,
        "annotation": None,
        "provenance": {
            "dataset": dataset.get("provenance_dataset", dataset["title"]),
            "subcategory": item["subcategory"],
            "split": dataset.get("provenance_split", "self_collected"),
        },
    }


def entry_sort_key(entry_id: str) -> tuple:
    return natural_sort_key(entry_id)


def aggregate_sample_records(dataset: dict, item: dict, records: list[dict]) -> dict | None:
    if not records:
        return None

    annotated_records = [record for record in records if record.get("status") == "annotated" and isinstance(record.get("annotation"), dict)]
    discarded_records = [record for record in records if record.get("status") == "discarded"]
    updated_at = max((str(record.get("updated_at") or "") for record in records), default="") or None

    if annotated_records:
        grouped: dict[str, dict] = {}
        for record in annotated_records:
            annotation = record["annotation"]
            has_editor_state = isinstance(annotation.get("editor_state"), dict) and bool(annotation.get("editor_state"))
            has_question_payload = bool(annotation.get("task")) or bool(annotation.get("template_id")) or bool(annotation.get("gt"))
            if not has_editor_state and not has_question_payload:
                continue
            entry_id = safe_slug(annotation.get("entry_id")) or "legacy"
            bucket = grouped.setdefault(
                entry_id,
                {
                    "entry_id": entry_id,
                    "task": annotation.get("task"),
                    "template_id": annotation.get("template_id"),
                    "question": annotation.get("question"),
                    "instruction": annotation.get("instruction"),
                    "stress": annotation.get("stress") if isinstance(annotation.get("stress"), dict) else None,
                    "editor_state": annotation.get("editor_state") if isinstance(annotation.get("editor_state"), dict) else {},
                    "outputs": [],
                },
            )
            bucket["outputs"].append(
                {
                    "variant": annotation.get("output_variant"),
                    "question": annotation.get("output_question") or annotation.get("question"),
                    "instruction": annotation.get("output_instruction") or annotation.get("instruction"),
                    "gt": annotation.get("gt"),
                    "answer": annotation.get("answer"),
                    "sample_id": record.get("sample_id"),
                }
            )

        entries = [grouped[key] for key in sorted(grouped.keys(), key=entry_sort_key)]
        return {
            "sample_id": item["sample_id"],
            "source_sample_id": item["sample_id"],
            "status": "annotated",
            "updated_at": updated_at,
            "annotation": {
                "sample_id": item["sample_id"],
                "source_sample_id": item["sample_id"],
                "entries": entries,
                "rgb": item["rgb"],
                "provenance": {
                    "dataset": dataset.get("provenance_dataset", dataset["title"]),
                    "subcategory": item["subcategory"],
                    "split": dataset.get("provenance_split", "self_collected"),
                },
            },
        }

    if discarded_records:
        return {
            "sample_id": item["sample_id"],
            "source_sample_id": item["sample_id"],
            "status": "discarded",
            "updated_at": updated_at,
            "annotation": None,
        }

    return None


def load_sample_records(dataset: dict, item: dict) -> list[dict]:
    records: list[dict] = []
    for path in sample_record_paths(dataset, item):
        try:
            record = read_json(path)
        except Exception:
            continue
        if source_sample_id_from_record(record) == item["sample_id"]:
            records.append(record)
    return records


def validate_stress(payload: object) -> dict[str, list[str]]:
    if not isinstance(payload, dict):
        raise ValueError("stress must be an object")
    normalized: dict[str, list[str]] = {}
    for axis, choices in payload.items():
        if axis not in STRESS_OPTIONS:
            raise ValueError(f"Unknown stress axis: {axis}")
        if not isinstance(choices, list) or not choices:
            raise ValueError(f"Stress axis {axis} must have at least one choice")
        seen: list[str] = []
        for choice in choices:
            if choice not in STRESS_OPTIONS[axis]:
                raise ValueError(f"Unknown stress choice: {choice}")
            if choice not in seen:
                seen.append(choice)
        if seen:
            normalized[axis] = seen
    if not normalized:
        raise ValueError("At least one stress axis is required")
    return normalized


def build_record(dataset: dict, item: dict, status: str, stress: dict[str, list[str]] | None) -> dict:
    record = {
        "sample_id": item["sample_id"],
        "status": status,
        "updated_at": now_utc_iso(),
        "annotation": None,
    }
    if status == "annotated":
        record["annotation"] = {
            "sample_id": item["sample_id"],
            "task": item["task"],
            "stress": stress,
            "instruction": item["instruction"],
            "rgb": item["rgb"],
            "gt": item["gt"],
            "provenance": {
                "dataset": dataset.get("provenance_dataset", dataset["title"]),
                "subcategory": item["subcategory"],
                "split": dataset.get("provenance_split", "self_collected"),
            },
        }
    return record


def dataset_progress(dataset: dict) -> dict:
    items = dataset_rows(dataset)
    records = load_record_map(dataset)
    annotated = 0
    discarded = 0
    per_subcategory: dict[str, dict] = {}
    for item in items:
        subcategory = item["subcategory"]
        bucket = per_subcategory.setdefault(
            subcategory,
            {"total": 0, "annotated": 0, "discarded": 0, "processed": 0},
        )
        bucket["total"] += 1
        record = records.get(item["sample_id"])
        if not record:
            continue
        if record["status"] == "annotated":
            annotated += 1
            bucket["annotated"] += 1
        elif record["status"] == "discarded":
            discarded += 1
            bucket["discarded"] += 1
        bucket["processed"] += 1
    total = len(items)
    processed = annotated + discarded
    return {
        "dataset": dataset["id"],
        "total": total,
        "annotated": annotated,
        "discarded": discarded,
        "processed": processed,
        "annotated_ratio": (annotated / total) if total else 0.0,
        "processed_ratio": (processed / total) if total else 0.0,
        "per_subcategory": per_subcategory,
    }


def dataset_items(dataset: dict, subcategory: str | None) -> dict:
    if subcategory and subcategory not in dataset["subcategories"]:
        raise ValueError(f"Unknown subcategory: {subcategory}")
    items = dataset_rows(dataset)
    if subcategory:
        items = [item for item in items if item["subcategory"] == subcategory]
    records = load_record_map(dataset)
    return {
        "dataset": dataset["id"],
        "subcategory": subcategory,
        "scope": None,
        "items": [
            {
                **item,
                "status": records.get(item["sample_id"], {}).get("status", "unprocessed"),
                "updated_at": records.get(item["sample_id"], {}).get("updated_at"),
            }
            for item in items
        ],
    }


class AnnotationHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            super().do_GET()
            return
        try:
            payload = self.handle_get(parsed.path, parse_qs(parsed.query))
            self.send_json(payload)
        except KeyError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": f"Internal server error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_PUT(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.METHOD_NOT_ALLOWED, "PUT is only supported for /api/*")
            return
        try:
            payload = self.handle_put(parsed.path, self.read_body())
            self.send_json(payload)
        except KeyError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self.send_json({"error": f"Internal server error: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def handle_get(self, path: str, query: dict[str, list[str]]) -> dict:
        parts = [part for part in path.split("/") if part]
        if parts == ["api", "datasets"]:
            registry = parse_registry()
            datasets = []
            for dataset in registry["datasets"]:
                datasets.append(
                    {
                        "id": dataset["id"],
                        "title": dataset["title"],
                        "card_description": dataset["card_description"],
                        "source_manifest": dataset.get("source_dir", "."),
                        "subcategories": dataset["subcategories"],
                        "annotation_stage": dataset.get("annotation_stage"),
                        "annotation_entry_caps": None,
                        "scopes": [],
                        "progress": dataset_progress(dataset),
                    }
                )
            return {"datasets": datasets}

        if len(parts) < 4 or parts[0] != "api" or parts[1] != "datasets":
            raise KeyError("Unknown API path")

        dataset = get_dataset_config(parts[2])
        resource = parts[3]
        if resource == "progress":
            return dataset_progress(dataset)
        if resource == "items":
            subcategory = query.get("subcategory", [None])[0]
            return dataset_items(dataset, subcategory)
        if resource == "records" and len(parts) == 5:
            sample_id = parts[4]
            item = dataset_index(dataset).get(sample_id)
            if not item:
                raise KeyError(f"Unknown sample: {sample_id}")
            record = aggregate_sample_records(dataset, item, load_sample_records(dataset, item))
            return {"dataset": dataset["id"], "sample_id": sample_id, "record": record}
        if resource == "cursor" and len(parts) == 4:
            return {"dataset": dataset["id"], "cursor": read_cursor(dataset["id"])}
        raise KeyError("Unknown API path")

    def handle_put(self, path: str, body: dict) -> dict:
        parts = [part for part in path.split("/") if part]
        if len(parts) < 4 or parts[0] != "api" or parts[1] != "datasets":
            raise KeyError("Unknown API path")
        dataset = get_dataset_config(parts[2])
        resource = parts[3]

        if resource == "records" and len(parts) == 5:
            sample_id = parts[4]
            item = dataset_index(dataset).get(sample_id)
            if not item:
                raise KeyError(f"Unknown sample: {sample_id}")
            status = body.get("status")
            if status not in {"annotated", "discarded"}:
                raise ValueError("status must be annotated or discarded")
            remove_sample_records(dataset, item)
            updated_at = now_utc_iso()
            written_records: list[dict] = []
            if status == "annotated":
                entries = validate_entries(body.get("entries"))
                for entry in entries:
                    for output in entry["outputs"]:
                        record = build_annotation_record(dataset, item, entry, output, updated_at)
                        path = sample_record_dir(dataset, item) / f"{record['sample_id']}.json"
                        atomic_write_json(path, record)
                        written_records.append(record)
            else:
                record = build_discard_record(dataset, item, updated_at)
                path = sample_record_dir(dataset, item) / f"{record['sample_id']}.json"
                atomic_write_json(path, record)
                written_records.append(record)
            append_event(
                dataset["id"],
                {
                    "ts": updated_at,
                    "dataset": dataset["id"],
                    "sample_id": sample_id,
                    "action": "save" if status == "annotated" else "discard",
                    "status_after": status,
                    "entry_count": len(body.get("entries") or []) if status == "annotated" else 0,
                },
            )
            aggregate_record = aggregate_sample_records(dataset, item, written_records)
            return {
                "dataset": dataset["id"],
                "sample_id": sample_id,
                "record": aggregate_record,
                "progress": dataset_progress(dataset),
            }

        if resource == "cursor" and len(parts) == 4:
            sample_id = body.get("last_sample_id")
            item = dataset_index(dataset).get(sample_id)
            if not item:
                raise KeyError(f"Unknown sample: {sample_id}")
            cursor = {
                "dataset": dataset["id"],
                "subcategory": item["subcategory"],
                "scope_id": None,
                "last_sample_id": sample_id,
                "updated_at": now_utc_iso(),
            }
            atomic_write_json(cursor_path(dataset["id"]), cursor)
            append_event(
                dataset["id"],
                {
                    "ts": now_utc_iso(),
                    "dataset": dataset["id"],
                    "sample_id": sample_id,
                    "action": "cursor_update",
                },
            )
            return {"dataset": dataset["id"], "cursor": cursor}

        raise KeyError("Unknown API path")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local annotation server for Self-Collection")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8766, help="Bind port")
    args = parser.parse_args()

    with ThreadingHTTPServer((args.host, args.port), AnnotationHandler) as httpd:
        print(f"Serving annotation UI at http://{args.host}:{args.port}/annotation_index.html")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down...")


if __name__ == "__main__":
    main()
