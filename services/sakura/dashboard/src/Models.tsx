import { Records, routeFilters } from "./Diagnostics";
import { useMemo, useState } from "react";
import { Accordion, Tabs, Tab } from "react-bootstrap";
import { ArrowUpRight, ChevronRight, X } from "lucide-react";
import { useApi } from "./api";
import { href, navigate } from "./state";
import {
  DataTable,
  Panel,
  QueryState,
  Metrics,
  Composition,
  Trend,
  Id,
  Empty,
  fmt,
  pct,
  short,
  numCol,
  textCol,
} from "./components";
import { Lookup } from "./Details";
import type { Row, Route } from "./types";
export function Models({ route }: { route: Route }) {
  const query = useApi(`model-metrics?days=${route.days}`);
  return (
    <>
      <QueryState query={query}>
        {(d) => (
          <>
            <Metrics
              items={[
                {
                  label: "模型调用",
                  value: fmt(d.summary.calls),
                  note: `成功 ${fmt(d.summary.successful_calls)} 次`,
                },
                {
                  label: "输入 token",
                  value: fmt(d.summary.input_tokens),
                  note: `${fmt(d.summary.usage_samples)} 次调用返回用量`,
                },
                {
                  label: "输出 token",
                  value: fmt(d.summary.output_tokens),
                  note: `平均延迟 ${fmt(d.summary.average_latency_ms)} ms`,
                },
                {
                  label: "Context Window",
                  value: pct(d.summary.contextWindowUsagePercent),
                  note: `按窗口加权 · ${fmt(d.summary.context_samples)} 个样本`,
                },
              ]}
            />
            <Tabs
              transition={false}
              defaultActiveKey={route.installation ? "calls" : "summary"}
              activeKey={route.installation ? "calls" : undefined}
              onSelect={(k) => {
                if (k === "summary" && route.installation)
                  navigate({ installation: undefined, operation: undefined });
              }}
              className="workspace-tabs main-tabs"
            >
              <Tab eventKey="summary" title="整体用量">
                <div className="split-grid even">
                  <Panel title="Context 构成" note="占请求估算 token 的比例">
                    <Composition
                      available={!!d.composition.request_tokens}
                      parts={Object.fromEntries(
                        Object.entries(d.composition.shares).map(
                          ([key, value]) => [key, { share: value }],
                        ),
                      )}
                    />
                  </Panel>
                  <Panel title="窗口使用趋势" note="北京时间 · 每日按窗口加权">
                    <Trend
                      data={d.daily}
                      value="context_window_usage_percent"
                      percent
                    />
                  </Panel>
                </div>
                <div className="split-grid even">
                  <Panel title="模型族">
                    <DataTable
                      data={d.byModel}
                      columns={[
                        textCol("model_family", "模型族"),
                        numCol("calls", "调用"),
                        numCol("input_tokens", "输入 token"),
                        numCol("output_tokens", "输出 token"),
                        numCol("average_latency_ms", "延迟 ms"),
                      ]}
                      label="模型族"
                      search={false}
                    />
                  </Panel>
                  <Panel title="用途与结果">
                    <DataTable
                      data={d.byPurpose}
                      columns={[
                        textCol("purpose", "用途"),
                        textCol("outcome", "结果"),
                        numCol("calls", "调用"),
                      ]}
                      label="用途与结果"
                      search={false}
                    />
                  </Panel>
                </div>
              </Tab>
              <Tab eventKey="calls" title="逐次调用">
                <Panel
                  title="选择安装实例"
                  note="从下方列表选择，或粘贴 Installation ID。"
                >
                  <Lookup kind="installation" />
                </Panel>
                {route.installation && <ModelCalls route={route} />}
              </Tab>
            </Tabs>
            <Panel
              title="有模型调用的安装实例"
              note="最多返回 100 个安装实例，按调用次数排序。"
            >
              <DataTable
                label="模型安装实例"
                data={d.installations}
                columns={[
                  {
                    accessorKey: "installation_id",
                    header: "匿名安装实例",
                    cell: ({ getValue }) => (
                      <a
                        className="mono"
                        href={href({
                          installation: getValue(),
                          operation: undefined,
                        })}
                      >
                        {short(getValue())}
                        <ChevronRight size={13} />
                      </a>
                    ),
                  },
                  numCol("calls", "调用"),
                  numCol("operations", "操作"),
                  {
                    accessorKey: "average_context_window_usage_percent",
                    header: "平均 Window",
                    cell: ({ getValue }) => pct(getValue()),
                  },
                  {
                    accessorKey: "peak_context_window_usage_percent",
                    header: "峰值 Window",
                    cell: ({ getValue }) => (
                      <span className={getValue() >= 80 ? "text-danger" : ""}>
                        {pct(getValue())}
                      </span>
                    ),
                  },
                  textCol("last_seen", "最近调用"),
                ]}
              />
            </Panel>
          </>
        )}
      </QueryState>
    </>
  );
}
function ModelCalls({ route }: { route: Route }) {
  return (
    <Panel title="逐次模型调用">
      <Records kind="model-calls" filters={routeFilters(route)} />
    </Panel>
  );
}
