const STRESS_CONFIG = {
  M: {
    title: "Material / M",
    options: [
      { value: "dark_absorptive", desc: "深色吸光" },
      { value: "low_contrast_blend", desc: "低对比目标融入背景" },
      { value: "complex_texture", desc: "复杂高频纹理干扰" },
      { value: "transparent", desc: "透明材质" },
      { value: "specular_confusion", desc: "镜面高光混淆" },
    ],
  },
  V: {
    title: "Viewpoint / V",
    options: [
      { value: "extreme_viewpoint", desc: "极端视角" },
      { value: "truncated_out_of_frame", desc: "目标局部超出视野" },
      { value: "large_scale", desc: "目标特别大" },
      { value: "small_scale", desc: "目标特别小" },
    ],
  },
  G: {
    title: "Geometry / G",
    options: [
      { value: "occlusion", desc: "遮挡" },
      { value: "non_rigid_deform", desc: "非刚性形变" },
      { value: "stacked_layout", desc: "堆叠层级复杂" },
      { value: "cluttered_layout", desc: "杂乱拥挤布局" },
    ],
  },
  L: {
    title: "Lighting / L",
    options: [
      { value: "global_overexposure", desc: "整体过曝" },
      { value: "local_overexposure", desc: "局部过曝" },
      { value: "global_underexposure", desc: "整体欠曝" },
      { value: "local_underexposure", desc: "局部欠曝" },
    ],
  },
};

const TASK_OPTIONS = [
  { value: "target_grounding", label: "target_grounding: 找物体" },
  { value: "placement_grounding", label: "placement_grounding: 找可放置区域" },
  { value: "spatial_mcq", label: "spatial_mcq: 空间关系问答" },
];

const TEMPLATE_OPTIONS = {
  target_grounding: [
    { value: "target_object", label: "指定一个物体，自动生成 bbox / mask 两题" },
  ],
  placement_grounding: [
    { value: "vacant_space_yesno", label: "是否有空位可放置某个室内物品" },
  ],
  spatial_mcq: [
    { value: "relation", label: "模板一：A 相对于 B 的位置关系" },
    { value: "nearest", label: "模板二：哪个物体离我们最近" },
    { value: "farthest", label: "模板三：哪个物体离我们最远" },
  ],
};

const RELATION_OPTIONS = [
  "up",
  "down",
  "left",
  "right",
  "connect",
  "uncertain",
];

const PLACEMENT_ITEMS = [
  "book",
  "lamp",
  "mug",
  "monitor",
  "notebook",
  "plate",
  "pillow",
  "speaker",
  "toy",
  "vase",
];

const state = {
  dataset: null,
  items: [],
  currentIndex: 0,
  currentRecord: null,
  resumeMode: false,
  resumeCursor: null,
  candidateObjects: [],
  taskDrafts: [],
  draftCounter: 1,
  dirty: false,
};

function apiFetch(url, options = {}) {
  return fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  }).then(async (response) => {
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  });
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function queryDatasetId() {
  return new URLSearchParams(window.location.search).get("dataset");
}

function isResumeMode() {
  return new URLSearchParams(window.location.search).get("resume") === "1";
}

function currentItem() {
  return state.items[state.currentIndex] || null;
}

function setStatus(message, kind = "") {
  const line = document.getElementById("statusLine");
  line.textContent = message || "";
  line.className = "status-line action-status-line";
  if (kind) {
    line.classList.add(kind);
  }
}

function renderListSummary() {
  const total = state.items.length;
  const processed = state.items.filter((item) => item.status !== "unprocessed").length;
  const annotated = state.items.filter((item) => item.status === "annotated").length;
  const discarded = state.items.filter((item) => item.status === "discarded").length;
  document.getElementById("processedSummaryValue").textContent = `${processed} / ${total}`;
  document.getElementById("annotatedSummaryValue").textContent = `${annotated}`;
  document.getElementById("discardedSummaryValue").textContent = `${discarded}`;
  document.getElementById("annotatedSummaryBar").style.width = total ? `${(annotated / total) * 100}%` : "0%";
  document.getElementById("discardedSummaryBar").style.width = total ? `${(discarded / total) * 100}%` : "0%";
}

function collectStressSelection(root) {
  const stress = {};
  Object.keys(STRESS_CONFIG).forEach((axis) => {
    const selected = Array.from(root.querySelectorAll(`[data-axis-option="${axis}"]`))
      .filter((option) => option.checked)
      .map((option) => option.value);
    if (selected.length) {
      stress[axis] = selected;
    }
  });
  return stress;
}

function validateStress(stress) {
  if (!Object.keys(stress).length) {
    throw new Error("至少选择一个二级 stress。");
  }
  return stress;
}

function syncAxisSelection(root, axis) {
  const toggle = root.querySelector(`[data-axis-toggle="${axis}"]`);
  const group = root.querySelector(`.stress-group[data-axis="${axis}"]`);
  const enabled = Array.from(root.querySelectorAll(`[data-axis-option="${axis}"]`)).some(
    (option) => option.checked
  );
  if (toggle) {
    toggle.checked = enabled;
  }
  if (group) {
    group.classList.toggle("is-active", enabled);
  }
}

