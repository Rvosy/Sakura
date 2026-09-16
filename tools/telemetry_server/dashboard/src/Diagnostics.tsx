import { useEffect, useMemo, useState } from "react";
import { useApi, ApiError } from "./api";
import { Panel, QueryState } from "./components";
import { href, navigate } from "./state";
import type { Route, Row } from "./types";

type Filters = Record<string, string | boolean>;
export function routeFilters(route: Route): Filters {
  const f: Filters = {};
  for (const key of [
    "build",
    "version",
    "platform",
    "severity",
    "component",
    "reason",
    "installation",
    "run",
    "generation",
    "operation",
    "group",
  ] as const) {
    if (route[key]) f[key] = route[key]!;
  }
  if (route.query) f.q = route.query;
  if (route.includeTest) f.includeTest = true;
  return f;
}
function params(filters: Filters) {
  return new URLSearchParams(
    Object.entries(filters).map(([k, v]) => [k, String(v)]),
  ).toString();
}
export function ExportButton({
  filters = {},
  report,
}: {
  filters?: Filters;
  report?: string;
}) {
  const [scope, setScope] = useState(report ? "report" : "current");
  const [job, setJob] = useState<Row | null>(null);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    if (job?.status !== "running") return;
    const abort = new AbortController();
    const timer = window.setTimeout(async () => {
      try {
        const res = await fetch(`/admin/api/v2/exports/${job.id}`, {
          signal: abort.signal,
          cache: "no-store",
        });
        if (!res.ok) throw new ApiError(res.status);
        const next = await res.json();
        setJob(next);
        if (next.status === "failed")
          setError(
            next.error === "EXPORT_SIZE_LIMIT" ||
              next.error === "EXPORT_TIME_LIMIT"
              ? "数据量超过导出上限，请缩小时间范围。"
              : "导出失败，请重试。",
          );
      } catch (e) {
        if (!abort.signal.aborted) {
          setError(String(e));
          setJob(null);
        }
      }
    }, 1000);
    return () => {
      clearTimeout(timer);
      abort.abort();
    };
  }, [job]);
  async function start() {
    setStarting(true);
    setError("");
    setJob(null);
    try {
      const selected =
        scope === "all" ? {} : scope === "report" ? { report } : filters;
      const res = await fetch("/admin/api/v2/exports", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(selected),
      });
      if (res.status === 409)
        throw new Error("已有分析包正在生成，请稍后重试。");
      if (!res.ok) throw new ApiError(res.status);
      setJob(await res.json());
    } catch (e) {
      setError(String(e));
    } finally {
      setStarting(false);
    }
  }
  return (
    <div className="export-controls">
      <select
        className="form-select"
        aria-label="分析包范围"
        value={scope}
        onChange={(e) => setScope(e.target.value)}
        disabled={starting || job?.status === "running"}
      >
        <option value="current">当前筛选</option>
        {report && <option value="report">此报告及同运行上下文</option>}
        <option value="all">全部保留数据</option>
      </select>
      <button
        className="btn"
        onClick={start}
        disabled={starting || job?.status === "running"}
      >
        {starting || job?.status === "running"
          ? "正在生成分析包…"
          : "生成分析包"}
      </button>
      {job?.status === "ready" && (
        <a
          className="btn btn-primary"
          href={`/admin/api/v2/exports/${job.id}/download`}
        >
          下载 ZIP
        </a>
      )}
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
export function Records({ kind, filters }: { kind: string; filters: Filters }) {
  const [cursor, setCursor] = useState("");
  const queryKey = params(filters);
  useEffect(() => setCursor(""), [queryKey, kind]);
  const q = useApi(
    `v2/${kind === "groups" ? "groups" : kind === "timeline" ? "timeline" : `records/${kind}`}?${queryKey}&limit=100${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
  );
  return (
    <QueryState query={q}>
      {(d) => (
        <>
          <p className="records-count">
            共 {d.total} {kind === "groups" ? "个问题组" : "条记录"}
          </p>
          <div className="diagnostic-table">
            <table className="table">
              <thead>
                <tr>
                  {(kind === "groups"
                    ? ["问题", "原因 / 阶段", "报告 / 出现 / 安装", "最近收到"]
                    : [
                        "事件 / 错误",
                        "构建 / 运行",
                        "原因 / 位置 / 结果",
                        "发生 / 接收时间",
                      ]
                  ).map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.items.map((r: Row, i: number) => (
                  <tr key={r.id ?? `${r.fingerprint}-${i}`}>
                    <td>
                      {kind === "groups" ? (
                        <button
                          className="btn btn-sm"
                          onClick={() =>
                            navigate({
                              group: r.fingerprint,
                              tab: "reports",
                              cursor: undefined,
                            })
                          }
                        >
                          {r.error_code}
                        </button>
                      ) : r.report_id ? (
                        <a href={href({ report: r.report_id })}>
                          {r.error_code}
                        </a>
                      ) : (
                        r.event || r.purpose
                      )}
                      <small>
                        {r.evidence?.diagnostic ||
                          r.diagnostic ||
                          r.component ||
                          r.outcome ||
                          ""}
                      </small>
                    </td>
                    <td>
                      {kind === "groups" ? (
                        <>
                          {r.reason_code ?? "原因未知"}
                          <small>{r.stage ?? "阶段未知"}</small>
                        </>
                      ) : (
                        <>
                          {r.build_id ?? "构建未知"}
                          <small>
                            {r.run_id} / {r.generation ?? "generation 未知"}
                          </small>
                          {r.operation_id && (
                            <button
                              className="btn btn-sm"
                              onClick={() =>
                                navigate({
                                  view: "diagnostics",
                                  tab: "timeline",
                                  installation: r.installation_id,
                                  run: r.run_id,
                                  generation: r.generation || undefined,
                                  operation: r.operation_id,
                                  group: undefined,
                                })
                              }
                            >
                              操作时间线
                            </button>
                          )}
                        </>
                      )}
                    </td>
                    <td>
                      {kind === "groups" ? (
                        `${r.reports} / ${r.occurrences} / ${r.installations}`
                      ) : (
                        <>
                          {r.reason_code || r.details?.reasonCode || "原因未知"}
                          <small>
                            {r.details?.file
                              ? `${r.details.file}:${r.details.line ?? "?"}`
                              : "位置未知"}
                          </small>
                          {r.details?.outcome || r.outcome || "终态未知"}
                          <details>
                            <summary>诊断字段</summary>
                            <pre>{JSON.stringify(r, null, 2)}</pre>
                          </details>
                        </>
                      )}
                    </td>
                    <td>
                      {kind === "groups" ? (
                        r.last_seen
                      ) : (
                        <>
                          {r.occurred_ms == null
                            ? "发生时间未知"
                            : `运行起点 +${r.occurred_ms} ms`}
                          <small>{r.received_at_iso}</small>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!d.items.length && <p className="records-empty">没有匹配记录。</p>}
          <div className="diagnostic-pagination">
            <button
              className="btn"
              disabled={!cursor}
              onClick={() => setCursor("")}
            >
              首页
            </button>
            <button
              className="btn"
              disabled={!d.hasMore}
              onClick={() => setCursor(d.nextCursor)}
            >
              下一页
            </button>
          </div>
        </>
      )}
    </QueryState>
  );
}
export function Diagnostics({ route }: { route: Route }) {
  const [start, setStart] = useState(route.start || "");
  const [end, setEnd] = useState(route.end || "");
  const filters = useMemo(
    () => ({
      ...routeFilters(route),
      ...(route.start ? { start: route.start } : {}),
      ...(route.end ? { end: route.end } : {}),
    }),
    [JSON.stringify(route)],
  );
  const quality = useApi(`v2/quality?${params(filters)}`);
  const [kind, setKind] = useState("events");
  return (
    <>
      <Panel title="诊断筛选" className="diagnostic-panel">
        <form
          className="diagnostic-filters"
          onSubmit={(e) => {
            e.preventDefault();
            const values = new FormData(e.currentTarget);
            const patch: Row = {};
            for (const [k, v] of values) patch[k] = String(v) || undefined;
            patch.start = start ? new Date(start).toISOString() : undefined;
            patch.end = end ? new Date(end).toISOString() : undefined;
            patch.includeTest = values.get("includeTest") === "on";
            navigate(patch);
          }}
        >
          {[
            ["query", "错误原文、错误码或指纹"],
            ["build", "构建"],
            ["version", "版本"],
            ["platform", "平台"],
            ["component", "组件"],
            ["reason", "原因码"],
            ["severity", "严重程度"],
            ["installation", "Installation ID"],
            ["run", "Run ID"],
            ["generation", "Generation"],
            ["operation", "Operation ID"],
            ["group", "问题指纹"],
          ].map(([k, label]) => (
            <label key={`${k}-${String((route as unknown as Row)[k])}`}>
              {label}
              <input
                className="form-control"
                name={k}
                defaultValue={(route as unknown as Row)[k] || ""}
              />
            </label>
          ))}
          <label>
            开始时间（本机时区）
            <input
              className="form-control"
              type="datetime-local"
              value={start}
              onChange={(e) => setStart(e.target.value)}
            />
          </label>
          <label>
            结束时间（本机时区）
            <input
              className="form-control"
              type="datetime-local"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
            />
          </label>
          <div className="diagnostic-filter-actions">
            <label className="diagnostic-test-toggle">
              <input
                type="checkbox"
                name="includeTest"
                defaultChecked={route.includeTest}
              />{" "}
              包含开发与验收数据
            </label>
            <button className="btn btn-primary">应用筛选</button>
          </div>
        </form>
        <div className="diagnostic-export">
          <ExportButton filters={filters} />
        </div>
      </Panel>
      <div className="diagnostic-tabs" aria-label="诊断视图">
        {[
          ["groups", "问题组"],
          ["reports", "错误报告"],
          ["timeline", "操作时间线"],
          ["quality", "数据质量"],
        ].map(([tab, label]) => (
          <button
            className="btn"
            aria-pressed={(route.tab || "groups") === tab}
            key={tab}
            onClick={() => navigate({ tab })}
          >
            {label}
          </button>
        ))}
      </div>
      {route.tab === "quality" ? (
        <Panel title="字段覆盖" className="diagnostic-panel">
          <QueryState query={quality}>
            {(d) => (
              <>
                <p>
                  计数是缺失数。未收到记录不能视作零故障；旧样本只能按显式
                  acceptance 标记排除。
                </p>
                <div className="diagnostic-table">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Schema / 构建</th>
                        <th>报告数</th>
                        <th>位置覆盖</th>
                        <th>原因覆盖</th>
                        <th>操作关联覆盖</th>
                      </tr>
                    </thead>
                    <tbody>
                      {d.items.map((r: Row, i: number) => (
                        <tr key={i}>
                          <td>
                            {r.schema_version ?? "v1"} /{" "}
                            {r.build_id ?? "构建未知"}
                          </td>
                          <td>{r.reports}</td>
                          {[
                            r.missing_location,
                            r.missing_reason,
                            r.missing_operation,
                          ].map((v: number, n: number) => (
                            <td key={n}>
                              {r.reports
                                ? (((r.reports - v) / r.reports) * 100).toFixed(
                                    1,
                                  ) + "%"
                                : "未知"}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <details>
                  <summary>采集与服务器累计计数</summary>
                  <p>
                    客户端仅统计收到摘要的运行；服务器计数从本次服务启动开始。
                  </p>
                  <pre>
                    {JSON.stringify(
                      { client: d.client, server: d.server },
                      null,
                      2,
                    )}
                  </pre>
                </details>
              </>
            )}
          </QueryState>
        </Panel>
      ) : route.tab === "timeline" ? (
        <Panel title="操作时间线" className="diagnostic-panel">
          {!route.installation || !route.run ? (
            <p>
              请先指定 Installation ID 和 Run ID。旧链接只有 Operation ID
              时，需要选择具体运行。
            </p>
          ) : (
            <>
              <p>同一运行内按相对时间比较；generation 缺失的旧记录保留未知。</p>
              <Records kind="timeline" filters={filters} />
            </>
          )}
        </Panel>
      ) : (
        <Panel
          title={route.tab === "reports" ? "错误报告" : "问题组"}
          className="diagnostic-panel"
        >
          <Records
            kind={route.tab === "reports" ? "errors" : "groups"}
            filters={filters}
          />
        </Panel>
      )}
    </>
  );
}
