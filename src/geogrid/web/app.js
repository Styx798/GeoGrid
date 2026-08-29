/* global Konva */
"use strict";

const ui = {
  acceptedNodes: document.querySelector("#accepted-node-count"),
  pendingCount: document.querySelector("#pending-count"),
  regionProgress: document.querySelector("#region-progress"),
  saveState: document.querySelector("#save-state"),
  undo: document.querySelector("#undo-button"),
  redo: document.querySelector("#redo-button"),
  finalReview: document.querySelector("#final-review-button"),
  exportButton: document.querySelector("#export-button"),
  fitImage: document.querySelector("#fit-image-button"),
  regionList: document.querySelector("#region-list"),
  regionAction: document.querySelector("#region-action"),
  inspector: document.querySelector("#inspector"),
  readiness: document.querySelector("#readiness"),
  loading: document.querySelector("#canvas-loading"),
  shell: document.querySelector("#canvas-shell"),
  toast: document.querySelector("#toast"),
  modeHint: document.querySelector("#mode-hint"),
  showImage: document.querySelector("#show-image"),
  showClean: document.querySelector("#show-clean"),
  showReview: document.querySelector("#show-review"),
  showLabels: document.querySelector("#show-labels"),
};

const app = {
  data: null,
  image: null,
  stage: null,
  layers: {},
  mode: "select",
  selected: null,
  selectedRegionId: null,
  firstEdgeNodeId: null,
  view: { scale: 1, x: 0, y: 0 },
  spaceDown: false,
  panStart: null,
  toastTimer: null,
};

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const body = response.headers.get("content-type")?.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    const error = new Error(body?.detail || `${response.status} ${response.statusText}`);
    error.status = response.status;
    error.state = body?.state;
    throw error;
  }
  return body;
}

function toast(message, error = false) {
  clearTimeout(app.toastTimer);
  ui.toast.textContent = message;
  ui.toast.classList.toggle("error", error);
  ui.toast.classList.add("show");
  app.toastTimer = setTimeout(() => ui.toast.classList.remove("show"), 3200);
}

function nodeById(identifier) {
  return app.data.session.nodes.find((node) => node.node_id === identifier);
}

function edgeById(identifier) {
  return app.data.session.edges.find((edge) => edge.edge_id === identifier);
}

function regionById(identifier) {
  return app.data.session.review_regions.find((region) => region.review_region_id === identifier);
}

function updateData(data) {
  app.data = data;
  if (app.selected?.kind === "node" && !nodeById(app.selected.id)) app.selected = null;
  if (app.selected?.kind === "edge" && !edgeById(app.selected.id)) app.selected = null;
  renderChrome();
  drawTopology();
}

async function sendCommand(commandType, payload) {
  ui.saveState.textContent = "正在保存…";
  try {
    const data = await requestJson("/api/command", {
      method: "POST",
      body: JSON.stringify({
        expected_revision: app.data.session.revision,
        command_type: commandType,
        payload,
      }),
    });
    updateData(data);
    ui.saveState.textContent = `已自动保存 · r${data.session.revision}`;
    return data;
  } catch (error) {
    if (error.status === 409 && error.state) updateData(error.state);
    ui.saveState.textContent = "保存失败";
    toast(error.message, true);
    throw error;
  }
}

async function historyAction(kind) {
  try {
    const data = await requestJson(`/api/${kind}`, {
      method: "POST",
      body: JSON.stringify({ expected_revision: app.data.session.revision }),
    });
    app.selected = null;
    updateData(data);
    ui.saveState.textContent = `已自动保存 · r${data.session.revision}`;
  } catch (error) {
    if (error.status === 409 && error.state) updateData(error.state);
    toast(error.message, true);
  }
}