function renderStressControlsMarkup(stress = {}, entryId = "task") {
  return Object.entries(STRESS_CONFIG)
    .map(
      ([axis, config]) => `
        <section class="stress-group compact-stress-group" data-axis="${axis}">
          <div class="axis-head">
            <label class="checkbox axis-label" for="${entryId}-axis-${axis}">
              <input
                id="${entryId}-axis-${axis}"
                class="axis-toggle-indicator"
                type="checkbox"
                data-axis-toggle="${axis}"
                disabled
                tabindex="-1"
              />
              <strong>${config.title}</strong>
            </label>
          </div>
          <div class="suboptions">
            ${config.options
              .map(
                (option) => `
                  <label class="checkbox option-item">
                    <input
                      type="checkbox"
                      data-axis-option="${axis}"
                      value="${option.value}"
                      ${(stress?.[axis] || []).includes(option.value) ? "checked" : ""}
                    />
                    <span class="option-code">${option.value}</span>
                    <span class="option-desc">${option.desc}</span>
                  </label>
                `
              )
              .join("")}
          </div>
        </section>
      `
    )
    .join("");
}

function syncStressBlocks(root) {
  root.querySelectorAll(".stress-block").forEach((block) => {
    Object.keys(STRESS_CONFIG).forEach((axis) => syncAxisSelection(block, axis));
  });
}

function renderStatusPill(record) {
  const pill = document.getElementById("statusPill");
  const positionPill = document.getElementById("positionPill");
  const status = record?.status || currentItem()?.status || "unprocessed";
  const textMap = {
    unprocessed: "未处理",
    annotated: "已标注",
    discarded: "已舍弃",
  };
  const position = state.items.length ? `${state.currentIndex + 1} / ${state.items.length}` : "0 / 0";
  pill.textContent = `当前样本：${textMap[status] || status}`;
  pill.className = `pill ${status}`;
  positionPill.textContent = `当前位置：${position}`;
}

function clearOverlays() {
  const maskCanvas = document.getElementById("maskCanvas");
  const primaryBboxLayer = document.getElementById("primaryBboxLayer");
  const primaryBbox = document.getElementById("primaryBbox");
  maskCanvas.style.display = "none";
  primaryBboxLayer.replaceChildren();
  primaryBbox.style.display = "none";
}

function clearView() {
  document.getElementById("sampleIdText").textContent = "-";
  document.getElementById("officialIdText").textContent = "-";
  document.getElementById("categoryLabelZhText").textContent = "-";
  document.getElementById("candidateCountText").textContent = "-";
  document.getElementById("taskCountText").textContent = "-";
  document.getElementById("candidateSummaryText").textContent = "-";
  document.getElementById("rgbPathText").textContent = "-";
  document.getElementById("updatedAtText").textContent = "-";
  document.getElementById("taskList").innerHTML = "";
  document.getElementById("primaryMediaTitle").textContent = "RGB";
  document.getElementById("primaryImage").removeAttribute("src");
  clearOverlays();
}

function renderImages(item) {
  const primaryImage = document.getElementById("primaryImage");
  document.getElementById("primaryMediaTitle").textContent = "RGB";
  clearOverlays();
  primaryImage.src = encodeURI(item.rgb);
}

function updateNavButtons() {
  document.getElementById("prevButton").disabled = state.currentIndex <= 0;
  document.getElementById("nextButton").disabled = state.currentIndex >= state.items.length - 1;
  document.getElementById("undoAddTaskButton").disabled = !state.taskDrafts.some((draft) => draft.justAdded);
}

function updateTaskCount() {
  document.getElementById("taskCountText").textContent = `${state.taskDrafts.length}`;
}

function candidatePath(item) {
  return encodeURI(`annotation_hub/self_collection/object_candidates_free/${item.subcategory}/${item.sample_id}.json`);
}

async function loadCandidateObjects(item) {
  try {
    const response = await fetch(candidatePath(item), { cache: "no-store" });
    if (!response.ok) {
      return [];
    }
    const payload = await response.json();
    return Array.isArray(payload.objects)
      ? payload.objects.slice().sort((a, b) => (b.score || 0) - (a.score || 0))
      : [];
  } catch (_error) {
    return [];
  }
}

function objectDisplayName(object, index) {
  return `${object.name || "unknown"} (#${String(index + 1).padStart(2, "0")}, ${(object.score || 0).toFixed(2)})`;
}

function topObjects(limit) {
  return state.candidateObjects.slice(0, limit);
}

function findObject(objectId) {
  return state.candidateObjects.find((object) => object.object_id === objectId) || null;
}

function chooseRandomPlacementItem() {
  return PLACEMENT_ITEMS[Math.floor(Math.random() * PLACEMENT_ITEMS.length)];
}

function nextEntryId() {
  const existingNumbers = state.taskDrafts
    .map((draft) => Number((draft.entry_id || "").replace(/^q/, "")))
    .filter((value) => Number.isFinite(value));
  const maxValue = existingNumbers.length ? Math.max(...existingNumbers) : state.draftCounter - 1;
  const nextValue = maxValue + 1;
  state.draftCounter = nextValue + 1;
  return `q${String(nextValue).padStart(3, "0")}`;
}

