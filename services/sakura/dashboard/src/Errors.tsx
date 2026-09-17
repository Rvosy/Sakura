import { ThemeSelect } from "./ThemeSelect";
import { useMemo } from "react";
import { Tabs, Tab } from "react-bootstrap";
import { Search, ArrowUpRight, X } from "lucide-react";
import type { ColumnDef } from "@tanstack/react-table";
import { useApi } from "./api";
import { href, navigate } from "./state";
import type { Row, Route } from "./types";
import {
  DataTable,
  Panel,
  QueryState,
  Severity,
  Trend,
  Id,
  textCol,
  numCol,
  fmt,
} from "./components";
export const reportColumns: ColumnDef<Row, any>[] = [
  {
    accessorKey: "error_code",
    header: "错误",
    cell: ({ row }) => (
      <div className="issue-cell">
        <a href={href({ report: row.original.report_id })}>
          {row.original.error_code}
          <ArrowUpRight size={13} />
        </a>
        <small>
          {row.original.diagnostic ||
            `${row.original.component} · ${row.original.event}`}
        </small>
      </div>
    ),
  },
  {
    accessorKey: "severity",
    header: "等级",
    cell: ({ getValue }) => <Severity value={getValue()} />,
  },
  textCol("app_version", "版本"),
  textCol("platform", "平台"),
  textCol("received_at", "出现时间"),
  {
    accessorKey: "report_id",
    header: "Report ID",
    cell: ({ getValue }) => <Id value={getValue()} />,
  },
];
export function ErrorFilters({
  route,
  versions,
  platforms,
}: {
  route: Route;
  versions: string[];
  platforms: string[];
}) {
  const active =
    route.query || route.severity || route.version || route.platform;
  return (
    <div className="filters">
      <div className="search-input">
        <Search size={16} />
        <input
          aria-label="搜索错误"
          placeholder="错误码、组件或定位信息…"
          value={route.query}
          onChange={(e) => navigate({ query: e.target.value }, true)}
        />
      </div>
      <ThemeSelect
        label="错误等级"
        value={route.severity}
        onChange={(severity) => navigate({ severity })}
        options={[
          { value: "", label: "所有等级" },
          { value: "critical", label: "严重" },
          { value: "error", label: "错误" },
          { value: "warning", label: "警告" },
          { value: "__unknown", label: "未分类" },
        ]}
      />
      {route.tab === "recent" && (
        <>
          <ThemeSelect
            label="版本"
            value={route.version}
            onChange={(version) => navigate({ version })}
            options={[
              { value: "", label: "所有版本" },
              ...versions.map((v) => ({ value: v, label: v })),
            ]}
          />
          <ThemeSelect
            label="平台"
            value={route.platform}
            onChange={(platform) => navigate({ platform })}
            options={[
              { value: "", label: "所有平台" },
              ...platforms.map((p) => ({ value: p, label: p })),
            ]}
          />
        </>
      )}
      {active && (
        <button
          className="btn btn-ghost-secondary"
          onClick={() =>
            navigate({ query: "", severity: "", version: "", platform: "" })
          }
        >
          <X size={15} />
          清除筛选
        </button>
      )}
    </div>
  );
}
export function Errors({ route }: { route: Route }) {
  const query = useApi(`errors?days=${route.days}&limit=200`);
  return (
    <QueryState query={query}>
      {(data) => <ErrorsContent data={data} route={route} />}
    </QueryState>
  );
}
function ErrorsContent({ data, route }: { data: Row; route: Route }) {
  const recent: Row[] = data.recent || [];
  const groups: Row[] = data.top || [];
  const matches = (r: Row) =>
    Object.values(r).some((v) =>
      String(v ?? "")
        .toLowerCase()
        .includes(route.query.toLowerCase()),
    ) &&
    (!route.severity ||
      (route.severity === "__unknown"
        ? !r.severity
        : r.severity === route.severity));
  const filteredGroups = groups.filter(matches);
  const filteredRecent = recent.filter(
    (r) =>
      matches(r) &&
      (!route.version || r.app_version === route.version) &&
      (!route.platform || r.platform === route.platform),
  );
  const columns = useMemo<ColumnDef<Row, any>[]>(
    () => [
      {
        accessorKey: "error_code",
        header: "错误 / 发生位置",
        cell: ({ row }) => (
          <div className="issue-cell">
            <button
              className="text-link"
              onClick={() =>
                navigate({
                  tab: "recent",
                  query: row.original.fingerprint || row.original.error_code,
                })
              }
            >
              {row.original.error_code}
              <ArrowUpRight size={13} />
            </button>
            <small>
              {row.original.diagnostic ||
                `${row.original.component} · ${row.original.event}`}
            </small>
            <small className="mono">
              {row.original.location || "未记录位置"}
            </small>
          </div>
        ),
      },
      {
        accessorKey: "severity",
        header: "等级",
        cell: ({ getValue }) => <Severity value={getValue()} />,
      },
      numCol("occurrences", "报告数"),
      numCol("installations", "受影响安装"),
      textCol("last_seen", "最近出现"),
      {
        id: "details",
        header: "详情",
        enableSorting: false,
        cell: ({ row }) => (
          <details className="inline-details">
            <summary>分组信息</summary>
            <p>{row.original.fingerprint || "无指纹"}</p>
            <p>首次：{row.original.first_seen}</p>
          </details>
        ),
      },
    ],
    [],
  );
  return (
    <>
      <div className="split-grid">
        <Panel title="报告趋势" note="北京时间 · 按日统计">
          <Trend data={data.daily} />
        </Panel>
        <Panel title="版本分布" note="所选时间内的错误报告">
          <div className="version-bars">
            {data.byVersion.length ? (
              data.byVersion.slice(0, 5).map((v: Row) => (
                <div key={v.app_version}>
                  <button
                    className="text-link"
                    onClick={() =>
                      navigate({ tab: "recent", version: v.app_version })
                    }
                  >
                    {v.app_version}
                  </button>
                  <div className="bar-track">
                    <span
                      style={{
                        width: `${(v.occurrences / Math.max(...data.byVersion.map((x: Row) => x.occurrences), 1)) * 100}%`,
                      }}
                    />
                  </div>
                  <strong>{fmt(v.occurrences)}</strong>
                </div>
              ))
            ) : (
              <p className="muted">暂无数据</p>
            )}
          </div>
        </Panel>
      </div>
      <Panel
        title="错误列表"
        note="按错误码、等级、位置和指纹聚合；点击错误查看最近报告。"
      >
        <ErrorFilters
          route={route}
          versions={[...new Set(recent.map((r) => r.app_version))]}
          platforms={[...new Set(recent.map((r) => r.platform))]}
        />
        <Tabs
          transition={false}
          activeKey={route.tab === "recent" ? "recent" : "groups"}
          onSelect={(key) =>
            navigate({ tab: key || "groups", version: "", platform: "" })
          }
          className="workspace-tabs"
        >
          <Tab eventKey="groups" title={`错误分组  ${groups.length}`}>
            <p className="scope-note">
              最多返回 50 个高频分组。搜索、排序和分页仅作用于这些分组。
            </p>
            <DataTable
              data={filteredGroups}
              columns={columns}
              label="错误分组"
              search={false}
              initialSort={[{ id: "occurrences", desc: true }]}
            />
          </Tab>
          <Tab eventKey="recent" title={`最近报告  ${recent.length}`}>
            <p className="scope-note">
              最多返回最近 200 条报告。筛选不会检索更早的报告，可按 Report ID
              直接查询。
            </p>
            <DataTable
              data={filteredRecent}
              columns={reportColumns}
              label="最近报告"
              search={false}
            />
          </Tab>
        </Tabs>
      </Panel>
    </>
  );
}
