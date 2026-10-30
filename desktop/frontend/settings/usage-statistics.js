import { createDateRangePicker, formatDate } from "../core/date-range-picker.js";
import { createIcon } from "../core/icons.js";

const number = new Intl.NumberFormat("zh-CN");
const compact = new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 });
const format = value => value == null ? "—" : number.format(value);
let chartsReady;

export function trendBuckets(snapshot) {
  const hourly = snapshot.granularity === "hour", field = hourly ? "hourly" : "daily", keyField = hourly ? "hour" : "day";
  const days = new Map(snapshot[field].map(row => [row[keyField], { ...row, turns: 0 }]));
  for (const row of snapshot.conversation[field]) days.set(row[keyField], { ...days.get(row[keyField]), ...row });
  const end = new Date(snapshot.end);
  const start = snapshot.start ? new Date(snapshot.start) : days.size ? new Date([...days.keys()].sort()[0] + "T00:00:00") : new Date(end);
  const monthly = (end - start) / 86400000 > 62;
  if (hourly) start.setMinutes(0, 0, 0);
  const buckets = new Map();
  for (const day = new Date(start); day <= end; hourly ? day.setTime(day.getTime() + 3600000) : day.setDate(day.getDate() + 1)) {
    const key = hourly ? `${formatDate(day)}T${String(day.getHours()).padStart(2, "0")}:00:00` : formatDate(day);
    const label = monthly ? key.slice(0, 7) : key;
    const bucket = buckets.get(label) || { label, total: 0, input: null, output: null, turns: 0, measured: 0, calls: 0 };
    const row = days.get(key) || {};
    bucket.total += row.totalTokens || 0;
    if (row.inputTokens != null) bucket.input = (bucket.input || 0) + row.inputTokens;
    if (row.outputTokens != null) bucket.output = (bucket.output || 0) + row.outputTokens;
    bucket.measured += row.measuredCalls || 0;
    bucket.calls += row.calls || 0;
    bucket.turns += row.turns || 0;
    buckets.set(label, bucket);
  }
  return [...buckets.values()].map(row => ({ ...row, total: row.calls && !row.measured ? null : row.total }));
}

