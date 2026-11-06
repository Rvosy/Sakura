import assert from "node:assert/strict";
import test from "node:test";
import { createRecordTable } from "../settings/record-table.js";
import { browserFixture, settle } from "./fixtures/plugin-settings-fixture.js";

for (const change of ["removed", "disposed", "selected", "cancelled"]) test(`late record result is ignored when ${change}`, async () => {
  const { document } = browserFixture();
  let items = [{ id: "one", label: "设备", values: { name: "设备" } }, { id: "two", label: "另一设备", values: { name: "另一设备" } }], complete;
  const table = createRecordTable({ document, read: key => key === "items" ? items : {}, write() {},
    presentation: { itemsField: "items", valueField: "grants", columns: [{ key: "name", label: "名称", readonly: true }] },
    inspect: () => new Promise(resolve => { complete = resolve; }), inspectLabel: "读取", onError: assert.fail,
    enhanceSelect() {}, refreshSelect() {}, closeSelects() {} });
  document.body.append(table.element);
  const pending = table.element.querySelector(".record-inspect").fire("click");
  if (change === "removed") { items = []; table.update(); }
  else if (change === "selected") await table.element.querySelectorAll(".record-choice")[1].fire("click");
  else if (change === "cancelled") table.cancel();
  else table.dispose();
  complete({ id: "one", title: "过期数据", rows: [] }); await pending; await settle();
  assert.equal(document.querySelector(".record-details"), null);
  table.dispose();
});