function renderChrome() {
  const { counts, session, readiness } = app.data;
  ui.acceptedNodes.textContent = counts.accepted_nodes;
  ui.pendingCount.textContent = counts.pending_nodes + counts.pending_edges;
  ui.regionProgress.textContent = `${counts.reviewed_regions}/${counts.regions}`;
  ui.undo.disabled = !session.can_undo;
  ui.redo.disabled = !session.can_redo;
  ui.finalReview.disabled = !readiness.ready_for_final_review || session.final_review.status === "reviewed";
  ui.exportButton.disabled = session.final_review.status !== "reviewed";
  renderRegions();
  renderRegionAction();
  renderInspector();
  renderReadiness();
}

function renderRegions() {
  ui.regionList.replaceChildren();
  for (const region of app.data.session.review_regions) {
    const button = document.createElement("button");
    button.className = `region-card ${region.review_status === "reviewed" ? "reviewed" : ""} ${
      region.review_region_id === app.selectedRegionId ? "active" : ""
    }`;
    button.innerHTML = `
      <span class="region-id">${region.review_region_id}</span>
      <span class="region-meta"><span>${region.review_item_ids.length} 个提示</span><span>${
        region.priority === "high" ? "高优先级" : "普通"
      }</span></span>
      <span class="${region.review_status === "reviewed" ? "check" : `priority ${region.priority}`}">${
        region.review_status === "reviewed" ? "✓" : ""
      }</span>`;
    button.addEventListener("click", () => selectRegion(region.review_region_id));
    ui.regionList.append(button);
  }
}

function renderRegionAction() {
  const region = regionById(app.selectedRegionId);
  if (!region) {
    ui.regionAction.innerHTML = "<h3>整图诊断</h3><p>从左侧选择一个区域，进入原图、干净拓扑与冲突层的局部校核。</p>";
    return;
  }
  const reviewed = region.review_status === "reviewed";
  ui.regionAction.innerHTML = `
    <h3>${region.review_region_id} · ${region.priority === "high" ? "高优先级" : "普通"}</h3>
    <p>${region.review_item_ids.length} 个节点/边提示。先目视检查，再接受当前建议或逐项修改。</p>
    <button id="accept-region-button" ${reviewed ? "disabled" : ""}>${reviewed ? "本区已复核" : "接受本区当前建议"}</button>`;
  ui.regionAction.querySelector("button").addEventListener("click", async () => {
    await sendCommand("accept_region_defaults", { review_region_id: region.review_region_id });
    toast(`${region.review_region_id} 已复核；整步可撤销`);
  });
}

function decisionButtons(kind, id, decision) {
  return `<div class="decision-actions">
    <button class="accept" data-decision="accepted" ${decision === "accepted" ? "disabled" : ""}>接受</button>
    <button class="pending" data-decision="pending" ${decision === "pending" ? "disabled" : ""}>待定</button>
    <button class="reject" data-decision="rejected" ${decision === "rejected" ? "disabled" : ""}>排除</button>
  </div>`;
}