export function createUsageStatistics({ document, invoke }) {
  const root = document.getElementById("page-statistics"), win = document.defaultView;
  let active = false, disposed = false, revision = 0, selection = { period: "30d" };
  let view = "tokens", chartType = "line", dimension = "categories", snapshot = null, chart = null, chartLibrary = null;
  const node = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = text;
    return element;
  };
  const button = (label, handler) => {
    const element = node("button", "secondary-button", label);
    element.type = "button"; element.addEventListener("click", handler); return element;
  };
  function tabs(choices, selected, label, change) {
    const group = node("div", "plugin-role-tabs usage-tabs");
    group.setAttribute("role", "group"); group.setAttribute("aria-label", label);
    group.style.setProperty("--tab-count", choices.length);
    const items = [];
    function select(key) {
      group.style.setProperty("--active-tab", choices.findIndex(choice => choice[0] === key));
      items.forEach(([value, item]) => { item.classList.toggle("is-active", key === value); item.setAttribute("aria-pressed", String(key === value)); });
    }
    for (const [key, text] of choices) {
      const item = button(text, () => { select(key); change(key); }); item.className = "plugin-role-tab";
      items.push([key, item]); group.append(item);
    }
    select(selected); return group;
  }
  const toolbar = node("div", "usage-toolbar");
  const datePicker = createDateRangePicker({ document, id: "usage", initialDays: 30, anchored: true,
    isCurrent: () => active && !disposed,
    onChange() { selection = { period: "custom", ...datePicker.getValue() }; void refresh(true); },
    async loadRange() {
      const result = await invoke("settings_usage_statistics_get", { period: "all" });
      const dates = [result.earliest?.slice(0, 10), result.conversation.daily[0]?.day].filter(Boolean).sort();
      return { startDate: dates[0] || formatDate(new Date()), endDate: formatDate(new Date()) };
    },
  });
  const status = node("span", "usage-status"); status.setAttribute("role", "status");
  const refreshButton = button("", () => void refresh()); refreshButton.classList.add("usage-refresh");
  refreshButton.append(createIcon(document, "refresh-cw")); refreshButton.setAttribute("aria-label", "刷新统计");
  const toolbarRight = node("div", "usage-toolbar-right"); toolbarRight.append(status, refreshButton);
  toolbar.append(datePicker.element, toolbarRight);
  const overview = node("div", "usage-overview"), fields = {};
  for (const [key, title] of [["tokens", "总 Token"], ["turns", "对话轮数"], ["calls", "模型回复"]]) {
    const card = node("div", `usage-card usage-card-${key}`), value = node("div", "usage-value", "—"), note = node("div", "usage-note");
    card.append(node("div", "usage-label", title), value, note); overview.append(card); fields[key] = { value, note };
  }
  const breakdown = node("div", "usage-breakdown");
  const chartSection = node("section", "usage-trend"), chartHeading = node("div", "usage-section-heading");
  const chartControls = node("div", "usage-chart-controls");
  chartControls.append(
    tabs([["tokens", "Token"], ["turns", "对话"]], view, "趋势指标", key => { view = key; updateChart(); }),
    tabs([["line", "曲线"], ["bar", "柱状"]], chartType, "图表类型", key => { chartType = key; updateChart(); }),
  );
  chartHeading.append(node("h3", null, "使用趋势"), chartControls);
  const chartHost = node("div", "usage-chart"); chartHost.setAttribute("role", "img"); chartHost.setAttribute("aria-label", "用量趋势");
  const chartEmpty = node("div", "usage-chart-empty", "这段时间暂无记录"); chartEmpty.hidden = true;
  const chartFrame = node("div", "usage-chart-frame"); chartFrame.append(chartHost, chartEmpty);
  chartSection.append(chartHeading, chartFrame);
  const detail = node("section", "usage-detail"), detailHeading = node("div", "usage-section-heading");
  detailHeading.append(node("h3", null, "用量分布"), tabs([["categories", "功能"], ["models", "模型"], ["plugins", "插件"]], dimension,
    "统计维度", key => { dimension = key; renderTable(); }));
  const tableScroll = node("div", "usage-table-scroll"), table = node("table", "usage-table"), head = node("thead"), body = node("tbody");
  table.append(head, body); tableScroll.append(table); detail.append(detailHeading, tableScroll);
  root.append(toolbar, overview, breakdown, chartSection, detail);

  function renderTable() {
    if (!snapshot) return;
    head.replaceChildren(); body.replaceChildren();
    const tr = node("tr");
    for (const text of [({ categories: "功能", models: "模型", plugins: "插件" })[dimension], "回复次数", "输入 Token", "输出 Token", "总 Token"]) {
      const th = node("th", null, text); th.scope = "col"; tr.append(th);
    }
    head.append(tr);
    const rows = snapshot[dimension];
    const maximum = Math.max(1, ...rows.map(row => row.totalTokens || 0));
    for (const row of rows) {
      const tr = node("tr"), name = node("td");
      name.append(node("span", "usage-row-name", row.label || row.model || "未分类"));
      const track = node("span", "usage-share"), fill = node("span");
      fill.style.width = `${(row.totalTokens || 0) / maximum * 100}%`; track.append(fill); name.append(track); tr.append(name);
      for (const value of [row.calls, row.inputTokens, row.outputTokens, row.totalTokens]) tr.append(node("td", null, format(value)));
      body.append(tr);
    }
    if (!rows.length) { const tr = node("tr"), td = node("td", "usage-empty", "暂无用量记录"); td.colSpan = 5; tr.append(td); body.append(tr); }
  }

  function tooltip(params, buckets) {
    const row = buckets[params[0]?.dataIndex];
    if (!row) return "";
    const content = node("div", "usage-tooltip"); content.append(node("strong", null, row.label.replace("T", " ").slice(0, 16)));
    const values = view === "tokens" ? [["总 Token", row.total], ["输入", row.input], ["输出", row.output]] : [["对话轮数", row.turns]];
    for (const [label, value] of values) { const line = node("div"); line.append(node("span", null, label), node("b", null, format(value))); content.append(line); }
    return content;
  }
  function updateChart(resetRange = false) {
    if (!active || !snapshot || !chartLibrary) return;
    if (!chart) { chart = chartLibrary.init(chartHost, null, { renderer: "svg" }); resize.observe(chartHost); }
    const styles = win.getComputedStyle(document.documentElement), color = key => styles.getPropertyValue(key).trim();
    const primary = color("--sakura-primary"), muted = color("--sakura-muted-text");
    const area = { color: primary, opacity: .08 };
    const buckets = trendBuckets(snapshot), values = buckets.map(row => view === "tokens" ? row.total : row.turns);
    const reduced = win.matchMedia("(prefers-reduced-motion: reduce)").matches;
    chartEmpty.hidden = buckets.some(row => view === "tokens" ? row.calls > 0 : row.turns > 0);
    chartHost.setAttribute("aria-label", `${view === "tokens" ? "Token" : "对话"}趋势，${buckets.length} 个时间点`);
    chart.setOption({
      animation: !reduced, animationDuration: 650, animationDurationUpdate: 380, animationEasing: "cubicOut", animationEasingUpdate: "cubicOut",
      color: [primary], backgroundColor: "transparent",
      grid: { top: 24, left: 54, right: 20, bottom: 77 },
      tooltip: { trigger: "axis", confine: true, borderWidth: 0, padding: 12,
        backgroundColor: color("--sakura-input-bg"), textStyle: { color: color("--sakura-text"), fontSize: 12 },
        extraCssText: "border-radius:10px;box-shadow:0 6px 28px #0002;", formatter: params => tooltip(params, buckets),
        axisPointer: { type: "line", animation: false, lineStyle: { color: muted, width: 1, type: "dashed", opacity: .35 } } },
      xAxis: { type: "category", data: buckets.map(row => row.label), boundaryGap: chartType === "bar",
        axisLine: { show: false }, axisTick: { show: false }, axisLabel: { color: muted, fontSize: 11, hideOverlap: true,
          formatter: value => snapshot.granularity === "hour" ? value.slice(11, 16) : value.slice(value.length === 7 ? 2 : 5), margin: 15 } },
      yAxis: { type: "value", minInterval: 1, splitNumber: 3, axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { color: muted, fontSize: 11, formatter: value => compact.format(value) }, splitLine: { show: false } },
      dataZoom: [{ type: "slider", throttle: 0, left: "6%", right: "6%", bottom: 4, height: 24,
        // 为手柄保留间距，避免最小范围时交叠、越过。
        minSpan: 8 / (chartHost.clientWidth * .88) * 100,
        borderColor: "transparent", backgroundColor: "transparent",
        fillerColor: chartLibrary.color.modifyAlpha(primary, .09), dataBackground: { lineStyle: { color: primary, opacity: .3 }, areaStyle: { color: primary, opacity: .08 } },
        selectedDataBackground: { lineStyle: { color: primary }, areaStyle: { color: primary, opacity: .12 } },
        handleStyle: { color: color("--sakura-input-bg"), borderColor: primary, borderWidth: 1 },
        moveHandleSize: 0, handleLabel: { show: false },
        emphasis: { handleLabel: { show: false } }, brushSelect: false,
        ...(resetRange ? { start: 0, end: 100 } : {}) }],
      series: [{ id: "usage", name: view === "tokens" ? "Token" : "对话轮数", type: chartType, data: values,
        smooth: .3, symbol: "circle", symbolSize: 5, showSymbol: true, showAllSymbol: "auto",
        lineStyle: { width: 2.5, cap: "round" },
        areaStyle: chartType === "line" ? area : undefined,
        barMaxWidth: 28, itemStyle: { borderRadius: [6, 6, 0, 0] },
        emphasis: { scale: false, areaStyle: area, lineStyle: { width: 2.5 }, itemStyle: { color: primary, opacity: .82 } },
      }],
    }, { replaceMerge: ["series"] });
    chart.dispatchAction({ type: "hideTip" });
  }
  const resize = new win.ResizeObserver(() => {
    if (chart && (chart.getWidth() !== chartHost.clientWidth || chart.getHeight() !== chartHost.clientHeight)) chart.resize();
  });
  const theme = new win.MutationObserver(() => updateChart());
  function render(resetRange) {
    const s = snapshot.summary, c = snapshot.conversation;
    fields.tokens.value.textContent = format(s.calls ? s.totalTokens : 0);
    fields.tokens.note.textContent = `输入 ${format(s.inputTokens)} · 输出 ${format(s.outputTokens)}`;
    fields.turns.value.textContent = format(c.turns); fields.turns.note.textContent = `${format(c.characters)} 个角色`;
    fields.calls.value.textContent = format(s.calls); fields.calls.note.textContent = `${format(s.measuredCalls)} 次返回用量`;
    breakdown.replaceChildren(node("span", null, `缓存输入 ${format(s.cachedTokens)}`), node("span", null, `推理输出 ${format(s.reasoningTokens)}`));
    renderTable(); updateChart(resetRange);
  }
  async function refresh(resetRange = false) {
    const ticket = ++revision;
    refreshButton.disabled = true; refreshButton.setAttribute("aria-busy", "true");
    status.textContent = snapshot ? "正在刷新…" : "正在读取…";
    try {
      const [result, library] = await Promise.all([invoke("settings_usage_statistics_get", selection),
        chartsReady ||= import("../vendor/echarts/echarts.esm.min.js")]);
      if (disposed || !active || ticket !== revision) return;
      snapshot = result; chartLibrary = library; render(resetRange);
      status.textContent = `${new Date(result.updatedAt || result.end).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })} 更新`;
    } catch (error) {
      if (disposed || !active || ticket !== revision) return;
      status.textContent = `读取失败：${error.message || error}`;
    } finally {
      if (ticket === revision) { refreshButton.disabled = false; refreshButton.setAttribute("aria-busy", "false"); }
    }
  }
  return {
    onPageChanged(page) {
      const next = page === "statistics";
      if (next === active) return;
      active = next; ++revision;
      if (active) { theme.observe(document.documentElement, { attributes: true, attributeFilter: ["style"] }); void refresh(); }
      else { datePicker.dispose(); resize.disconnect(); theme.disconnect(); chart?.dispose(); chart = null; }
    },
    dispose() { disposed = true; active = false; ++revision; datePicker.dispose(); resize.disconnect(); theme.disconnect(); chart?.dispose(); },
  };
}
