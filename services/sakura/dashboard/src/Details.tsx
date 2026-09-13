import { ExportButton } from "./Diagnostics";
import { useState, useRef, type KeyboardEvent } from "react";
import { tabbable } from "tabbable";
import { Offcanvas, Tabs, Tab } from "react-bootstrap";
import { ArrowUpRight, Search, X } from "lucide-react";
import { useApi } from "./api";
import { href, navigate } from "./state";
import {
  Id,
  Panel,
  QueryState,
  Severity,
  Metrics,
  DataTable,
  Empty,
  fmt,
} from "./components";
import { reportColumns } from "./Errors";
import type { Route, Row } from "./types";
export function Lookup({ kind }: { kind: "report" | "installation" }) {
  const [id, setId] = useState("");
  const [error, setError] = useState("");
  const label = kind === "report" ? "Report ID" : "Installation ID";
  return (
    <form
      className="lookup"
      onSubmit={(e) => {
        e.preventDefault();
        if (!/^[\da-f]{8}(-[\da-f]{4}){3}-[\da-f]{12}$/i.test(id.trim())) {
          setError("请输入完整的 UUID。");
          return;
        }
        setError("");
        navigate(
          kind === "report"
            ? { report: id.trim().toLowerCase() }
            : { installation: id.trim().toLowerCase() },
        );
      }}
    >
      <label htmlFor={`lookup-${kind}`}>{label}</label>
      <div>
        <input
          className="form-control mono"
          id={`lookup-${kind}`}
          placeholder={`粘贴完整 ${label}`}
          autoComplete="off"
          spellCheck={false}
          value={id}
          onChange={(e) => setId(e.target.value)}
        />
        <button className="btn btn-primary">
          <Search size={15} />
          查询
        </button>
      </div>
      {error && (
        <p className="text-danger" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
export function Installation({ route }: { route: Route }) {
  const q = useApi(
    `installations/${encodeURIComponent(route.installation || "")}?limit=200`,
    !!route.installation,
  );
  return (
    <>
      <Panel
        title="查询安装实例"
        note="Installation ID 是匿名安装标识。可从错误报告或模型调用列表进入。"
      >
        <Lookup kind="installation" />
      </Panel>
      {route.installation ? (
        <QueryState query={q}>
          {(d) => (
            <>
              <div className="installation-header">
                <Id value={d.installationId} />
                <a
                  className="btn"
                  href={href({
                    view: "models",
                    installation: d.installationId,
                    operation: undefined,
                  })}
                >
                  查看模型调用
                  <ArrowUpRight size={15} />
                </a>
              </div>
              <Metrics
                items={[
                  {
                    label: "错误报告",
                    value: fmt(d.retainedCounts.errors),
                    note: "整个保留期",
                  },
                  {
                    label: "运行事件",
                    value: fmt(d.retainedCounts.events),
                    note: "整个保留期",
                  },
                  {
                    label: "模型调用",
                    value: fmt(d.retainedCounts.model_calls),
                    note: "整个保留期",
                  },
                ]}
              />
              <Panel
                title="最近错误"
                note="整个保留期内最近 200 条，不受顶部时间范围影响。"
              >
                <DataTable
                  data={d.recentErrors}
                  columns={reportColumns}
                  label="安装实例错误"
                />
              </Panel>
            </>
          )}
        </QueryState>
      ) : (
        <Empty text="输入 Installation ID，查看这个安装实例的记录。" />
      )}
    </>
  );
}
export function ReportDrawer({ route }: { route: Route }) {
  const lastReport = useRef(route.report);
  if (route.report) lastReport.current = route.report;
  const shownReport = route.report || lastReport.current;
  const q = useApi(
    `reports/${encodeURIComponent(shownReport || "")}`,
    !!route.report,
  );
  return (
    <Offcanvas
      show={!!route.report}
      onHide={() => navigate({ report: undefined })}
      placement="end"
      className="report-drawer"
      backdropClassName="report-backdrop"
      aria-labelledby="report-title"
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) => {
        if (event.key !== "Tab") return;
        const elements = tabbable(event.currentTarget);
        const first = elements[0],
          last = elements[elements.length - 1];
        if (!first) return;
        // Bootstrap restores stray focus; explicitly wrap Tab at both ends.
        if (
          event.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === event.currentTarget)
        ) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }}
    >
      <Offcanvas.Header>
        <div>
          <span className="section-kicker">错误报告</span>
          <Offcanvas.Title id="report-title">报告详情</Offcanvas.Title>
        </div>
        <button
          className="icon-btn"
          aria-label="关闭报告"
          onClick={() => navigate({ report: undefined })}
        >
          <X size={20} />
        </button>
      </Offcanvas.Header>
      <Offcanvas.Body>
        {shownReport && (
          <QueryState query={q}>
            {(d) => <ReportContent key={shownReport} d={d} />}
          </QueryState>
        )}
      </Offcanvas.Body>
    </Offcanvas>
  );
}
function ReportContent({ d }: { d: Row }) {
  const [copied, setCopied] = useState("");
  const evidence = d.evidence || {};
  return (
    <>
      <div className="report-summary">
        <Severity value={d.error.severity} />
        <h2>{d.error.code}</h2>
        <p>
          {d.error.component} · {d.error.event}
        </p>
        <time>{d.receivedAt} · 北京时间</time>
        <Id value={d.reportId} compact={false} />
      </div>
      <section aria-label="原始错误" className="diagnostic-evidence">
        <h3>原始错误</h3>
        {evidence.diagnostic ? (
          <pre>{evidence.diagnostic}</pre>
        ) : (
          <p>这份记录未采集原始错误。</p>
        )}
        {[
          ["调用栈", evidence.exception_stack],
          ["异常链", evidence.exception_chain],
          ["恢复错误", evidence.recovery_diagnostic],
          ["子进程 stderr", evidence.stderr],
        ].map(([label, text]) =>
          text ? (
            <div key={label}>
              <h4>{label}</h4>
              <pre>{text}</pre>
            </div>
          ) : null,
        )}
        {Object.keys(evidence).length > 0 && (
          <details>
            <summary>现场字段</summary>
            <pre>{JSON.stringify(evidence, null, 2)}</pre>
          </details>
        )}
        <div className="report-copy-actions">
          <button
            className="btn"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(
                  JSON.stringify(d.rawReport || d, null, 2),
                );
                setCopied("已复制");
              } catch {
                setCopied("复制失败，请下载报告");
              }
            }}
          >
            复制完整报告
          </button>
          {copied && <span role="status">{copied}</span>}
        </div>
      </section>
      <ExportButton report={d.reportId} filters={{ report: d.reportId }} />
      <div className="report-links">
        <a
          className="btn"
          href={href({
            view: "installation",
            installation: d.installationId,
            report: undefined,
          })}
        >
          查看安装实例
          <ArrowUpRight size={14} />
        </a>
        <a
          className="btn"
          href={href({
            view: "diagnostics",
            tab: "timeline",
            run: d.runId,
            generation: d.generation || undefined,
            installation: d.installationId,
            operation: d.operationId || undefined,
            report: undefined,
          })}
        >
          关联模型调用
          <ArrowUpRight size={14} />
        </a>
      </div>
      <Tabs
        transition={false}
        defaultActiveKey="context"
        className="workspace-tabs"
      >
        <Tab eventKey="context" title="环境与定位">
          <dl className="detail-list">
            {[
              [
                "发生位置",
                d.details?.file
                  ? `${d.details.file}:${d.details.line ?? "?"}`
                  : d.error.location,
              ],
              ["原因", d.details?.reasonCode],
              ["阶段", d.details?.stage],
              ["Generation", d.generation],
              ["原始失败", d.details?.primaryCode],
              ["恢复失败", d.details?.recoveryCode],
              ["恢复结果", d.details?.recoveryOutcome],
              ["异常类型", d.error.exceptionType],
              ["指纹", d.error.fingerprint],
              ["版本", d.app.version],
              [
                "构建 / 渠道",
                [d.app.build, d.app.channel].filter(Boolean).join(" / "),
              ],
              [
                "系统",
                [d.system.platform, d.system.osVersion, d.system.arch]
                  .filter(Boolean)
                  .join(" / "),
              ],
              ["WebView", d.system.webviewVersion],
              ["安装方式", d.context.installKind],
              ["升级前版本", d.context.upgradedFrom],
            ].map(([label, v]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  {typeof v === "boolean" ? (v ? "是" : "否") : (v ?? "未知")}
                </dd>
              </div>
            ))}
            {[
              ["Installation ID", d.installationId],
              ["Operation ID", d.operationId],
              ["Run ID", d.runId],
            ].map(([label, v]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>
                  <Id value={v} />
                </dd>
              </div>
            ))}
          </dl>
        </Tab>
        <Tab eventKey="stack" title={`调用栈 (${d.stack.length})`}>
          <SafeRows rows={d.stack} kind="stack" />
        </Tab>
        <Tab
          eventKey="breadcrumbs"
          title={`事件轨迹 (${d.breadcrumbs.length})`}
        >
          <SafeRows rows={d.breadcrumbs} kind="breadcrumbs" />
        </Tab>
      </Tabs>
    </>
  );
}
function SafeRows({ rows, kind }: { rows: Row[]; kind: string }) {
  return rows.length ? (
    <ol className="safe-rows">
      {rows.map((r, i) => (
        <li key={i}>
          <span className="trace-index">{i + 1}</span>
          <div>
            <strong>
              {String(
                r.function ||
                  r.event ||
                  r.category ||
                  (kind === "stack" ? "栈帧" : "事件"),
              )}
            </strong>
            <dl>
              {Object.entries(r).map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>
                    {typeof v === "object"
                      ? JSON.stringify(v)
                      : String(v ?? "—")}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        </li>
      ))}
    </ol>
  ) : (
    <Empty text="这份报告没有保存相关记录。" />
  );
}