function makeDraft(overrides = {}) {
  const draft = {
    entry_id: overrides.entry_id || nextEntryId(),
    task: overrides.task || "target_grounding",
    template_id: overrides.template_id || "target_object",
    stress: overrides.stress || {},
    selectedObjectId: overrides.selectedObjectId || "",
    placementItem: overrides.placementItem || chooseRandomPlacementItem(),
    answerIndex: Number.isInteger(overrides.answerIndex) ? overrides.answerIndex : null,
    spatialRelationAObjectId: overrides.spatialRelationAObjectId || "",
    spatialRelationALabel: overrides.spatialRelationALabel || "",
    spatialRelationBObjectId: overrides.spatialRelationBObjectId || "",
    spatialRelationBLabel: overrides.spatialRelationBLabel || "",
    spatialOptionTexts: Array.isArray(overrides.spatialOptionTexts) ? overrides.spatialOptionTexts.slice(0, 5) : [],
    justAdded: Boolean(overrides.justAdded),
  };
  ensureDraftDefaults(draft, { resetTemplateState: false });
  return draft;
}

function ensureDraftDefaults(draft, { resetTemplateState = false } = {}) {
  const templateValues = TEMPLATE_OPTIONS[draft.task].map((option) => option.value);
  if (!templateValues.includes(draft.template_id)) {
    draft.template_id = templateValues[0];
    resetTemplateState = true;
  }

  if (draft.task === "target_grounding") {
    if (!findObject(draft.selectedObjectId) && state.candidateObjects[0]) {
      draft.selectedObjectId = state.candidateObjects[0].object_id;
    }
    return draft;
  }

  if (draft.task === "placement_grounding") {
    if (!draft.placementItem || resetTemplateState) {
      draft.placementItem = chooseRandomPlacementItem();
    }
    if (![0, 1].includes(draft.answerIndex)) {
      draft.answerIndex = null;
    }
    return draft;
  }

  const top10 = topObjects(10);
  if (draft.template_id === "relation") {
    if (!findObject(draft.spatialRelationAObjectId) && top10[0]) {
      draft.spatialRelationAObjectId = top10[0].object_id;
    }
    if (!findObject(draft.spatialRelationBObjectId) && top10[1]) {
      draft.spatialRelationBObjectId = top10[1]?.object_id || top10[0]?.object_id || "";
    }
    if (!Number.isInteger(draft.answerIndex) || draft.answerIndex < 0 || draft.answerIndex >= RELATION_OPTIONS.length) {
      draft.answerIndex = null;
    }
    return draft;
  }

  const defaultOptionTexts = topObjects(5).map((object) => object.name || "unknown");
  if (resetTemplateState || draft.spatialOptionTexts.length !== defaultOptionTexts.length) {
    draft.spatialOptionTexts = defaultOptionTexts;
  } else {
    draft.spatialOptionTexts = draft.spatialOptionTexts.map((text, index) => text || defaultOptionTexts[index] || "");
  }
  if (!Number.isInteger(draft.answerIndex) || draft.answerIndex < 0 || draft.answerIndex >= draft.spatialOptionTexts.length) {
    draft.answerIndex = null;
  }
  return draft;
}

function createDraftFromSaved(entry) {
  const editorState = entry?.editor_state || {};
  return makeDraft({
    entry_id: entry?.entry_id || editorState.entry_id,
    task: editorState.task || entry?.task,
    template_id: editorState.template_id || entry?.template_id,
    stress: entry?.stress || editorState.stress || {},
    selectedObjectId: editorState.selectedObjectId,
    placementItem: editorState.placementItem,
    answerIndex: editorState.answerIndex,
    spatialRelationAObjectId: editorState.spatialRelationAObjectId,
    spatialRelationALabel: editorState.spatialRelationALabel,
    spatialRelationBObjectId: editorState.spatialRelationBObjectId,
    spatialRelationBLabel: editorState.spatialRelationBLabel,
    spatialOptionTexts: editorState.spatialOptionTexts,
    justAdded: false,
  });
}

function objectSelectMarkup(selectedObjectId, objects, fieldName) {
  return `
    <select data-field="${fieldName}">
      ${objects
        .map(
          (object, index) => `
            <option value="${escapeHtml(object.object_id)}" ${object.object_id === selectedObjectId ? "selected" : ""}>
              ${escapeHtml(objectDisplayName(object, index))}
            </option>
          `
        )
        .join("")}
    </select>
  `;
}

function fieldBlock(label, content) {
  return `
    <div class="field task-field">
      <label>${escapeHtml(label)}</label>
      ${content}
    </div>
  `;
}

