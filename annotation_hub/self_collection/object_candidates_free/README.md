# Free Object Candidate Generation

This directory stores object candidates generated without paid APIs or private tokens.

Default pipeline:

```text
image
-> English indoor candidate vocabulary
-> local GroundingDINO detection
-> local SAM box-prompt segmentation
-> bbox + COCO RLE mask JSON
```

The current script is:

```bash
conda run -n self_data_obj python scripts/generate_object_candidates_grounded_sam.py \
  --subcategory Home_Office \
  --limit 1 \
  --overwrite
```

Outputs are written to:

```text
annotation_hub/self_collection/object_candidates_free/<subcategory>/<sample_id>.json
```

The default English candidate vocabulary is:

```text
object_label_vocab/indoor_objects.txt
```

This approach is free after model downloads, but it is not fully prompt-free: GroundingDINO needs candidate labels.
