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

const state = {
  dataset: null,
  items: [],
  currentIndex: 0,
  currentRecord: null,
  resumeMode: false,
  resumeCursor: null,
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

function setStatus(message, kind = "") {
  const line = document.getElementById("statusLine");
  line.textContent = message || "";
  line.className = "status-line action-status-line";
  if (kind) {
    line.classList.add(kind);
  }
}

function pendingText() {
  return "第二阶段待定";
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

function hasAnyAxisSelected() {
  return Array.from(document.querySelectorAll("[data-axis-option]")).some((option) => option.checked);
}

function collectStressSelection() {
  const stress = {};
  Object.keys(STRESS_CONFIG).forEach((axis) => {
    const selected = Array.from(document.querySelectorAll(`[data-axis-option="${axis}"]`))
      .filter((option) => option.checked)
      .map((option) => option.value);
    if (selected.length) {
      stress[axis] = selected;
    }
  });
  return stress;
}

function validateStress() {
  const stress = collectStressSelection();
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

function renderStressControls() {
  const root = document.getElementById("stressGrid");
  root.innerHTML = Object.entries(STRESS_CONFIG)
    .map(
      ([axis, config]) => `
        <section class="stress-group" data-axis="${axis}">
          <div class="axis-head">
            <label class="checkbox axis-label" for="axis-${axis}">
              <input
                id="axis-${axis}"
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
                    <input type="checkbox" data-axis-option="${axis}" value="${option.value}" />
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

  root.querySelectorAll("[data-axis-option]").forEach((option) => {
    option.addEventListener("change", () => {
      const axis = option.getAttribute("data-axis-option");
      syncAxisSelection(root, axis);
    });
  });

  Object.keys(STRESS_CONFIG).forEach((axis) => syncAxisSelection(root, axis));
}

function setStressSelection(stress) {
  const root = document.getElementById("stressGrid");
  root.querySelectorAll("[data-axis-option]").forEach((option) => {
    option.checked = false;
  });
  Object.keys(STRESS_CONFIG).forEach((axis) => syncAxisSelection(root, axis));

  if (!stress) {
    return;
  }

  Object.entries(stress).forEach(([axis, values]) => {
    root.querySelectorAll(`[data-axis-option="${axis}"]`).forEach((option) => {
      option.checked = values.includes(option.value);
    });
    syncAxisSelection(root, axis);
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

function renderOptionsList(item) {
  const optionsRoot = document.getElementById("optionsText");
  optionsRoot.replaceChildren();
  const options = item?.gt?.type === "mcq" ? item.gt.mcq?.options || [] : [];
  if (!options.length) {
    optionsRoot.textContent = pendingText();
    return;
  }
  options.forEach((option, index) => {
    const line = document.createElement("div");
    line.textContent = `option[${index}]：${option}`;
    optionsRoot.appendChild(line);
  });
}

function formatGtAnswer(item) {
  if (!item?.gt) {
    return pendingText();
  }
  if (item.gt.type === "mask") {
    return "mask 类 GT，无文本答案";
  }
  const options = item.gt.mcq?.options || [];
  const answerIndex = item.gt.mcq?.answer_index;
  if (Number.isInteger(answerIndex) && options[answerIndex]) {
    return options[answerIndex];
  }
  return item.answer || "-";
}

function clearOverlays() {
  const maskCanvas = document.getElementById("maskCanvas");
  const primaryBboxLayer = document.getElementById("primaryBboxLayer");
  const secondaryBboxLayer = document.getElementById("secondaryBboxLayer");
  const primaryBbox = document.getElementById("primaryBbox");
  const secondaryBbox = document.getElementById("secondaryBbox");

  maskCanvas.style.display = "none";
  primaryBboxLayer.replaceChildren();
  secondaryBboxLayer.replaceChildren();
  primaryBbox.style.display = "none";
  secondaryBbox.style.display = "none";
}

function clearView() {
  document.getElementById("sampleIdText").textContent = "-";
  document.getElementById("officialIdText").textContent = "-";
  document.getElementById("categoryLabelZhText").textContent = "-";
  document.getElementById("taskText").textContent = "-";
  document.getElementById("gtTypeText").textContent = "-";
  document.getElementById("gtAnswerText").textContent = "-";
  document.getElementById("optionsText").textContent = "-";
  document.getElementById("instructionText").textContent = "-";
  document.getElementById("questionText").textContent = "-";
  document.getElementById("rgbPathText").textContent = "-";
  document.getElementById("maskPathText").textContent = "-";
  document.getElementById("updatedAtText").textContent = "-";
  document.getElementById("primaryMediaTitle").textContent = "RGB";
  document.getElementById("secondaryMediaTitle").textContent = "RGB";
  document.getElementById("primaryImage").removeAttribute("src");
  document.getElementById("secondaryImage").removeAttribute("src");
  clearOverlays();
  setStressSelection(null);
}

function renderMeta(item, record) {
  document.getElementById("sampleIdText").textContent = item.sample_id;
  document.getElementById("officialIdText").textContent = item.official_id || item.source_filename || "-";
  document.getElementById("categoryLabelZhText").textContent = item.category_label_zh || "-";
  document.getElementById("taskText").textContent = item.task || pendingText();
  document.getElementById("gtTypeText").textContent = item?.gt?.type || pendingText();
  document.getElementById("gtAnswerText").textContent = formatGtAnswer(item);
  renderOptionsList(item);
  document.getElementById("instructionText").textContent = item.instruction || pendingText();
  document.getElementById("questionText").textContent = item.question || pendingText();
  document.getElementById("rgbPathText").textContent = item.rgb;
  document.getElementById("maskPathText").textContent = "-";
  document.getElementById("updatedAtText").textContent = record?.updated_at || "未保存";
}

function renderImages(item) {
  const primaryImage = document.getElementById("primaryImage");
  const secondaryImage = document.getElementById("secondaryImage");
  document.getElementById("primaryMediaTitle").textContent = "RGB";
  document.getElementById("secondaryMediaTitle").textContent = "RGB（对照）";
  clearOverlays();
  primaryImage.src = encodeURI(item.rgb);
  secondaryImage.src = encodeURI(item.rgb);
}

function updateNavButtons() {
  document.getElementById("prevButton").disabled = state.currentIndex <= 0;
  document.getElementById("nextButton").disabled = state.currentIndex >= state.items.length - 1;
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
    clearView();
    renderListSummary();
    renderStatusPill(null);
    updateNavButtons();
    setStatus("当前筛选范围内没有样本。", "error");
    return;
  }

  updateNavButtons();
  setStatus("");
  const record = await loadRecord(item.sample_id);
  state.currentRecord = record;
  renderStatusPill(record);
  renderListSummary();
  renderMeta(item, record);
  renderImages(item);
  setStressSelection(record?.status === "annotated" ? record.annotation.stress : null);
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
    let stress = null;
    if (status === "annotated") {
      stress = validateStress();
    }

    const payload = await apiFetch(`/api/datasets/${state.dataset.id}/records/${item.sample_id}`, {
      method: "PUT",
      body: JSON.stringify({ status, stress }),
    });

    const currentListItem = state.items.find((entry) => entry.sample_id === item.sample_id);
    if (currentListItem) {
      currentListItem.status = status;
      currentListItem.updated_at = payload.record.updated_at;
    }

    state.currentRecord = payload.record;
    renderStatusPill(payload.record);
    renderListSummary();
    renderMeta(item, payload.record);
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

async function handleSaveNext() {
  const record = state.currentRecord;
  if (record?.status === "discarded" && !hasAnyAxisSelected()) {
    const moved = await goToNextItem();
    setStatus(
      moved ? "当前样本已舍弃，已进入下一张。" : "当前样本已舍弃，已经是最后一张。",
      "ok"
    );
    return;
  }
  await saveRecord("annotated", { advanceAfterSave: true });
}

async function init() {
  renderStressControls();

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

  subcategorySelect.addEventListener("change", () => {
    loadItems({ preserveCurrent: true });
  });

  document.getElementById("prevButton").addEventListener("click", async () => {
    if (state.currentIndex > 0) {
      state.currentIndex -= 1;
      await renderCurrent();
    }
  });

  document.getElementById("nextButton").addEventListener("click", async () => {
    if (state.currentIndex < state.items.length - 1) {
      state.currentIndex += 1;
      await renderCurrent();
    }
  });

  document.getElementById("saveNextButton").addEventListener("click", async () => {
    await handleSaveNext();
  });

  document.getElementById("discardButton").addEventListener("click", async () => {
    await saveRecord("discarded", { advanceAfterSave: true });
  });

  await loadItems();
}

init().catch((error) => setStatus(error.message || String(error), "error"));