function compileDraft(draft) {
  const errors = [];
  const editorState = {
    entry_id: draft.entry_id,
    task: draft.task,
    template_id: draft.template_id,
    stress: draft.stress,
    selectedObjectId: draft.selectedObjectId,
    placementItem: draft.placementItem,
    answerIndex: draft.answerIndex,
    spatialRelationAObjectId: draft.spatialRelationAObjectId,
    spatialRelationALabel: draft.spatialRelationALabel,
    spatialRelationBObjectId: draft.spatialRelationBObjectId,
    spatialRelationBLabel: draft.spatialRelationBLabel,
    spatialOptionTexts: draft.spatialOptionTexts,
  };
  let stress = {};
  try {
    stress = validateStress(draft.stress);
  } catch (error) {
    errors.push(error.message);
  }

  if (draft.task === "target_grounding") {
    const targetObject = findObject(draft.selectedObjectId);
    if (!targetObject) {
      errors.push("请选择一个已有物体。");
    }
    const objectName = targetObject?.name || "target";
    return {
      errors,
      preview: [
        `bbox: What is the bounding box of the ${objectName} in the image?`,
        `mask: What is the segmentation mask of the ${objectName} in the image?`,
      ],
      payload: targetObject
        ? {
            entry_id: draft.entry_id,
            task: draft.task,
            template_id: draft.template_id,
            question: `Locate the ${objectName} in the image.`,
            instruction: `Locate the ${objectName} in the image.`,
            stress,
            editor_state: editorState,
            outputs: [
              {
                variant: "bbox",
                question: `What is the bounding box of the ${objectName} in the image?`,
                instruction: `What is the bounding box of the ${objectName} in the image?`,
                gt: {
                  type: "bbox",
                  object_id: targetObject.object_id,
                  object_name: objectName,
                  bbox_xyxy: targetObject.bbox_xyxy,
                  bbox_xywh: targetObject.bbox_xywh,
                },
                answer: objectName,
              },
              {
                variant: "mask",
                question: `What is the segmentation mask of the ${objectName} in the image?`,
                instruction: `What is the segmentation mask of the ${objectName} in the image?`,
                gt: {
                  type: "mask",
                  object_id: targetObject.object_id,
                  object_name: objectName,
                  mask: targetObject.mask,
                  bbox_xyxy: targetObject.bbox_xyxy,
                },
                answer: objectName,
              },
            ],
          }
        : null,
    };
  }

  if (draft.task === "placement_grounding") {
    const placementItem = (draft.placementItem || "").trim();
    if (!placementItem) {
      errors.push("请先生成或填写要放置的室内物品。");
    }
    if (![0, 1].includes(draft.answerIndex)) {
      errors.push("请为 yes / no 选择 GT。");
    }
    const options = ["yes", "no"];
    const question = `Is there any vacant space to place a ${placementItem || "target object"} in the image?`;
    return {
      errors,
      preview: [question],
      payload: {
        entry_id: draft.entry_id,
        task: draft.task,
        template_id: draft.template_id,
        question,
        instruction: question,
        stress,
        editor_state: editorState,
        outputs: [
          {
            question,
            instruction: question,
            gt: {
              type: "mcq",
              mcq: {
                options,
                answer_index: draft.answerIndex,
              },
            },
            answer: Number.isInteger(draft.answerIndex) ? options[draft.answerIndex] : null,
          },
        ],
      },
    };
  }

  if (draft.template_id === "relation") {
    const objectA = findObject(draft.spatialRelationAObjectId);
    const objectB = findObject(draft.spatialRelationBObjectId);
    const labelA = (draft.spatialRelationALabel || "").trim() || objectA?.name || "";
    const labelB = (draft.spatialRelationBLabel || "").trim() || objectB?.name || "";
    if (!labelA) {
      errors.push("模板一需要物体 A。");
    }
    if (!labelB) {
      errors.push("模板一需要物体 B。");
    }
    if (!Number.isInteger(draft.answerIndex) || draft.answerIndex < 0 || draft.answerIndex >= RELATION_OPTIONS.length) {
      errors.push("请为空间关系选择 GT。");
    }
    const question = `What is the spatial relation of the ${labelA || "object A"} relative to the ${labelB || "object B"} in the image?`;
    return {
      errors,
      preview: [question],
      payload: {
        entry_id: draft.entry_id,
        task: draft.task,
        template_id: draft.template_id,
        question,
        instruction: question,
        stress,
        editor_state: editorState,
        outputs: [
          {
            question,
            instruction: question,
            gt: {
              type: "mcq",
              mcq: {
                options: RELATION_OPTIONS,
                answer_index: draft.answerIndex,
              },
              subject_a: {
                object_id: objectA?.object_id || null,
                label: labelA,
              },
              subject_b: {
                object_id: objectB?.object_id || null,
                label: labelB,
              },
            },
            answer: Number.isInteger(draft.answerIndex) ? RELATION_OPTIONS[draft.answerIndex] : null,
          },
        ],
      },
    };
  }

  const optionTexts = draft.spatialOptionTexts.map((text) => (text || "").trim());
  if (optionTexts.length !== 5 || optionTexts.some((text) => !text)) {
    errors.push("模板二/三需要 5 个有效选项。");
  }
  if (!Number.isInteger(draft.answerIndex) || draft.answerIndex < 0 || draft.answerIndex >= optionTexts.length) {
    errors.push("请为选项题选择 GT。");
  }
  const question =
    draft.template_id === "nearest"
      ? "Which object is closest to us in the image?"
      : "Which object is farthest from us in the image?";
  return {
    errors,
    preview: [question, ...optionTexts.map((text, index) => `option[${index + 1}]: ${text || "-"}`)],
    payload: {
      entry_id: draft.entry_id,
      task: draft.task,
      template_id: draft.template_id,
      question,
      instruction: question,
      stress,
      editor_state: editorState,
      outputs: [
        {
          question,
          instruction: question,
          gt: {
            type: "mcq",
            mcq: {
              options: optionTexts,
              answer_index: draft.answerIndex,
            },
          },
          answer: Number.isInteger(draft.answerIndex) ? optionTexts[draft.answerIndex] : null,
        },
      ],
    },
  };
}

function previewMarkup(lines) {
  return lines.map((line) => `<div>${escapeHtml(line)}</div>`).join("");
}