function renderInspector() {
  if (!app.selected) {
    ui.inspector.className = "inspector empty";
    ui.inspector.innerHTML = "<p>选择节点或边后，可修改审核状态与格网索引。</p>";
    return;
  }
  ui.inspector.className = "inspector";
  if (app.selected.kind === "node") {
    const node = nodeById(app.selected.id);
    ui.inspector.innerHTML = `
      <div class="object-id">${node.node_id}</div>
      <div class="object-meta">
        <span>坐标 <b>${node.x.toFixed(2)}, ${node.y.toFixed(2)} px</b></span>
        <span>自动状态 <b>${node.auto_status}</b></span>
        <span>来源 <b>${node.source}</b></span>
      </div>
      <div class="field-grid">
        <label>格网行<input id="grid-row" type="number" step="1" value="${node.grid_row ?? ""}" /></label>
        <label>格网列<input id="grid-col" type="number" step="1" value="${node.grid_col ?? ""}" /></label>
      </div>
      <button id="apply-index" class="ghost">应用行列</button>
      ${decisionButtons("node", node.node_id, node.decision)}`;
    ui.inspector.querySelector("#apply-index").addEventListener("click", () =>
      sendCommand("set_node_grid_index", {
        node_id: node.node_id,
        grid_row: ui.inspector.querySelector("#grid-row").value,
        grid_col: ui.inspector.querySelector("#grid-col").value,
      }),
    );
    ui.inspector.querySelectorAll("[data-decision]").forEach((button) =>
      button.addEventListener("click", () =>
        sendCommand("set_node_decision", { node_id: node.node_id, decision: button.dataset.decision }),
      ),
    );
    return;
  }
  const edge = edgeById(app.selected.id);
  ui.inspector.innerHTML = `
    <div class="object-id">${edge.edge_id}</div>
    <div class="object-meta">
      <span>端点 <b>${edge.from_node_id}</b></span>
      <span>→ <b>${edge.to_node_id}</b></span>
      <span>自动状态 <b>${edge.auto_status}</b></span>
      <span>方向 <b>${edge.orientation ?? "未定"}</b></span>
    </div>
    ${decisionButtons("edge", edge.edge_id, edge.decision)}`;
  ui.inspector.querySelectorAll("[data-decision]").forEach((button) =>
    button.addEventListener("click", () =>
      sendCommand("set_edge_decision", { edge_id: edge.edge_id, decision: button.dataset.decision }),
    ),
  );
}

function renderReadiness() {
  const { readiness, session } = app.data;
  if (session.final_review.status === "reviewed") {
    ui.readiness.className = "readiness ready";
    ui.readiness.textContent = "整张网格已确认。若继续编辑，最终确认会自动失效。";
    return;
  }
  if (readiness.ready_for_final_review) {
    ui.readiness.className = "readiness ready";
    ui.readiness.textContent = "所有结构门槛已通过，可以确认整张网格。";
    return;
  }
  ui.readiness.className = "readiness";
  ui.readiness.innerHTML = `<b>尚不能最终确认</b><ul>${readiness.errors
    .slice(0, 5)
    .map((error) => `<li>${error}</li>`)
    .join("")}</ul>`;
}

function initStage() {
  const initialRegionId = app.selectedRegionId;
  app.stage = new Konva.Stage({
    container: "canvas-container",
    width: ui.shell.clientWidth,
    height: ui.shell.clientHeight,
  });
  app.layers.image = new Konva.Layer();
  app.layers.roi = new Konva.Layer();
  app.layers.edges = new Konva.Layer();
  app.layers.nodes = new Konva.Layer();
  for (const layer of Object.values(app.layers)) app.stage.add(layer);

  const imageNode = new Konva.Image({ image: app.image, listening: false });
  app.layers.image.add(imageNode);
  const roi = app.data.manifest.roi.polygon_normalized_full_image_px.flat();
  if (roi.length) {
    app.layers.roi.add(
      new Konva.Line({ points: roi, closed: true, stroke: "#f1b85b", strokeWidth: 3, dash: [18, 10], listening: false }),
    );
  }

  app.stage.on("wheel", handleWheel);
  app.stage.on("mousedown", handlePanStart);
  app.stage.on("mousemove", handlePanMove);
  app.stage.on("mouseup mouseleave", handlePanEnd);
  app.stage.on("contextmenu", (event) => event.evt.preventDefault());
  app.stage.on("click tap", handleStageClick);
  new ResizeObserver(() => {
    app.stage.size({ width: ui.shell.clientWidth, height: ui.shell.clientHeight });
    applyView();
  }).observe(ui.shell);
  fitImage();
  drawTopology();
  ui.loading.style.display = "none";
  if (initialRegionId) selectRegion(initialRegionId);
}

function applyView() {
  for (const layer of Object.values(app.layers)) {
    layer.scale({ x: app.view.scale, y: app.view.scale });
    layer.position({ x: app.view.x, y: app.view.y });
    layer.batchDraw();
  }
}

