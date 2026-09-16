import { ThemeSelect } from "./ThemeSelect";
import { useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Copy,
  Check,
  Search,
  RefreshCw,
} from "lucide-react";
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
} from "recharts";
import type { UseQueryResult } from "@tanstack/react-query";
import type { Row } from "./types";
export const fmt = (n: unknown) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 1 }).format(
        Number(n),
      );
export const pct = (n: unknown) =>
  n == null ? "—" : `${Number(n).toFixed(1)}%`;
export const short = (s: string) =>
  s?.length > 20 ? `${s.slice(0, 8)}…${s.slice(-6)}` : s || "—";
export function Id({
  value,
  compact = true,
}: {
  value?: string;
  compact?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  return (
    <span className="id-value">
      <code title={value}>{compact ? short(value || "") : value || "—"}</code>
      {value && (
        <button
          className="icon-btn"
          aria-label="复制完整 ID"
          title={
            copied
              ? "已复制"
              : failed
                ? "复制失败，请选中文本复制"
                : "复制完整 ID"
          }
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(value);
              setCopied(true);
              setFailed(false);
              setTimeout(() => setCopied(false), 1800);
            } catch {
              setFailed(true);
            }
          }}
        >
          {copied ? (
            <Check size={13} className="copy-confirmation" />
          ) : (
            <Copy size={13} />
          )}
          <span className="visually-hidden" role="status">
            {copied ? "已复制" : ""}
          </span>
        </button>
      )}
      {failed && <small role="status">{value}（请手动复制）</small>}
    </span>
  );
}
export function Severity({ value }: { value?: string }) {
  const names: Row = {
    critical: "严重",
    error: "错误",
    warning: "警告",
    info: "信息",
  };
  return (
    <span className={`severity severity-${value || "unknown"}`}>
      <i />
      {names[value || ""] || value || "未分类"}
    </span>
  );
}
export function Empty({
  text = "暂无数据，请调整时间范围。",
}: {
  text?: string;
}) {
  return (
    <div className="empty-state">
      <p>{text}</p>
    </div>
  );
}
export function QueryState({
  query,
  children,
}: {
  query: UseQueryResult<Row>;
  children: (data: Row) => ReactNode;
}) {
  if (query.isPending)
    return (
      <div className="loading-state" role="status">
        <span className="spinner-border spinner-border-sm" /> 正在读取…
      </div>
    );
  return (
    <>
      {query.isError && (
        <div className="alert alert-danger" role="alert">
          <span>
            {query.error.message}
            {query.data && " 下方仍是上次读取的结果。"}
          </span>
          <button className="btn btn-sm" onClick={() => query.refetch()}>
            <RefreshCw size={14} />
            重试
          </button>
        </div>
      )}
      {query.data && children(query.data)}
    </>
  );
}
export function Panel({
  title,
  note,
  action,
  children,
  className = "",
}: {
  title: string;
  note?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {note && <p>{note}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
export function Metrics({
  items,
}: {
  items: { label: string; value: unknown; note: string; tone?: string }[];
}) {
  return (
    <div className="metrics">
      {items.map((m) => (
        <div className={`metric ${m.tone || ""}`} key={m.label}>
          <span>{m.label}</span>
          <strong>{String(m.value)}</strong>
          <small>{m.note}</small>
        </div>
      ))}
    </div>
  );
}
export function DataTable({
  data,
  columns,
  label,
  search = true,
  initialSort = [],
  emptyText,
}: {
  data: Row[];
  columns: ColumnDef<Row, any>[];
  label: string;
  search?: boolean;
  initialSort?: SortingState;
  emptyText?: string;
}) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const [filter, setFilter] = useState("");
  const table = useReactTable({
    data,
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: 10 } },
    autoResetPageIndex: true,
  });
  return (
    <div className="data-table">
      {search && (
        <div className="table-search">
          <Search size={15} />
          <input
            aria-label={`搜索${label}`}
            placeholder="搜索当前列表…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
      )}
      <div className="table-responsive">
        <table className="table table-hover" aria-label={label}>
          <thead>
            {table.getHeaderGroups().map((g) => (
              <tr key={g.id}>
                {g.headers.map((h) => (
                  <th
                    key={h.id}
                    aria-sort={
                      h.column.getIsSorted() === "asc"
                        ? "ascending"
                        : h.column.getIsSorted() === "desc"
                          ? "descending"
                          : undefined
                    }
                  >
                    {h.column.getCanSort() ? (
                      <button
                        className="sort-btn"
                        onClick={h.column.getToggleSortingHandler()}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {h.column.getIsSorted() === "asc" ? (
                          <ArrowUp size={12} />
                        ) : h.column.getIsSorted() === "desc" ? (
                          <ArrowDown size={12} />
                        ) : (
                          <ArrowUpDown size={12} />
                        )}
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((c) => (
                  <td key={c.id}>
                    {flexRender(c.column.columnDef.cell, c.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {!table.getRowModel().rows.length && (
          <Empty text={emptyText || "没有匹配的记录，试试清除筛选。"} />
        )}
      </div>
      <div className="table-footer">
        <span>
          {fmt(table.getFilteredRowModel().rows.length)} 条
          <span className="muted"> · 当前返回结果</span>
        </span>
        <div className="pagination-controls">
          <ThemeSelect
            label="每页条数"
            value={table.getState().pagination.pageSize}
            onChange={(size) => table.setPageSize(Number(size))}
            options={[10, 25, 50].map((n) => ({
              value: n,
              label: `${n} 条 / 页`,
            }))}
          />
          <button
            className="icon-btn"
            aria-label="上一页"
            disabled={!table.getCanPreviousPage()}
            onClick={() => table.previousPage()}
          >
            <ChevronLeft size={17} />
          </button>
          <span>
            {table.getPageCount()
              ? table.getState().pagination.pageIndex + 1
              : 0}{" "}
            / {table.getPageCount()}
          </span>
          <button
            className="icon-btn"
            aria-label="下一页"
            disabled={!table.getCanNextPage()}
            onClick={() => table.nextPage()}
          >
            <ChevronRight size={17} />
          </button>
        </div>
      </div>
    </div>
  );
}
export function Trend({
  data,
  value = "occurrences",
  percent = false,
}: {
  data: Row[];
  value?: string;
  percent?: boolean;
}) {
  return data.length ? (
    <div
      className="chart"
      role="img"
      aria-label={data
        .map((r) => `${r.day}: ${percent ? pct(r[value]) : fmt(r[value])}`)
        .join("；")}
    >
      <ResponsiveContainer width="100%" height="100%">
        <BarChart
          data={data}
          margin={{ top: 15, right: 15, bottom: 0, left: -20 }}
        >
          <CartesianGrid vertical={false} stroke="var(--hairline)" />
          <XAxis
            dataKey="day"
            tickFormatter={(v) => v.slice(5)}
            axisLine={false}
            tickLine={false}
            minTickGap={25}
            fontSize={11}
          />
          <YAxis
            axisLine={false}
            tickLine={false}
            fontSize={11}
            tickFormatter={(v) => (percent ? `${v}%` : fmt(v))}
          />
          <Tooltip
            labelFormatter={(v) => `${v} · 北京时间`}
            formatter={(v) => [
              percent ? pct(v) : fmt(v),
              percent ? "Window 使用率" : "报告数",
            ]}
          />
          <Bar
            dataKey={value}
            fill={
              percent ? "var(--sakura-primary-hover)" : "var(--sakura-primary)"
            }
            radius={[3, 3, 0, 0]}
            maxBarSize={30}
            isAnimationActive={false}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  ) : (
    <Empty />
  );
}
export const PARTS = [
  {
    key: "history",
    label: "历史消息",
    en: "History",
    color: "var(--sakura-primary)",
  },
  { key: "memory", label: "记忆", en: "Memory", color: "var(--sakura-accent)" },
  {
    key: "dynamicContext",
    label: "动态上下文",
    en: "Dynamic",
    color: "var(--sakura-primary-hover)",
  },
  {
    key: "toolSchema",
    label: "工具定义",
    en: "Tools",
    color:
      "color-mix(in srgb, var(--sakura-primary) 45%, var(--sakura-accent))",
  },
  {
    key: "other",
    label: "其他",
    en: "Other",
    color: "var(--sakura-muted-text)",
  },
];
export function Composition({
  parts,
  available = true,
}: {
  parts: Row;
  available?: boolean;
}) {
  if (!available) return <Empty text="这次调用没有 Context 估算数据。" />;
  return (
    <div className="composition">
      <div className="composition-strip">
        {PARTS.map((p) => (
          <span
            key={p.key}
            style={{
              width: `${Math.max(0, Math.min(100, parts[p.key]?.share || 0))}%`,
              backgroundColor: p.color,
            }}
            title={`${p.label} ${pct(parts[p.key]?.share)}`}
          />
        ))}
      </div>
      <div className="composition-legend">
        {PARTS.map((p) => (
          <div key={p.key}>
            <span>
              <i style={{ backgroundColor: p.color }} />
              {p.label} <small>{p.en}</small>
            </span>
            <strong>{pct(parts[p.key]?.share)}</strong>
            {parts[p.key]?.tokens != null && (
              <small>{fmt(parts[p.key].tokens)} token</small>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
export const textCol = (key: string, title: string): ColumnDef<Row, any> => ({
  accessorKey: key,
  header: title,
  cell: (info) => (
    <span title={String(info.getValue() ?? "")} className="cell-text">
      {String(info.getValue() ?? "—")}
    </span>
  ),
});
export const numCol = (key: string, title: string): ColumnDef<Row, any> => ({
  accessorKey: key,
  header: title,
  cell: (info) => <span className="numeric">{fmt(info.getValue())}</span>,
});