function errorsMarkup(errors) {
  if (!errors.length) {
    return `<div class="task-validation ok">当前任务配置完整，可保存。</div>`;
  }
  return `<div class="task-validation error">${errors.map((error) => escapeHtml(error)).join("；")}</div>`;
}

function renderTaskCard(draft, index) {
  const compiled = compileDraft(draft);
  const taskOptions = TASK_OPTIONS.map(
    (option) => `
      <option value="${option.value}" ${option.value === draft.task ? "selected" : ""}>${escapeHtml(option.label)}</option>
    `
  ).join("");
  const templateOptions = TEMPLATE_OPTIONS[draft.task]
    .map(
      (option) => `
        <option value="${option.value}" ${option.value === draft.template_id ? "selected" : ""}>${escapeHtml(option.label)}</option>
      `
    )
    .join("");
  const top10 = topObjects(10);
  const top5 = topObjects(5);

  let bodyMarkup = "";
  if (draft.task === "target_grounding") {
    bodyMarkup = `
      <div class="task-grid">
        ${fieldBlock(
          "选择目标物体",
          state.candidateObjects.length
            ? objectSelectMarkup(draft.selectedObjectId, state.candidateObjects, "selectedObjectId")
            : `<div class="task-empty-note">当前图片没有读取到候选物体。</div>`
        )}
      </div>
      <div class="task-note">保存后会自动写出两个文件：一个 bbox 题，一个 mask 题。</div>
    `;
  } else if (draft.task === "placement_grounding") {
    bodyMarkup = `
      <div class="task-grid">
        ${fieldBlock(
          "待放置室内物品",
          `
            <div class="inline-field">
              <input data-field="placementItem" type="text" value="${escapeHtml(draft.placementItem)}" />
              <button type="button" data-action="reroll-placement-item">随机一个</button>
            </div>
          `
        )}
        ${fieldBlock(
          "GT",
          `
            <div class="radio-row">
              ${["yes", "no"]
                .map(
                  (option, answerIndex) => `
                    <label class="checkbox">
                      <input
                        type="radio"
                        name="placement-answer-${draft.entry_id}"
                        value="${answerIndex}"
                        data-field="answerIndex"
                        ${draft.answerIndex === answerIndex ? "checked" : ""}
                      />
                      <span>${option}</span>
                    </label>
                  `
                )
                .join("")}
            </div>
          `
        )}
      </div>
    `;
  } else if (draft.template_id === "relation") {
    bodyMarkup = `
      <div class="task-grid two-up">
        ${fieldBlock(
          "物体 A（前 10 个候选）",
          top10.length ? objectSelectMarkup(draft.spatialRelationAObjectId, top10, "spatialRelationAObjectId") : `<div class="task-empty-note">当前图片没有可用候选。</div>`
        )}
        ${fieldBlock(
          "物体 A 手动改写",
          `<input data-field="spatialRelationALabel" type="text" value="${escapeHtml(draft.spatialRelationALabel)}" placeholder="留空则使用选择的物体名" />`
        )}
        ${fieldBlock(
          "物体 B（前 10 个候选）",
          top10.length ? objectSelectMarkup(draft.spatialRelationBObjectId, top10, "spatialRelationBObjectId") : `<div class="task-empty-note">当前图片没有可用候选。</div>`
        )}
        ${fieldBlock(
          "物体 B 手动改写",
          `<input data-field="spatialRelationBLabel" type="text" value="${escapeHtml(draft.spatialRelationBLabel)}" placeholder="留空则使用选择的物体名" />`
        )}
      </div>
      ${fieldBlock(
        "GT",
        `
          <div class="radio-row">
            ${RELATION_OPTIONS.map(
              (option, answerIndex) => `
                <label class="checkbox">
                  <input
                    type="radio"
                    name="relation-answer-${draft.entry_id}"
                    value="${answerIndex}"
                    data-field="answerIndex"
                    ${draft.answerIndex === answerIndex ? "checked" : ""}
                  />
                  <span>${escapeHtml(option)}</span>
                </label>
              `
            ).join("")}
          </div>
        `
      )}
    `;
  } else {
    bodyMarkup = `
      <div class="task-grid">
        ${fieldBlock(
          "5 个候选选项（默认取最高置信前 5 个，可改写）",
          `
            <div class="option-edit-list">
              ${Array.from({ length: 5 })
                .map((_, index) => {
                  const fallback = top5[index]?.name || "";
                  const value = draft.spatialOptionTexts[index] || fallback;
                  return `
                    <div class="option-edit-row">
                      <span class="option-edit-label">option ${index + 1}</span>
                      <input
                        type="text"
                        data-field="spatialOptionText"
                        data-option-index="${index}"
                        value="${escapeHtml(value)}"
                      />
                    </div>
                  `;
                })
                .join("")}
            </div>
          `
        )}
        ${fieldBlock(
          "GT",
          `
            <div class="radio-row vertical">
              ${Array.from({ length: 5 })
                .map((_, answerIndex) => {
                  const label = draft.spatialOptionTexts[answerIndex] || top5[answerIndex]?.name || `option ${answerIndex + 1}`;
                  return `
                    <label class="checkbox">
                      <input
                        type="radio"
                        name="mcq-answer-${draft.entry_id}"
                        value="${answerIndex}"
                        data-field="answerIndex"
                        ${draft.answerIndex === answerIndex ? "checked" : ""}
                      />
                      <span>${escapeHtml(label)}</span>
                    </label>
                  `;
                })
                .join("")}
            </div>
          `
        )}
      </div>
    `;
  }

  return `
    <section class="task-card" data-entry-id="${escapeHtml(draft.entry_id)}">
      <div class="task-card-head">
        <div>
          <div class="task-card-title">任务 ${index + 1}</div>
          <div class="task-card-meta">${escapeHtml(draft.entry_id)}</div>
        </div>
        ${draft.justAdded ? `<span class="pill position">新添加</span>` : ""}
      </div>
      <div class="task-grid two-up">
        ${fieldBlock("任务类型", `<select data-field="task">${taskOptions}</select>`)}
        ${fieldBlock("模板类型", `<select data-field="template_id">${templateOptions}</select>`)}
      </div>
      ${bodyMarkup}
      <div class="task-stress-panel">
        <div class="task-preview-title">Stress 标注（当前任务独立）</div>
        <div class="task-note">直接勾选二级 stress；一级 stress 只作为状态提示。</div>
        <div class="stress-grid compact-stress-grid stress-block" data-field="stress">
          ${renderStressControlsMarkup(draft.stress, draft.entry_id)}
        </div>
      </div>
      <div class="task-preview">
        <div class="task-preview-title">生成预览</div>
        <div class="info-multiline">${previewMarkup(compiled.preview)}</div>
      </div>
      ${errorsMarkup(compiled.errors)}
    </section>
  `;
}

