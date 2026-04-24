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


def record_path(dataset: dict, sample_id: str) -> Path:
    item = dataset_index(dataset)[sample_id]
    return ROOT / dataset["record_root"] / item["subcategory"] / f"{sample_id}.json"


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
        sample_id = record.get("sample_id")
        if sample_id in index and record.get("status") in {"annotated", "discarded"}:
            result[sample_id] = record
    return result


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
            path = record_path(dataset, sample_id)
            record = read_json(path) if path.is_file() else None
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
            stress = validate_stress(body.get("stress")) if status == "annotated" else None
            record = build_record(dataset, item, status, stress)
            path = record_path(dataset, sample_id)
            atomic_write_json(path, record)
            append_event(
                dataset["id"],
                {
                    "ts": now_utc_iso(),
                    "dataset": dataset["id"],
                    "sample_id": sample_id,
                    "action": "save" if status == "annotated" else "discard",
                    "status_after": status,
                },
            )
            return {
                "dataset": dataset["id"],
                "sample_id": sample_id,
                "record": record,
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