function fitBounds(bbox, padding = 70) {
  const [x0, y0, x1, y1] = bbox;
  const width = Math.max(1, x1 - x0);
  const height = Math.max(1, y1 - y0);
  const stageWidth = app.stage.width();
  const stageHeight = app.stage.height();
  const scale = Math.min((stageWidth - padding * 2) / width, (stageHeight - padding * 2) / height);
  app.view.scale = Math.max(0.05, Math.min(6, scale));
  app.view.x = (stageWidth - width * app.view.scale) / 2 - x0 * app.view.scale;
  app.view.y = (stageHeight - height * app.view.scale) / 2 - y0 * app.view.scale;
  applyView();
}

function fitImage() {
  const [width, height] = app.data.manifest.image.normalized_size_width_height;
  app.selectedRegionId = null;
  renderRegions();
  renderRegionAction();
  fitBounds([0, 0, width, height], 26);
}

function selectRegion(identifier) {
  app.selectedRegionId = identifier;
  app.selected = null;
  const region = regionById(identifier);
  fitBounds(region.bbox_full_xyxy, 44);
  renderChrome();
  drawTopology();
}

function handleWheel(event) {
  event.evt.preventDefault();
  const pointer = app.stage.getPointerPosition();
  const oldScale = app.view.scale;
  const point = { x: (pointer.x - app.view.x) / oldScale, y: (pointer.y - app.view.y) / oldScale };
  const direction = event.evt.deltaY > 0 ? -1 : 1;
  const nextScale = Math.max(0.04, Math.min(8, oldScale * (direction > 0 ? 1.12 : 1 / 1.12)));
  app.view.scale = nextScale;
  app.view.x = pointer.x - point.x * nextScale;
  app.view.y = pointer.y - point.y * nextScale;
  applyView();
}

function handlePanStart(event) {
  if (event.evt.button === 2 || app.spaceDown) {
    app.panStart = { x: event.evt.clientX, y: event.evt.clientY, viewX: app.view.x, viewY: app.view.y };
  }
}

function handlePanMove(event) {
  if (!app.panStart) return;
  app.view.x = app.panStart.viewX + event.evt.clientX - app.panStart.x;
  app.view.y = app.panStart.viewY + event.evt.clientY - app.panStart.y;
  applyView();
}

function handlePanEnd() { app.panStart = null; }

function pointerInImage() {
  const transform = app.layers.nodes.getAbsoluteTransform().copy().invert();
  return transform.point(app.stage.getPointerPosition());
}

function handleStageClick(event) {
  if (app.panStart || event.target !== app.stage || event.evt.button !== 0) return;
  if (app.mode === "add-node") {
    const point = pointerInImage();
    sendCommand("add_node", { x: point.x, y: point.y, grid_row: null, grid_col: null }).then((data) => {
      const newest = data.session.nodes.at(-1);
      app.selected = { kind: "node", id: newest.node_id };
      setMode("select");
      renderChrome();
      drawTopology();
    });
  } else {
    app.selected = null;
    app.firstEdgeNodeId = null;
    renderChrome();
    drawTopology();
  }
}

function edgeStyle(edge) {
  const selected = app.selected?.kind === "edge" && app.selected.id === edge.edge_id;
  if (selected) return { stroke: "#ffffff", width: 5, dash: [] };
  if (edge.decision === "accepted") return { stroke: "#54c8bd", width: 2.6, dash: [] };
  if (edge.decision === "pending" && edge.suggested_decision === "accepted") {
    return { stroke: "#f1b85b", width: 3.2, dash: [10, 7] };
  }
  if (edge.decision === "pending") return { stroke: "#f07167", width: 3.2, dash: [5, 6] };
  return { stroke: "#7a3430", width: 2.5, dash: [4, 8] };
}