function renderTaskList() {
  const root = document.getElementById("taskList");
  if (!state.taskDrafts.length) {
    root.innerHTML = `<div class="task-empty-state">当前图片还没有任务，点击“添加任务”开始出题。</div>`;
    updateTaskCount();
    updateNavButtons();
    return;
  }
  root.innerHTML = state.taskDrafts.map(renderTaskCard).join("");
  syncStressBlocks(root);
  updateTaskCount();
  updateNavButtons();
}

function renderMeta(item, record) {
  const candidateSummary = topObjects(8).map((object, index) => objectDisplayName(object, index));
  document.getElementById("sampleIdText").textContent = item.sample_id;
  document.getElementById("officialIdText").textContent = item.official_id || item.source_filename || "-";
  document.getElementById("categoryLabelZhText").textContent = item.category_label_zh || "-";
  document.getElementById("candidateCountText").textContent = `${state.candidateObjects.length}`;
  document.getElementById("candidateSummaryText").replaceChildren(
    ...candidateSummary.map((line) => {
      const node = document.createElement("div");
      node.textContent = line;
      return node;
    })
  );
  if (!candidateSummary.length) {
    document.getElementById("candidateSummaryText").textContent = "未找到对象候选文件或候选为空。";
  }
  document.getElementById("rgbPathText").textContent = item.rgb;
  document.getElementById("updatedAtText").textContent = record?.updated_at || "未保存";
  updateTaskCount();
}

function syncDraftsFromDom() {
  const root = document.getElementById("taskList");
  if (!root) {
    return;
  }
  state.taskDrafts.forEach((draft) => {
    const card = root.querySelector(`[data-entry-id="${CSS.escape(draft.entry_id)}"]`);
    if (!card) {
      return;
    }
    const taskField = card.querySelector('[data-field="task"]');
    const templateField = card.querySelector('[data-field="template_id"]');
    if (taskField) {
      draft.task = taskField.value;
    }
    if (templateField) {
      draft.template_id = templateField.value;
    }
    const selectedObjectField = card.querySelector('[data-field="selectedObjectId"]');
    if (selectedObjectField) {
      draft.selectedObjectId = selectedObjectField.value;
    }
    const placementItemField = card.querySelector('[data-field="placementItem"]');
    if (placementItemField) {
      draft.placementItem = placementItemField.value;
    }
    const answerField = card.querySelector('[data-field="answerIndex"]:checked');
    draft.answerIndex = answerField ? Number(answerField.value) : null;
    const relationAField = card.querySelector('[data-field="spatialRelationAObjectId"]');
    if (relationAField) {
      draft.spatialRelationAObjectId = relationAField.value;
    }
    const relationALabelField = card.querySelector('[data-field="spatialRelationALabel"]');
    if (relationALabelField) {
      draft.spatialRelationALabel = relationALabelField.value;
    }
    const relationBField = card.querySelector('[data-field="spatialRelationBObjectId"]');
    if (relationBField) {
      draft.spatialRelationBObjectId = relationBField.value;
    }
    const relationBLabelField = card.querySelector('[data-field="spatialRelationBLabel"]');
    if (relationBLabelField) {
      draft.spatialRelationBLabel = relationBLabelField.value;
    }
    const optionInputs = Array.from(card.querySelectorAll('[data-field="spatialOptionText"]'));
    if (optionInputs.length) {
      draft.spatialOptionTexts = optionInputs.map((input) => input.value);
    }
    const stressBlock = card.querySelector('.stress-block[data-field="stress"]');
    draft.stress = stressBlock ? collectStressSelection(stressBlock) : {};
    ensureDraftDefaults(draft, { resetTemplateState: false });
  });
}

function markDirty() {
  state.dirty = true;
}

function maybeConfirmNavigation() {
  syncDraftsFromDom();
  if (!state.dirty) {
    return true;
  }
  return window.confirm("当前图片有未保存修改，确定切换吗？");
}

