// src/api/static/condition-tree-canvas.js
// 可拖拽条件树画布（思维导图式左→右布局）。
// - 节点卡片可点选、可拖拽（同组排序 / 跨组重挂父节点）
// - 画布空白处拖拽平移、滚轮缩放、工具栏 ± / 适应
// - 纯 DOM + SVG，无第三方依赖；只依赖 window.CdssConditionTree 的纯函数
//
// 公共 API：CdssTreeCanvas.render(mount, tree, options)
// options: {
//   selectedPath: []                      // 当前选中节点路径
//   onSelect(pathKey)                     // 点选卡片（pathKey 字符串）
//   onMove(srcPathKey, targetPathKey, i)  // 拖拽落下（i 为目标组原始 children 下标）
//   labels: { lab_concept: "检验概念", ... }
//   esc: (s) => html 转义函数
// }
(function () {
  "use strict";

  const H_GAP = 64;   // 父卡右缘到子卡左缘的水平间距
  const V_GAP = 14;   // 兄弟卡片垂直间距
  const PAD = 24;     // 世界坐标内边距
  const MIN_SCALE = 0.35;
  const MAX_SCALE = 1.8;

  const ctree = () => window.CdssConditionTree || null;

  // 每个 mount 记住视图状态（平移/缩放），重渲染不丢视角
  const viewStates = new WeakMap();

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // ------------------------------------------------------------ 卡片内容

  function chipLabel(node, labels) {
    if (node.type === "group") {
      const op = String(node.operator || "AND").toUpperCase();
      return op === "AND" ? "且 AND" : op === "OR" ? "或 OR" : "非 NOT";
    }
    if (node.type === "weighted_group") return "加权组";
    return (labels && labels[node.type]) || node.type;
  }

  function cardSummary(node) {
    const ct = ctree();
    if (!ct) return "";
    if (node.type === "group") return `${(node.children || []).length} 个子条件`;
    if (node.type === "weighted_group") {
      return `scoring=${node.scoring || "weighted_sum"} · ${(node.children || []).length} 项`;
    }
    return ct.describeNode(node) || "";
  }

  function cardHtml(node, path, readonly, options, selectedKey) {
    const ct = ctree();
    const esc = options.esc || ((s) => String(s));
    const key = ct.pathKey(path);
    const isGroup = node.type === "group" || node.type === "weighted_group";
    const op = String(node.operator || "AND").toUpperCase();
    const chipClass = node.type === "group" ? `op-${op}` : isGroup ? "op-w" : "op-leaf";
    const classes = [
      "ctc-card",
      isGroup ? "ctc-group" : "ctc-leaf",
      readonly ? "ctc-readonly" : "",
      key === selectedKey ? "ctc-selected" : "",
    ].filter(Boolean).join(" ");
    const badge = readonly ? '<span class="badge noncompilable">仅原生引擎</span>' : "";
    const label = node.label
      ? String(node.label).replace(/^[（(【[]+/, "").replace(/[）)】\]]+$/, "")
      : "";
    const sum = esc(cardSummary(node));
    return `<div class="${classes}" data-path="${key}" data-depth="${path.length}">
      <div class="ctc-card-top"><span class="ctc-chip ${chipClass}">${esc(chipLabel(node, options.labels))}</span>${badge}</div>
      <div class="ctc-sum" ${label ? `title="${esc(label)}"` : ""}>${sum}</div>
    </div>`;
  }

  // ------------------------------------------------------------ 布局与绘制

  function render(mount, tree, options = {}) {
    const ct = ctree();
    if (!mount) return;
    if (!ct || !tree || typeof tree !== "object") {
      mount.innerHTML = '<div class="ct-empty">条件树不可用</div>';
      return;
    }
    const esc = options.esc || ((s) => String(s));
    const selectedKey = ct.pathKey(options.selectedPath || []);

    mount.innerHTML = "";
    const rootEl = document.createElement("div");
    rootEl.className = "ctc-root";
    rootEl.innerHTML = `
      <div class="ctc-toolbar">
        <button type="button" class="ctc-tool" data-ctc="zoom-out" title="缩小">−</button>
        <span class="ctc-zoom-label">100%</span>
        <button type="button" class="ctc-tool" data-ctc="zoom-in" title="放大">＋</button>
        <button type="button" class="ctc-tool ctc-tool-wide" data-ctc="fit" title="适应窗口">适应</button>
      </div>
      <div class="ctc-viewport">
        <div class="ctc-world">
          <svg class="ctc-edges"></svg>
          <div class="ctc-nodes"></div>
          <div class="ctc-indicator" hidden></div>
        </div>
        <div class="ctc-hint">拖拽卡片调整结构 · 拖动空白平移 · 滚轮缩放</div>
      </div>`;
    mount.appendChild(rootEl);

    const viewport = rootEl.querySelector(".ctc-viewport");
    const world = rootEl.querySelector(".ctc-world");
    const nodesLayer = rootEl.querySelector(".ctc-nodes");
    const edgesSvg = rootEl.querySelector(".ctc-edges");
    const zoomLabel = rootEl.querySelector(".ctc-zoom-label");
    const indicator = rootEl.querySelector(".ctc-indicator");

    // -------------------------------------------------- 卡片 DOM + 只读判定

    const items = []; // { node, path, key, el, readonly, x, y, w, h, parent }
    const walk = (node, path, parentItem) => {
      const own = ct.READONLY_TYPES.includes(node.type);
      const readonly = own || Boolean(parentItem && parentItem.readonly);
      const el = document.createElement("div");
      el.innerHTML = cardHtml(node, path, readonly, options, selectedKey);
      const card = el.firstElementChild;
      nodesLayer.appendChild(card);
      const item = { node, path, key: ct.pathKey(path), el: card, readonly, parent: parentItem };
      items.push(item);
      (node.children || []).forEach((child, i) => walk(child, [...path, i], item));
      return item;
    };
    walk(tree, [], null);

    // 量尺寸
    items.forEach((item) => {
      item.w = item.el.offsetWidth;
      item.h = item.el.offsetHeight;
    });

    // 左→右布局：x 按父右缘推；y 叶子顺序堆叠、父在子范围垂直居中。
    // 关键：返回"子树底部"（含后代），否则相邻兄弟子树会在同一列重叠。
    let maxY = 0;
    const layout = (item, x, top) => {
      item.x = x;
      const childItems = items.filter((it) => it.parent === item);
      let subBottom;
      if (childItems.length) {
        const childX = x + item.w + H_GAP;
        let cursor = top;
        let deepest = 0;
        childItems.forEach((child) => {
          const bottom = layout(child, childX, cursor);
          cursor = bottom + V_GAP;
          deepest = Math.max(deepest, bottom);
        });
        const first = childItems[0];
        const last = childItems[childItems.length - 1];
        item.y = (first.y + last.y + last.h) / 2 - item.h / 2;
        subBottom = Math.max(item.y + item.h, deepest);
      } else {
        item.y = top;
        subBottom = item.y + item.h;
      }
      maxY = Math.max(maxY, subBottom);
      return subBottom;
    };
    const rootItem = items[0];
    layout(rootItem, PAD, PAD);

    // 定位卡片
    items.forEach((item) => {
      item.el.style.left = `${item.x}px`;
      item.el.style.top = `${item.y}px`;
    });

    // 连线
    const worldW = Math.max(
      PAD * 2,
      items.reduce((m, it) => Math.max(m, it.x + it.w), 0) + PAD,
    );
    const worldH = maxY + PAD;
    edgesSvg.setAttribute("width", worldW);
    edgesSvg.setAttribute("height", worldH);
    edgesSvg.setAttribute("viewBox", `0 0 ${worldW} ${worldH}`);
    const lines = items.filter((it) => it.parent).map((it) => {
      const p = it.parent;
      const x1 = p.x + p.w;
      const y1 = p.y + p.h / 2;
      const x2 = it.x;
      const y2 = it.y + it.h / 2;
      const dx = Math.max(28, (x2 - x1) / 2);
      return `<path class="ctc-edge ${it.readonly ? "ctc-edge-ro" : ""}" d="M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}"></path>`;
    });
    edgesSvg.innerHTML = lines.join("");

    // -------------------------------------------------------- 视图（平移/缩放）

    const view = viewStates.get(mount) || { tx: 0, ty: 0, scale: 1, fitted: false };
    viewStates.set(mount, view);
    const applyView = () => {
      world.style.transform = `translate(${view.tx}px, ${view.ty}px) scale(${view.scale})`;
      zoomLabel.textContent = `${Math.round(view.scale * 100)}%`;
    };
    const fit = () => {
      const vw = viewport.clientWidth || 480;
      const vh = viewport.clientHeight || 320;
      const scale = clamp(Math.min((vw - 32) / worldW, (vh - 24) / worldH, 1.1), MIN_SCALE, 1);
      view.scale = scale;
      view.tx = (vw - worldW * scale) / 2;
      view.ty = (vh - worldH * scale) / 2;
      view.fitted = true;
      applyView();
    };
    const zoomAt = (mx, my, factor) => {
      const next = clamp(view.scale * factor, MIN_SCALE, MAX_SCALE);
      if (next === view.scale) return;
      view.tx = mx - ((mx - view.tx) / view.scale) * next;
      view.ty = my - ((my - view.ty) / view.scale) * next;
      view.scale = next;
      applyView();
    };
    rootEl.querySelector(".ctc-toolbar").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-ctc]");
      if (!btn) return;
      const action = btn.dataset.ctc;
      if (action === "zoom-in") zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, 1.2);
      else if (action === "zoom-out") zoomAt(viewport.clientWidth / 2, viewport.clientHeight / 2, 1 / 1.2);
      else if (action === "fit") fit();
    });
    viewport.addEventListener("wheel", (event) => {
      event.preventDefault();
      const rect = viewport.getBoundingClientRect();
      zoomAt(event.clientX - rect.left, event.clientY - rect.top, event.deltaY < 0 ? 1.1 : 1 / 1.1);
    }, { passive: false });

    // ------------------------------------------------------------- 拖拽逻辑

    const isDescendantOrSelf = (prefix, path) =>
      prefix.length <= path.length && prefix.every((v, i) => v === path[i]);

    // 候选放置组：可编辑 group、不在被拖子树内
    const dropZones = () => items.filter((item) =>
      item.node.type === "group"
      && !item.readonly
      && !isDescendantOrSelf(drag.path, item.path),
    ).map((item) => {
      const childItems = items.filter((it) => it.parent === item);
      const right = childItems.reduce((m, c) => Math.max(m, c.x + c.w), item.x + item.w);
      const bottom = childItems.length
        ? childItems[childItems.length - 1].y + childItems[childItems.length - 1].h
        : item.y + item.h;
      return {
        item,
        childItems,
        x1: item.x,
        y1: item.y - 8,
        x2: right + 12,
        y2: bottom + 8,
      };
    });

    let drag = null; // { path, key, el, started, sx, sy, drop }
    const hideIndicator = () => { indicator.hidden = true; };
    const showIndicator = (zone, index) => {
      const { item, childItems } = zone;
      let y;
      if (!childItems.length) y = item.y + item.h + 4;
      else if (index >= childItems.length) y = childItems[childItems.length - 1].y + childItems[childItems.length - 1].h + 4;
      else y = childItems[index].y - 4;
      indicator.hidden = false;
      indicator.style.left = `${item.x + item.w + 26}px`;
      indicator.style.top = `${y}px`;
      indicator.style.width = `${Math.max(160, zone.x2 - item.x - item.w - 38)}px`;
    };
    const pickDrop = (px, py) => {
      // 选取包含指针且最深的组（x1 最大者）
      let best = null;
      dropZones().forEach((zone) => {
        if (px < zone.x1 || px > zone.x2 || py < zone.y1 || py > zone.y2) return;
        if (!best || zone.item.x > best.item.x) best = zone;
      });
      if (!best) return null;
      let index = 0;
      best.childItems.forEach((child) => {
        if (child.y + child.h / 2 < py) index += 1;
      });
      return { zone: best, index };
    };

    const cleanup = () => {
      if (drag && drag.el) {
        drag.el.classList.remove("ctc-dragging");
        drag.el.style.transform = "";
      }
      hideIndicator();
      drag = null;
    };

    rootEl.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      const card = event.target.closest(".ctc-card");
      if (!card) {
        // 空白处：平移画布
        if (event.target.closest(".ctc-toolbar")) return;
        const rect = viewport.getBoundingClientRect();
        const startX = event.clientX;
        const startY = event.clientY;
        const startTx = view.tx;
        const startTy = view.ty;
        let moved = false;
        viewport.setPointerCapture(event.pointerId);
        const onMove = (e) => {
          const dx = e.clientX - startX;
          const dy = e.clientY - startY;
          if (Math.abs(dx) + Math.abs(dy) > 3) moved = true;
          if (moved) {
            viewport.classList.add("ctc-panning");
            view.tx = startTx + dx;
            view.ty = startTy + dy;
            applyView();
          }
        };
        const onUp = () => {
          viewport.classList.remove("ctc-panning");
          viewport.removeEventListener("pointermove", onMove);
          viewport.removeEventListener("pointerup", onUp);
          viewport.removeEventListener("pointercancel", onUp);
        };
        viewport.addEventListener("pointermove", onMove);
        viewport.addEventListener("pointerup", onUp);
        viewport.addEventListener("pointercancel", onUp);
        return;
      }
      // 卡片：延迟到移动超过阈值才算拖拽，否则是点选
      const item = items.find((it) => it.el === card);
      if (!item) return;
      const draggable = !item.readonly && item.path.length > 0;
      const sx = event.clientX;
      const sy = event.clientY;
      let started = false;
      card.setPointerCapture(event.pointerId);
      const onCardMove = (e) => {
        if (!draggable) return;
        const dx = e.clientX - sx;
        const dy = e.clientY - sy;
        if (!started && Math.abs(dx) + Math.abs(dy) > 5) {
          started = true;
          drag = { path: item.path, key: item.key, el: card, started: true };
          card.classList.add("ctc-dragging");
        }
        if (!started) return;
        card.style.transform = `translate(${dx / view.scale}px, ${dy / view.scale}px)`;
        const rect = viewport.getBoundingClientRect();
        const wx = (e.clientX - rect.left - view.tx) / view.scale;
        const wy = (e.clientY - rect.top - view.ty) / view.scale;
        const drop = pickDrop(wx, wy);
        drag.drop = drop;
        if (drop) showIndicator(drop.zone, drop.index);
        else hideIndicator();
      };
      const onCardUp = () => {
        card.removeEventListener("pointermove", onCardMove);
        card.removeEventListener("pointerup", onCardUp);
        card.removeEventListener("pointercancel", onCardCancel);
        if (started && drag && drag.drop) {
          const { zone, index } = drag.drop;
          if (typeof options.onMove === "function") {
            options.onMove(drag.key, zone.item.key, index);
          }
          cleanup();
          return;
        }
        if (!started && typeof options.onSelect === "function") {
          options.onSelect(item.key);
        }
        cleanup();
      };
      const onCardCancel = () => {
        card.removeEventListener("pointermove", onCardMove);
        card.removeEventListener("pointerup", onCardUp);
        card.removeEventListener("pointercancel", onCardCancel);
        cleanup();
      };
      card.addEventListener("pointermove", onCardMove);
      card.addEventListener("pointerup", onCardUp);
      card.addEventListener("pointercancel", onCardCancel);
    });

    // 首次渲染自适应；重渲染保持视角
    if (view.fitted) applyView();
    else fit();
  }

  window.CdssTreeCanvas = { render };
}());
