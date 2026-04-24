function percent(numerator, denominator) {
  if (!denominator) {
    return "0%";
  }
  return `${Math.round((numerator / denominator) * 100)}%`;
}

function datasetUrl(datasetId, resume = false) {
  const params = new URLSearchParams({ dataset: datasetId });
  if (resume) {
    params.set("resume", "1");
  }
  return `annotation_dataset.html?${params.toString()}`;
}

function progressSummaryMarkup(progress, total, extraClass = "") {
  const className = ["unified-progress", extraClass].filter(Boolean).join(" ");
  return `
    <div class="${className}">
      <div class="progress-metrics">
        <div class="progress-metric">
          <span class="progress-metric-label">已处理</span>
          <strong class="progress-metric-value">${progress.processed} / ${total}</strong>
        </div>
        <div class="progress-metric annotated">
          <span class="progress-metric-label">已标注</span>
          <strong class="progress-metric-value">${progress.annotated}</strong>
        </div>
        <div class="progress-metric warn">
          <span class="progress-metric-label">已舍弃</span>
          <strong class="progress-metric-value">${progress.discarded}</strong>
        </div>
      </div>
      <div class="stacked-progress">
        <div class="stacked-progress-segment annotated" style="width:${percent(progress.annotated, total)}"></div>
        <div class="stacked-progress-segment discarded" style="width:${percent(progress.discarded, total)}"></div>
      </div>
    </div>
  `;
}

function subcategoryProgressMarkup(perSubcategory, orderedKeys = []) {
  const source = perSubcategory || {};
  const keys = orderedKeys.length
    ? orderedKeys.filter((key) => source[key])
    : Object.keys(source);
  const entries = keys.map((key) => [key, source[key]]);
  if (!entries.length) {
    return "";
  }
  return `
    <div class="subprogress-list">
      ${entries
        .map(
          ([name, progress]) => `
            <section class="subprogress-item">
              <div class="subprogress-name">${name}</div>
              ${progressSummaryMarkup(progress, progress.total, "compact-progress subcategory-progress")}
            </section>
          `
        )
        .join("")}
    </div>
  `;
}

function cardTemplate(dataset) {
  const capsNote = dataset.annotation_entry_caps
    ? `<p class="meta-text muted">本评测集在标注入口按 <code>annotation_entry_caps</code> 截断样本；总进度与子类进度均基于截断后的集合。</p>`
    : "";
  return `
    <article class="card">
      <div class="repo">Annotation Hub</div>
      <h2 class="title">${dataset.title}</h2>
      <p class="desc">${dataset.card_description}</p>
      ${capsNote}
      <p class="meta-text">总样本数：${dataset.progress.total}</p>

      <div class="progress-section">
        <div class="progress-section-title">整体进度</div>
        ${progressSummaryMarkup(dataset.progress, dataset.progress.total)}
      </div>

      <div class="progress-section subprogress-section">
        <div class="progress-section-title">子类进度</div>
        ${subcategoryProgressMarkup(dataset.progress.per_subcategory, dataset.subcategories)}
      </div>

      <div class="actions">
        <a class="button primary" href="${datasetUrl(dataset.id)}">进入标注</a>
        <a class="button" href="${datasetUrl(dataset.id, true)}">继续上次</a>
      </div>
    </article>
  `;
}

async function loadDatasets() {
  const statusLine = document.getElementById("statusLine");
  const cardGrid = document.getElementById("cardGrid");
  statusLine.textContent = "正在加载评测集状态...";
  try {
    const response = await fetch("/api/datasets");
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const payload = await response.json();
    const datasets = payload.datasets || [];
    if (!datasets.length) {
      statusLine.textContent = "当前没有可用的评测集。";
      return;
    }
    cardGrid.innerHTML = datasets.map(cardTemplate).join("");
    statusLine.textContent = "";
  } catch (error) {
    statusLine.textContent = `加载失败：${error.message}`;
    statusLine.classList.add("error");
  }
}

loadDatasets();