async function updateCursor(item) {
  await apiFetch(`/api/datasets/${state.dataset.id}/cursor`, {
    method: "PUT",
    body: JSON.stringify({
      last_sample_id: item.sample_id,
      subcategory: item.subcategory,
      scope_id: null,
    }),
  });
}

async function loadRecord(sampleId) {
  const payload = await apiFetch(`/api/datasets/${state.dataset.id}/records/${sampleId}`);
  return payload.record;
}

async function renderCurrent() {
  const item = currentItem();
  if (!item) {
    state.currentRecord = null;
    state.candidateObjects = [];
    state.taskDrafts = [];
    clearView();
    renderListSummary();
    renderStatusPill(null);
    updateNavButtons();
    setStatus("当前筛选范围内没有样本。", "error");
    return;
  }

  updateNavButtons();
  setStatus("");
  const [record, candidateObjects] = await Promise.all([loadRecord(item.sample_id), loadCandidateObjects(item)]);
  state.currentRecord = record;
  state.candidateObjects = candidateObjects;
  state.taskDrafts =
    record?.status === "annotated" && Array.isArray(record.annotation?.entries)
      ? record.annotation.entries.map((entry) => createDraftFromSaved(entry))
      : [];
  state.draftCounter =
    state.taskDrafts
      .map((draft) => Number((draft.entry_id || "").replace(/^q/, "")))
      .filter((value) => Number.isFinite(value))
      .reduce((maxValue, value) => Math.max(maxValue, value), 0) + 1;
  renderStatusPill(record);
  renderListSummary();
  renderMeta(item, record);
  renderImages(item);
  renderTaskList();
  state.dirty = false;
}

function chooseIndex(items, preferredSampleId = null) {
  if (!items.length) {
    return 0;
  }
  if (preferredSampleId) {
    const preferredIndex = items.findIndex((item) => item.sample_id === preferredSampleId);
    if (preferredIndex >= 0 && items[preferredIndex].status === "unprocessed") {
      return preferredIndex;
    }
  }
  const firstUnprocessed = items.findIndex((item) => item.status === "unprocessed");
  if (firstUnprocessed >= 0) {
    return firstUnprocessed;
  }
  return items.length - 1;
}

async function loadItems({ preserveCurrent = false } = {}) {
  const subcategory = document.getElementById("subcategorySelect").value;
  const query = new URLSearchParams();
  if (subcategory) {
    query.set("subcategory", subcategory);
  }
  const payload = await apiFetch(`/api/datasets/${state.dataset.id}/items?${query.toString()}`);
  const previousSampleId = preserveCurrent ? currentItem()?.sample_id : null;
  state.items = payload.items || [];

  if (state.resumeMode) {
    state.currentIndex = chooseIndex(state.items, state.resumeCursor?.last_sample_id || null);
    state.resumeMode = false;
  } else if (preserveCurrent && previousSampleId) {
    const currentIndex = state.items.findIndex((item) => item.sample_id === previousSampleId);
    state.currentIndex = currentIndex >= 0 ? currentIndex : chooseIndex(state.items);
  } else {
    state.currentIndex = chooseIndex(state.items);
  }

  renderListSummary();
  await renderCurrent();
}

async function goToNextItem() {
  if (state.currentIndex < state.items.length - 1) {
    state.currentIndex += 1;
    await renderCurrent();
    return true;
  }
  await renderCurrent();
  return false;
}

async function saveRecord(status, { advanceAfterSave = false } = {}) {
  const item = currentItem();
  if (!item) {
    return;
  }
  try {
    syncDraftsFromDom();
    let entries = [];
    if (status === "annotated") {
      entries = state.taskDrafts.map((draft) => compileDraft(draft));
      const errors = entries.flatMap((entry, index) => entry.errors.map((error) => `任务 ${index + 1}: ${error}`));
      if (!entries.length) {
        throw new Error("当前图片还没有任务，请先点击“添加任务”。");
      }
      if (errors.length) {
        throw new Error(errors.join("；"));
      }
    }

    const payload = await apiFetch(`/api/datasets/${state.dataset.id}/records/${item.sample_id}`, {
      method: "PUT",
      body: JSON.stringify({
        status,
        entries: entries.map((entry) => entry.payload),
      }),
    });

    const currentListItem = state.items.find((entry) => entry.sample_id === item.sample_id);
    if (currentListItem) {
      currentListItem.status = status;
      currentListItem.updated_at = payload.record?.updated_at || null;
    }

    state.currentRecord = payload.record;
    if (status === "annotated") {
      state.taskDrafts = state.taskDrafts.map((draft) => ({ ...draft, justAdded: false }));
    } else {
      state.taskDrafts = [];
    }
    state.dirty = false;
    renderStatusPill(payload.record);
    renderListSummary();
    renderMeta(item, payload.record);
    renderTaskList();
    await updateCursor(item);

    if (advanceAfterSave) {
      const moved = await goToNextItem();
      setStatus(
        status === "annotated"
          ? moved
            ? "已保存，已进入下一张。"
            : "已保存，当前已经是最后一张。"
          : moved
            ? "已舍弃，已进入下一张。"
            : "已舍弃，当前已经是最后一张。",
        "ok"
      );
      return;
    }
    setStatus(status === "annotated" ? "已保存。" : "已舍弃。", "ok");
  } catch (error) {
    setStatus(error.message, "error");
  }
}

