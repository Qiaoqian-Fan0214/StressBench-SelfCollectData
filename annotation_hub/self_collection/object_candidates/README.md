# Object Candidate Generation

This directory stores second-stage auto-generated object candidate JSON files.

The stress annotations remain in:

```text
annotation_hub/self_collection/records/<subcategory>/<sample_id>.json
```

Object candidates are written separately to:

```text
annotation_hub/self_collection/object_candidates/<subcategory>/<sample_id>.json
```

Each output file contains one image's candidate objects, normalized object name, bbox, score, and mask. Masks are stored as COCO RLE JSON so they remain compact and can be decoded with `pycocotools.mask.decode`.

## Recommended Pipeline

```text
image
-> DINO-X prompt-free candidate discovery
-> optional label normalization
-> bbox output
-> DINO-X mask output in COCO RLE
```

DINO-X currently supports prompt-free detection and segmentation through `dds-cloudapi-sdk` V2. This is the quickest path because it returns both `bbox` and `mask` from one API call. Local Grounded-SAM or Grounded-SAM-2 can be added later as an offline fallback, but it requires PyTorch, CUDA compilation, GroundingDINO checkpoints, and SAM/SAM2 checkpoints.

## Environment

Use a Python 3.10 environment:

```bash
conda create -n self_data_obj python=3.10 -y
conda run -n self_data_obj pip install -r requirements-object-candidates.txt
```

Set your DINO-X token:

```bash
export DINOX_API_TOKEN="..."
```

The script also accepts `DDS_API_TOKEN` and `DEEPDATASPACE_API_TOKEN`.

## Dry Run

```bash
conda run -n self_data_obj python scripts/generate_object_candidates_dinox.py --limit 3 --dry-run
```

## Run One Image

```bash
conda run -n self_data_obj python scripts/generate_object_candidates_dinox.py \
  --subcategory Home_Office \
  --limit 1 \
  --overwrite
```

## Optional Label Map

If you have a canonical label system, create a JSON file such as:

```json
{
  "laptop computer": "laptop",
  "computer mouse": "mouse",
  "cellphone": "phone"
}
```

Then pass:

```bash
--label-map path/to/label_map.json
```