function nodeStyle(node) {
  const selected = app.selected?.kind === "node" && app.selected.id === node.node_id;
  const first = app.firstEdgeNodeId === node.node_id;
  if (selected || first) return { fill: "#ffffff", stroke: "#0b1116", radius: 7.5 };
  if (node.decision === "accepted") return { fill: "#5ed5c8", stroke: "#0a3633", radius: 4.8 };
  if (node.decision === "pending" && node.suggested_decision === "accepted") {
    return { fill: "#f1b85b", stroke: "#67491a", radius: 6.4 };
  }
  if (node.decision === "pending") return { fill: "#281210", stroke: "#f07167", radius: 6.4 };
  return { fill: "#6f2e2a", stroke: "#a74f49", radius: 5.8 };
}

function objectVisible(decision) {
  return decision === "accepted" ? ui.showClean.checked : ui.showReview.checked;
}

function drawTopology() {
  if (!app.stage || !app.data) return;
  app.layers.image.visible(ui.showImage.checked);
  app.layers.edges.destroyChildren();
  app.layers.nodes.destroyChildren();
  const nodeMap = new Map(app.data.session.nodes.map((node) => [node.node_id, node]));

  for (const edge of app.data.session.edges) {
    if (!objectVisible(edge.decision)) continue;
    const first = nodeMap.get(edge.from_node_id);
    const second = nodeMap.get(edge.to_node_id);
    if (!first || !second) continue;
    const style = edgeStyle(edge);
    const line = new Konva.Line({
      points: [first.x, first.y, second.x, second.y],
      stroke: style.stroke,
      strokeWidth: style.width / app.view.scale,
      dash: style.dash.map((value) => value / app.view.scale),
      hitStrokeWidth: 14 / app.view.scale,
      opacity: edge.decision === "rejected" ? 0.58 : 0.9,
    });
    line.on("click tap", (event) => {
      event.cancelBubble = true;
      app.selected = { kind: "edge", id: edge.edge_id };
      app.firstEdgeNodeId = null;
      renderChrome();
      drawTopology();
    });
    app.layers.edges.add(line);
  }

  for (const node of app.data.session.nodes) {
    if (!objectVisible(node.decision)) continue;
    const style = nodeStyle(node);
    const circle = new Konva.Circle({
      x: node.x,
      y: node.y,
      radius: style.radius / app.view.scale,
      fill: style.fill,
      stroke: style.stroke,
      strokeWidth: 1.6 / app.view.scale,
      draggable: app.mode === "select" && node.decision !== "rejected",
      hitStrokeWidth: 10 / app.view.scale,
    });
    circle.on("click tap", (event) => {
      event.cancelBubble = true;
      if (app.mode === "edge") {
        handleEdgeNode(node.node_id);
      } else {
        app.selected = { kind: "node", id: node.node_id };
        renderChrome();
        drawTopology();
      }
    });
    circle.on("dragend", async () => {
      await sendCommand("move_node", { node_id: node.node_id, x: circle.x(), y: circle.y() });
    });
    app.layers.nodes.add(circle);

    if (ui.showLabels.checked && Number.isInteger(node.grid_row) && Number.isInteger(node.grid_col)) {
      app.layers.nodes.add(
        new Konva.Text({
          x: node.x + 7 / app.view.scale,
          y: node.y - 12 / app.view.scale,
          text: `${node.grid_row},${node.grid_col}`,
          fill: "#ffffff",
          fontSize: 10 / app.view.scale,
          listening: false,
        }),
      );
    }
  }

  const region = regionById(app.selectedRegionId);
  if (region) {
    const [x0, y0, x1, y1] = region.bbox_full_xyxy;
    app.layers.nodes.add(
      new Konva.Rect({
        x: x0,
        y: y0,
        width: x1 - x0,
        height: y1 - y0,
        stroke: "#ffffff",
        strokeWidth: 1.5 / app.view.scale,
        dash: [9 / app.view.scale, 7 / app.view.scale],
        listening: false,
      }),
    );
  }
  app.layers.edges.batchDraw();
  app.layers.nodes.batchDraw();
}

