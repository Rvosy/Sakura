import { ArrowRight } from "lucide-react";
import { useApi } from "./api";
import { href } from "./state";
import type { Row, Route } from "./types";
import {
  DataTable,
  Panel,
  QueryState,
  Severity,
  Metrics,
  fmt,
  numCol,
  textCol,
  Empty,
} from "./components";
export function Overview({ route }: { route: Route }) {
  const query = useApi(`overview?days=${route.days}`);
  return (
    <QueryState query={query}>
      {(d) => (
        <>
          <div className="overview-intro">
            <div>
              <p>
                最近入库：{d.latestReceivedAt || "暂无记录"}{" "}
                <span className="muted">· 北京时间</span>
              </p>
            </div>
            <a className="btn btn-primary" href={href({ view: "errors" })}>
              查看错误
              <ArrowRight size={16} />
            </a>
          </div>
          <Metrics
            items={[
              {
                label: "活跃安装",
                value: fmt(d.totals.active_installations),
                note: `近 ${route.days} 天的匿名安装实例`,
              },
              {
                label: "错误报告",
                value: fmt(d.totals.errors),
                note: `错误 ${fmt(d.totals.error_severity_reports)} · 警告 ${fmt(d.totals.warning_reports)} · 未分类 ${fmt(d.totals.unclassified_reports)}`,
                tone: "metric-error",
              },
              {
                label: "模型调用",
                value: fmt(d.totals.model_calls),
                note: "所选时间内的调用次数",
              },
              {
                label: "运行事件",
                value: fmt(d.totals.events),
                note: "启动、运行等上报事件",
              },
            ]}
          />
          <div className="activity-note">
            <span>
              最近 24 小时 <strong>{fmt(d.totals.errors_24h)}</strong> 条报告
            </span>
            <span className="muted">
              前一日 {fmt(d.totals.errors_previous_24h)} 条
            </span>
          </div>
          <Panel
            title="高频错误"
            note="先按出现次数排查，再看受影响的安装范围。"
            action={
              <a className="text-link" href={href({ view: "errors" })}>
                全部错误 <ArrowRight size={14} />
              </a>
            }
          >
            <DataTable
              search={false}
              label="高频错误"
              data={d.topErrors}
              columns={[
                {
                  accessorKey: "error_code",
                  header: "错误码",
                  cell: ({ getValue }) => (
                    <a
                      className="mono"
                      href={href({ view: "errors", query: getValue() })}
                    >
                      {getValue()}
                    </a>
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
              ]}
            />
          </Panel>
          <div className="split-grid even">
            <Panel
              title="应用版本"
              note="同一安装可能在升级前后分别计入多个版本。"
            >
              <Breakdown rows={d.versions} name="app_version" />
            </Panel>
            <Panel title="运行平台" note="按错误报告与运行事件统计安装数。">
              <Breakdown rows={d.platforms} name="platform" />
            </Panel>
          </div>
        </>
      )}
    </QueryState>
  );
}
function Breakdown({ rows, name }: { rows: Row[]; name: string }) {
  return (
    <div className="version-bars">
      {rows.length ? (
        rows.map((r) => (
          <div key={r[name]}>
            <span className="mono">{r[name]}</span>
            <div className="bar-track">
              <span
                style={{
                  width: `${(r.installations / Math.max(...rows.map((x) => x.installations), 1)) * 100}%`,
                }}
              />
            </div>
            <strong>{fmt(r.installations)}</strong>
          </div>
        ))
      ) : (
        <Empty />
      )}
    </div>
  );
}
