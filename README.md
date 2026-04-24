# Self-Collection Stress Annotation

这是自标数据的第一阶段人工标注工具，目录内独立运行，不依赖修改 `VLM-Bench`。

当前阶段只标注 stress。`task`、`instruction`、`gt`、问题和选项字段已经在接口里预留，现阶段写入为 `null` 或空数组；第二阶段确定任务类型后，可以在同一套 API 和 JSON 结构上继续扩展。

## 启动

在 `self_data` 目录运行：

```bash
python annotation_server.py
```

然后打开：

```text
http://127.0.0.1:8766/annotation_index.html
```

如果 8766 被占用，可以换端口：

```bash
python annotation_server.py --port 8770
```

## 标注流程

1. 在入口页查看整体进度和每个子类进度。
2. 点击 `进入标注` 开始，或点击 `继续上次` 回到上次保存的位置。
3. 在标注页选择子类。
4. 对当前图片勾选一个或多个二级 stress。一级 M/V/G/L 会自动点亮，只作提示。
5. 点击 `保存并下一张` 写入 JSON 并跳到下一张。
6. 不需要纳入的数据点击 `舍弃并下一张`。
7. `上一张` / `下一张` 只翻页，不自动保存。

## Stress 选项

和 `VLM-Bench` 现有工具保持一致：

- `M`: `dark_absorptive`, `low_contrast_blend`, `complex_texture`, `transparent`, `specular_confusion`
- `V`: `extreme_viewpoint`, `truncated_out_of_frame`, `large_scale`, `small_scale`
- `G`: `occlusion`, `non_rigid_deform`, `stacked_layout`, `cluttered_layout`
- `L`: `global_overexposure`, `local_overexposure`, `global_underexposure`, `local_underexposure`

后端会校验 stress 名称；未知 axis 或未知 stress 会被拒绝。

## 输出位置

正式标注结果写到：

```text
annotation_hub/self_collection/records/<subcategory>/<sample_id>.json
```

续标游标和事件日志写到：

```text
.annotation_state/self_collection/
```

这些状态文件只用于本地工具恢复位置和排查操作历史。

## 输出 JSON

保存一张图片后，JSON 结构如下：

```json
{
  "sample_id": "self_collection_Home_Office_0_9f956b38",
  "status": "annotated",
  "updated_at": "2026-04-24T07:04:15Z",
  "annotation": {
    "sample_id": "self_collection_Home_Office_0_9f956b38",
    "task": null,
    "stress": {
      "V": ["truncated_out_of_frame"]
    },
    "instruction": null,
    "rgb": "Home_Office/0.jpg",
    "gt": null,
    "provenance": {
      "dataset": "Self-Collection",
      "subcategory": "Home_Office",
      "split": "self_collected"
    }
  }
}
```

`sample_id` 使用 `self_collection_<subcategory>_<filename_stem>_<hash>` 格式，避免和 benchmark 样本 ID 重合。

## API 速查

- `GET /api/datasets`: 数据集列表、总进度、子类统计。
- `GET /api/datasets/self_collection/progress`: 当前进度统计。
- `GET /api/datasets/self_collection/items?subcategory=Home_Office`: 子类样本列表。
- `GET /api/datasets/self_collection/records/<sample_id>`: 读取单个样本 record。
- `PUT /api/datasets/self_collection/records/<sample_id>`: 保存标注或舍弃。
- `GET /api/datasets/self_collection/cursor`: 读取续标位置。
- `PUT /api/datasets/self_collection/cursor`: 保存续标位置。

保存标注请求示例：

```bash
curl -X PUT http://127.0.0.1:8766/api/datasets/self_collection/records/self_collection_Home_Office_0_9f956b38 \
  -H 'Content-Type: application/json' \
  --data '{"status":"annotated","stress":{"V":["truncated_out_of_frame"]}}'
```

舍弃请求示例：

```bash
curl -X PUT http://127.0.0.1:8766/api/datasets/self_collection/records/self_collection_Home_Office_0_9f956b38 \
  -H 'Content-Type: application/json' \
  --data '{"status":"discarded"}'
```

## 第二阶段扩展点

当前 `items` 接口已经返回 `task`, `instruction`, `question`, `answer`, `gt`, `annotation_entries` 字段。第二阶段如果一张图对应多个 task / instruction / gt，建议把多任务数据填进 `annotation_entries`，同时保留当前单任务字段用于兼容已有 JSON 格式。