function handleEdgeNode(identifier) {
  if (!app.firstEdgeNodeId) {
    app.firstEdgeNodeId = identifier;
    ui.modeHint.textContent = "再点一个节点以切换连接";
    drawTopology();
    return;
  }
  if (app.firstEdgeNodeId === identifier) {
    app.firstEdgeNodeId = null;
    ui.modeHint.textContent = "依次点击两个节点";
    drawTopology();
    return;
  }
  const first = app.firstEdgeNodeId;
  app.firstEdgeNodeId = null;
  sendCommand("toggle_edge_between", { from_node_id: first, to_node_id: identifier }).then(() => {
    ui.modeHint.textContent = "依次点击两个节点";
  });
}

function setMode(mode) {
  app.mode = mode;
  app.firstEdgeNodeId = null;
  document.querySelectorAll(".tool").forEach((button) =>
    button.classList.toggle("active", button.dataset.mode === mode),
  );
  ui.modeHint.textContent = {
    select: "拖动节点可修正坐标",
    "add-node": "点击图像空白处新增节点",
    edge: "依次点击两个节点",
  }[mode];
  drawTopology();
}

function bindEvents() {
  document.querySelectorAll(".tool").forEach((button) =>
    button.addEventListener("click", () => setMode(button.dataset.mode)),
  );
  [ui.showImage, ui.showClean, ui.showReview, ui.showLabels].forEach((input) =>
    input.addEventListener("change", drawTopology),
  );
  ui.fitImage.addEventListener("click", fitImage);
  ui.undo.addEventListener("click", () => historyAction("undo"));
  ui.redo.addEventListener("click", () => historyAction("redo"));
  ui.finalReview.addEventListener("click", async () => {
    await sendCommand("set_final_reviewed", {});
    toast("整张网格已完成人工审核确认");
  });
  ui.exportButton.addEventListener("click", async () => {
    try {
      const result = await requestJson("/api/export", { method: "POST", body: "{}" });
      toast(`已导出 ${result.node_count} 节点、${result.edge_count} 条边、${result.cell_count} 个单元`);
    } catch (error) {
      toast(error.message, true);
    }
  });
  window.addEventListener("keydown", (event) => {
    if (event.code === "Space") app.spaceDown = true;
    if (event.ctrlKey && event.key.toLowerCase() === "z") {
      event.preventDefault();
      historyAction("undo");
    }
    if (event.ctrlKey && event.key.toLowerCase() === "y") {
      event.preventDefault();
      historyAction("redo");
    }
    if (event.key === "Escape") {
      app.selected = null;
      app.firstEdgeNodeId = null;
      setMode("select");
      renderChrome();
    }
    if (event.key === "Delete" && app.selected) {
      const command = app.selected.kind === "node" ? "set_node_decision" : "set_edge_decision";
      const key = app.selected.kind === "node" ? "node_id" : "edge_id";
      sendCommand(command, { [key]: app.selected.id, decision: "rejected" });
    }
  });
  window.addEventListener("keyup", (event) => { if (event.code === "Space") app.spaceDown = false; });
}

async function loadImage(url) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = reject;
    image.src = `${url}?t=${Date.now()}`;
  });
}

async function bootstrap() {
  bindEvents();
  try {
    const data = await requestJson("/api/state");
    app.data = data;
    app.selectedRegionId = data.session.review_regions[0]?.review_region_id ?? null;
    app.image = await loadImage(data.image_url);
    renderChrome();
    initStage();
    ui.saveState.textContent = `已载入 · r${data.session.revision}`;
  } catch (error) {
    ui.loading.textContent = `载入失败：${error.message}`;
    toast(error.message, true);
  }
}

bootstrap();
