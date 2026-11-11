// tests/js/condition_tree_editor.test.js
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const SOURCE_PATH = path.join(__dirname, "..", "..", "src", "api", "static", "condition-tree-editor.js");

function loadModule() {
  const source = fs.readFileSync(SOURCE_PATH, "utf8");
  const context = vm.createContext({ window: {}, console });
  vm.runInContext(source, context, { filename: SOURCE_PATH });
  return context.window.CdssConditionTree;
}

// Objects returned from the vm context have another realm's prototype, which
// breaks deepStrictEqual's prototype check; plain-JSON roundtrip normalizes.
const plain = (value) => JSON.parse(JSON.stringify(value));

const LAB_SYMPTOM = {
  type: "group",
  operator: "AND",
  children: [
    { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
    { type: "symptom", symptom_code: "SYM_PAIN" },
  ],
};

// ---------------------------------------------------------------- sanitize

test("sanitize strips null fields and defaults empty group children", () => {
  const ctree = loadModule();
  const tree = ctree.sanitizeTree({
    type: "group",
    operator: "AND",
    children: [
      { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED", parameter: null, label: null },
    ],
  });
  assert.deepEqual(plain(tree), {
    type: "group",
    operator: "AND",
    children: [{ type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" }],
  });
  const empty = ctree.sanitizeTree({ type: "group", operator: "OR", children: [] });
  assert.deepEqual(plain(empty), { type: "group", operator: "OR", children: [] });
});

test("sanitize rejects forbidden keys and unknown types", () => {
  const ctree = loadModule();
  // JSON.parse 才会创建名为 __proto__ 的自有属性；对象字面量只会设原型
  assert.throws(
    () => ctree.jsonToTree('{"type":"lab_concept","__proto__":{"polluted":true}}'),
    /禁止的字段/,
  );
  assert.throws(() => ctree.sanitizeTree({ type: "nope" }), /未知的条件类型/);
  assert.throws(() => ctree.sanitizeTree("lab"), /必须是对象/);
});

// ------------------------------------------------------------ compilability

test("compilable AND/OR/NOT trees with supported leaves", () => {
  const ctree = loadModule();
  assert.equal(ctree.isCompilable(LAB_SYMPTOM), true);
  assert.equal(ctree.isCompilable({
    type: "group", operator: "NOT",
    children: [{ type: "symptom", symptom_code: "S1" }],
  }), true);
});

test("weighted_group / evidence / fact not compilable with reasons", () => {
  const ctree = loadModule();
  assert.match(ctree.compilabilityReason({ type: "weighted_group", children: [] }), /weighted_group/);
  assert.match(ctree.compilabilityReason({ type: "evidence", evidence_id: "E1" }), /evidence/);
  assert.match(ctree.compilabilityReason({ type: "fact", code: "F1" }), /fact/);
});

test("empty group and unknown operator not compilable", () => {
  const ctree = loadModule();
  assert.match(ctree.compilabilityReason({ type: "group", operator: "AND", children: [] }), /children 为空/);
  assert.match(
    ctree.compilabilityReason({ type: "group", operator: "XOR", children: [{ type: "symptom", symptom_code: "S1" }] }),
    /operator=XOR/,
  );
});

test("nested uncompilable leaf reports root.children[i] path", () => {
  const ctree = loadModule();
  const tree = {
    type: "group",
    operator: "AND",
    children: [
      { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
      { type: "group", operator: "OR", children: [{ type: "evidence", evidence_id: "E1" }] },
    ],
  };
  assert.equal(ctree.compilabilityReason(tree), "root.children[1].children[0]: type=evidence 仅原生引擎可执行");
});

// ------------------------------------------------------------------- CRUD

test("updateNode patches fields and deletes empty values", () => {
  const ctree = loadModule();
  const updated = ctree.updateNode(LAB_SYMPTOM, [1], { symptom_code: "SYM_FEVER", duration_minutes: 30 });
  assert.equal(ctree.getNode(updated, [1]).symptom_code, "SYM_FEVER");
  assert.equal(ctree.getNode(updated, [1]).duration_minutes, 30);
  assert.equal(ctree.getNode(updated, [0]).lab_code, "CTN");
  const cleared = ctree.updateNode(updated, [1], { duration_minutes: null });
  assert.equal("duration_minutes" in ctree.getNode(cleared, [1]), false);
  // 原树不被修改
  assert.equal(ctree.getNode(LAB_SYMPTOM, [1]).symptom_code, "SYM_PAIN");
});

test("addChild on leaf wraps it into an AND group preserving fields", () => {
  const ctree = loadModule();
  const tree = ctree.addChild({ type: "symptom", symptom_code: "S1" }, [], { type: "symptom", symptom_code: "S2" });
  assert.deepEqual(plain(tree), {
    type: "group", operator: "AND",
    children: [{ type: "symptom", symptom_code: "S1" }, { type: "symptom", symptom_code: "S2" }],
  });
});

test("addSibling at root wraps whole tree into a new AND group", () => {
  const ctree = loadModule();
  const tree = ctree.addSibling({ type: "symptom", symptom_code: "S1" }, [], ctree.newLeaf("lab_concept"));
  assert.equal(tree.type, "group");
  assert.equal(tree.operator, "AND");
  assert.equal(tree.children[0].type, "symptom");
  assert.equal(tree.children[1].type, "lab_concept");
});

test("removeNode folds single-child AND group but never NOT", () => {
  const ctree = loadModule();
  const folded = ctree.removeNode(LAB_SYMPTOM, [0]);
  assert.deepEqual(plain(folded), { type: "symptom", symptom_code: "SYM_PAIN" });
  const notTree = {
    type: "group", operator: "NOT",
    children: [{ type: "group", operator: "OR", children: [{ type: "symptom", symptom_code: "S1" }] }],
  };
  const kept = ctree.removeNode(notTree, [0, 0]);
  assert.equal(kept.type, "group");
  assert.equal(kept.operator, "NOT");
  assert.equal(kept.children.length, 1);
  assert.equal(kept.children[0].operator, "OR");
});

test("removeNode root resets to empty AND group", () => {
  const ctree = loadModule();
  assert.deepEqual(plain(ctree.removeNode(LAB_SYMPTOM, [])), { type: "group", operator: "AND", children: [] });
});

test("moveNode swaps and ignores out-of-range", () => {
  const ctree = loadModule();
  const moved = ctree.moveNode(LAB_SYMPTOM, [0], 1);
  assert.equal(ctree.getNode(moved, [0]).type, "symptom");
  assert.equal(ctree.getNode(moved, [1]).type, "lab_concept");
  assert.equal(ctree.moveNode(LAB_SYMPTOM, [0], 5), LAB_SYMPTOM);
  assert.equal(ctree.moveNode(LAB_SYMPTOM, [], 1), LAB_SYMPTOM);
});

// ------------------------------------------------------------- moveSubtree

const NESTED = {
  type: "group", operator: "AND",
  children: [
    { type: "symptom", symptom_code: "SYM_PAIN" },
    {
      type: "group", operator: "OR",
      children: [
        { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
        { type: "vital", vital_code: "SBP", operator: ">", threshold_value: 180 },
      ],
    },
  ],
};

test("moveSubtree moves a leaf across groups", () => {
  const ctree = loadModule();
  // 根下 symptom 移入 OR 组末尾；根 AND 只剩 1 子被折叠 → OR 组升级为根
  const moved = ctree.moveSubtree(NESTED, [0], [1], 2);
  assert.equal(moved.operator, "OR");
  assert.equal(ctree.getNode(moved, [2]).symptom_code, "SYM_PAIN");
  assert.equal(ctree.isCompilable(moved), true);
  // 原树不被修改
  assert.equal(ctree.getNode(NESTED, [0]).type, "symptom");
});

test("moveSubtree reorders within the same group using original index", () => {
  const ctree = loadModule();
  // [pain, OR] 中 OR 组移到最前：index=0
  const moved = ctree.moveSubtree(NESTED, [1], [], 0);
  assert.equal(moved.children[0].operator, "OR");
  assert.equal(moved.children[1].symptom_code, "SYM_PAIN");
  // 同组 symptom：插到原 index=1（也就是自己位置）应是无操作
  const noop = ctree.moveSubtree(NESTED, [0], [], 1);
  assert.deepEqual(plain(noop), plain(NESTED));
  // 往后挪一位：index=2
  const back = ctree.moveSubtree(NESTED, [0], [], 2);
  assert.equal(back.children[0].operator, "OR");
  assert.equal(back.children[1].symptom_code, "SYM_PAIN");
});

test("moveSubtree rejects root, own descendant, readonly and non-group targets", () => {
  const ctree = loadModule();
  assert.equal(ctree.moveSubtree(NESTED, [], [1], 0), null); // 根不可移动
  assert.equal(ctree.moveSubtree(NESTED, [1], [1, 0], 0), null); // 移入自身后代
  assert.equal(ctree.moveSubtree(NESTED, [1], [1], 0), null); // 移到原位（前缀=自身）
  assert.equal(ctree.moveSubtree(NESTED, [9], [], 0), null); // 路径不存在
  const readonlyTree = {
    type: "group", operator: "AND",
    children: [
      { type: "evidence", evidence_id: "E1" },
      { type: "weighted_group", scoring: "weighted_sum", children: [{ type: "symptom", symptom_code: "S1" }] },
    ],
  };
  // src 只读
  assert.equal(ctree.moveSubtree(readonlyTree, [0], [1], 0), null);
  // 目标父是 weighted_group（只读）
  assert.equal(ctree.moveSubtree(readonlyTree, [0], [1], 0), null);
  // 目标父不存在
  assert.equal(ctree.moveSubtree(readonlyTree, [0], [5], 0), null);
});

test("moveSubtree folds single-child AND group left behind", () => {
  const ctree = loadModule();
  const tree = {
    type: "group", operator: "AND",
    children: [
      { type: "group", operator: "AND", children: [
        { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
        { type: "symptom", symptom_code: "SYM_PAIN" },
      ] },
      { type: "symptom", symptom_code: "SYM_FEVER" },
    ],
  };
  // 把 lab 从嵌套 AND 移到根末尾；嵌套 AND 只剩 1 子 → 折叠
  const moved = ctree.moveSubtree(tree, [0, 0], [], 2);
  assert.deepEqual(plain(moved), {
    type: "group", operator: "AND",
    children: [
      { type: "symptom", symptom_code: "SYM_PAIN" },
      { type: "symptom", symptom_code: "SYM_FEVER" },
      { type: "lab_concept", lab_code: "CTN", concept_code: "ELEVATED" },
    ],
  });
});

test("moveSubtree clamps index into target range", () => {
  const ctree = loadModule();
  const moved = ctree.moveSubtree(NESTED, [0], [1], 99);
  assert.equal(ctree.getNode(moved, [2]).symptom_code, "SYM_PAIN");
  const movedNeg = ctree.moveSubtree(NESTED, [0], [1], -3);
  assert.equal(ctree.getNode(movedNeg, [0]).symptom_code, "SYM_PAIN");
});

test("readonly nodes reject mutation via assertMutable", () => {
  const ctree = loadModule();
  const tree = {
    type: "group", operator: "AND",
    children: [{ type: "evidence", evidence_id: "E1" }, { type: "symptom", symptom_code: "S1" }],
  };
  assert.throws(() => ctree.updateNode(tree, [0], { evidence_id: "E2" }), /只读/);
  assert.throws(() => ctree.removeNode(tree, [0]), /只读/);
  // 非只读路径可正常变更
  assert.doesNotThrow(() => ctree.updateNode(tree, [1], { symptom_code: "S2" }));
});

// -------------------------------------------------------------- roundtrip

test("jsonToTree / treeToJson roundtrip keeps observation PRESENT", () => {
  const ctree = loadModule();
  const tree = {
    type: "group",
    operator: "OR",
    children: [
      { type: "observation", observation_type: "tongue", code: "T_RED", concept_code: "PRESENT" },
      { type: "vital", vital_code: "SBP", operator: ">", threshold_value: 180 },
    ],
  };
  const roundtripped = ctree.jsonToTree(ctree.treeToJson(tree));
  assert.deepEqual(plain(roundtripped), plain(tree));
});

test("pathKey / parsePathKey roundtrip", () => {
  const ctree = loadModule();
  assert.equal(ctree.pathKey([0, 2, 1]), "0-2-1");
  assert.deepEqual(plain(ctree.parsePathKey("0-2-1")), [0, 2, 1]);
  assert.deepEqual(plain(ctree.parsePathKey("")), []);
});

test("newLeaf presets observation and vital defaults", () => {
  const ctree = loadModule();
  assert.deepEqual(plain(ctree.newLeaf("observation")), {
    type: "observation", observation_type: "symptom", concept_code: "PRESENT",
  });
  assert.deepEqual(plain(ctree.newLeaf("vital")), { type: "vital", operator: ">" });
  assert.deepEqual(plain(ctree.newLeaf("symptom")), { type: "symptom" });
  assert.throws(() => ctree.newLeaf("evidence"), /不支持的叶子类型/);
});

test("countLeaves counts leaf nodes across nesting", () => {
  const ctree = loadModule();
  assert.equal(ctree.countLeaves(LAB_SYMPTOM), 2);
  assert.equal(ctree.countLeaves({ type: "symptom", symptom_code: "S1" }), 1);
});

test("treeToText renders readable Chinese text", () => {
  const ctree = loadModule();
  const text = ctree.treeToText(LAB_SYMPTOM);
  assert.ok(text.includes("CTN"));
  assert.ok(text.includes("SYM_PAIN"));
  const orText = ctree.treeToText({ ...LAB_SYMPTOM, operator: "OR" });
  assert.ok(orText.includes("或"));
});