function addTaskDraft() {
  syncDraftsFromDom();
  const draft = makeDraft({ justAdded: true });
  state.taskDrafts.push(draft);
  state.dirty = true;
  renderTaskList();
}

function undoAddTaskDraft() {
  syncDraftsFromDom();
  const index = [...state.taskDrafts].map((draft, currentIndex) => ({ draft, currentIndex })).reverse().find(({ draft }) => draft.justAdded)?.currentIndex;
  if (!Number.isInteger(index)) {
    setStatus("当前没有可回退的新任务。", "error");
    return;
  }
  state.taskDrafts.splice(index, 1);
  state.dirty = true;
  renderTaskList();
  setStatus("已回退最后一次添加。", "ok");
}

function bindTaskEvents() {
  const taskList = document.getElementById("taskList");
  taskList.addEventListener("click", (event) => {
    const button = event.target.closest("button");
    if (!button) {
      return;
    }
    if (button.dataset.action === "reroll-placement-item") {
      syncDraftsFromDom();
      const card = button.closest("[data-entry-id]");
      const draft = state.taskDrafts.find((entry) => entry.entry_id === card?.dataset.entryId);
      if (!draft) {
        return;
      }
      draft.placementItem = chooseRandomPlacementItem();
      draft.justAdded = draft.justAdded || !state.currentRecord;
      state.dirty = true;
      renderTaskList();
    }
  });

  taskList.addEventListener("change", (event) => {
    const field = event.target.closest("[data-field]");
    if (!field) {
      return;
    }
    syncDraftsFromDom();
    const card = event.target.closest("[data-entry-id]");
    const draft = state.taskDrafts.find((entry) => entry.entry_id === card?.dataset.entryId);
    if (!draft) {
      return;
    }
    if (field.dataset.field === "task") {
      ensureDraftDefaults(draft, { resetTemplateState: true });
      renderTaskList();
    } else if (field.dataset.field === "template_id") {
      ensureDraftDefaults(draft, { resetTemplateState: true });
      renderTaskList();
    } else {
      ensureDraftDefaults(draft, { resetTemplateState: false });
      renderTaskList();
    }
    markDirty();
  });

  taskList.addEventListener("input", (event) => {
    if (event.target.closest("[data-field]")) {
      markDirty();
    }
  });
}

async function init() {
  bindTaskEvents();

  const datasetId = queryDatasetId();
  if (!datasetId) {
    setStatus("缺少 dataset 参数。", "error");
    return;
  }

  const payload = await apiFetch("/api/datasets");
  const dataset = (payload.datasets || []).find((entry) => entry.id === datasetId);
  if (!dataset) {
    setStatus(`找不到数据集：${datasetId}`, "error");
    return;
  }

  state.dataset = dataset;
  document.title = `${dataset.title} 标注页`;
  document.getElementById("datasetTitle").textContent = dataset.title;
  document.getElementById("datasetDesc").textContent = dataset.card_description;
  renderListSummary();

  const subcategorySelect = document.getElementById("subcategorySelect");
  dataset.subcategories.forEach((subcategory) => {
    const option = document.createElement("option");
    option.value = subcategory;
    option.textContent = subcategory;
    subcategorySelect.appendChild(option);
  });

  document.getElementById("scopeField").classList.add("hidden");
  state.resumeMode = isResumeMode();
  if (state.resumeMode) {
    const cursorPayload = await apiFetch(`/api/datasets/${dataset.id}/cursor`);
    state.resumeCursor = cursorPayload.cursor;
    if (state.resumeCursor?.subcategory && dataset.subcategories.includes(state.resumeCursor.subcategory)) {
      subcategorySelect.value = state.resumeCursor.subcategory;
    }
  } else {
    subcategorySelect.value = dataset.subcategories[0];
  }

  subcategorySelect.addEventListener("change", async () => {
    if (!maybeConfirmNavigation()) {
      subcategorySelect.value = currentItem()?.subcategory || subcategorySelect.value;
      return;
    }
    await loadItems({ preserveCurrent: true });
  });

  document.getElementById("prevButton").addEventListener("click", async () => {
    if (state.currentIndex <= 0 || !maybeConfirmNavigation()) {
      return;
    }
    state.currentIndex -= 1;
    await renderCurrent();
  });

  document.getElementById("nextButton").addEventListener("click", async () => {
    if (state.currentIndex >= state.items.length - 1 || !maybeConfirmNavigation()) {
      return;
    }
    state.currentIndex += 1;
    await renderCurrent();
  });

  document.getElementById("saveNextButton").addEventListener("click", async () => {
    await saveRecord("annotated", { advanceAfterSave: true });
  });

  document.getElementById("discardButton").addEventListener("click", async () => {
    if (!window.confirm("确定将当前图片标记为舍弃并删除已保存的问题吗？")) {
      return;
    }
    await saveRecord("discarded", { advanceAfterSave: true });
  });

  document.getElementById("addTaskButton").addEventListener("click", () => {
    addTaskDraft();
  });

  document.getElementById("undoAddTaskButton").addEventListener("click", () => {
    undoAddTaskDraft();
  });

  await loadItems();
}

init().catch((error) => setStatus(error.message || String(error), "error"));
