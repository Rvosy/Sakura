import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowUpRight, PackageCheck, FileClock } from "lucide-react";
import { useApi } from "./api";
import { Panel, QueryState } from "./components";
import { href } from "./state";
import type { Row } from "./types";

const states: Record<string, string> = {
  ready: "待发布",
  publishing: "发布中",
  published: "已发布",
  failed: "发布失败",
  interrupted: "发布中断",
  discarded: "已丢弃",
};
const timestamp = (value?: string) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "—";
async function request(path: string, body?: Row) {
  const response = await fetch(
    `/admin/api/control/${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : { cache: "no-store" },
  );
  const result = await response.json();
  if (!response.ok)
    throw new Error(
      typeof result.detail === "string"
        ? result.detail
        : `请求失败（${response.status}）`,
    );
  return result;
}

export function ReleaseSummary() {
  const query = useApi("control/status");
  return (
    <QueryState query={query}>
      {(data) => (
        <div className="release-summary">
          <PackageCheck size={28} />
          <div>
            <span className="eyebrow">正式版本</span>
            <h2>
              {data.live?.updater?.version
                ? `v${data.live.updater.version}`
                : "尚未发布"}
            </h2>
          </div>
          <div className="release-summary-state">
            <strong>
              {data.live?.consistent ? "清单校验通过" : "清单需要检查"}
            </strong>
            <small>文件更新于 {timestamp(data.live?.fileUpdatedAt)}</small>
          </div>
          <a className="btn" href={href({ view: "releases" })}>
            管理发布 <ArrowUpRight size={16} />
          </a>
        </div>
      )}
    </QueryState>
  );
}

export function Releases() {
  const client = useQueryClient();
  const query = useApi("control/status");
  const [version, setVersion] = useState("");
  const [selected, setSelected] = useState<Row | null>(null);
  const [notes, setNotes] = useState("");
  const [minimum, setMinimum] = useState("");
  const [urgent, setUrgent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [confirm, setConfirm] = useState(false);
  function choose(item: Row) {
    setSelected(item);
    setNotes(item.payload.updater.notes);
    setMinimum(item.payload.release.minimumSupported || "");
    setUrgent(item.payload.release.urgent);
    setConfirm(false);
  }
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      await client.invalidateQueries({ queryKey: ["control/status"] });
    } catch (error) {
      setError(
        error instanceof Error ? error.message : "操作失败，请刷新后重试。",
      );
    } finally {
      setBusy(false);
    }
  }
  const dirty =
    selected &&
    (notes !== selected.payload.updater.notes ||
      minimum !== (selected.payload.release.minimumSupported || "") ||
      urgent !== selected.payload.release.urgent);
  const editable =
    selected && ["ready", "failed", "interrupted"].includes(selected.state);
  return (
    <>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {message && (
        <div className="alert alert-success" role="status">
          {message}
        </div>
      )}
      <QueryState query={query}>
        {(data) => (
          <>
            <div className="release-hero">
              <div>
                <span className="eyebrow">当前线上清单</span>
                <h2>
                  {data.live.updater?.version
                    ? `Sakura ${data.live.updater.version}`
                    : "尚未发布"}
                </h2>
                <p>
                  {data.live.consistent
                    ? "版本与下载字段校验通过"
                    : "清单缺失或不一致，请检查后修复发布"}
                </p>
                <a href={data.endpoint} target="_blank" rel="noreferrer">
                  查看公开清单 <ArrowUpRight size={14} />
                </a>
              </div>
              <dl>
                <dt>文件更新时间</dt>
                <dd>{timestamp(data.live.fileUpdatedAt)}</dd>
                <dt>安装包来源</dt>
                <dd>GitHub Releases</dd>
                <dt>该版本故障</dt>
                <dd>
                  <a
                    href={href({
                      view: "diagnostics",
                      version: data.live.updater?.version || "",
                    })}
                  >
                    查看诊断 →
                  </a>
                </dd>
              </dl>
            </div>
            <div className="release-workbench">
              <Panel title="版本草稿">
                <div className="release-panel-content">
                  <form
                    className="release-import"
                    onSubmit={(event) => {
                      event.preventDefault();
                      void run(async () => {
                        choose(
                          await request("drafts", {
                            version: version.trim().replace(/^v/, ""),
                          }),
                        );
                        setMessage("版本资料已保存为草稿。");
                      });
                    }}
                  >
                    <label htmlFor="release-version">GitHub 正式版本</label>
                    <div>
                      <input
                        id="release-version"
                        className="form-control"
                        placeholder="例如 1.1.0"
                        value={version}
                        onChange={(e) => setVersion(e.target.value)}
                        required
                        disabled={busy}
                      />
                      <button className="btn btn-primary" disabled={busy}>
                        导入
                      </button>
                    </div>
                  </form>
                  <div className="draft-list">
                    {data.drafts
                      .filter((d: Row) => d.state !== "discarded")
                      .map((item: Row) => (
                        <button
                          key={item.id}
                          className={`draft-item ${selected?.id === item.id ? "selected" : ""}`}
                          disabled={busy}
                          onClick={() =>
                            void run(async () => {
                              choose(await request(`drafts/${item.id}`));
                            })
                          }
                        >
                          <FileClock size={18} />
                          <span>
                            <strong>v{item.version}</strong>
                            <small>{timestamp(item.created_at)}</small>
                          </span>
                          <span className="draft-state">
                            {states[item.state] || item.state}
                          </span>
                        </button>
                      ))}
                    {!data.drafts.some((d: Row) => d.state !== "discarded") && (
                      <p className="release-empty">暂无草稿</p>
                    )}
                  </div>
                </div>
              </Panel>
              <Panel
                title={
                  selected
                    ? `v${selected.version} · ${states[selected.state]}`
                    : "发布预览"
                }
              >
                <div className="release-panel-content">
                  {!selected ? (
                    <div className="release-empty">
                      <PackageCheck size={38} />
                      <p>导入版本，核对说明与下载文件后发布。</p>
                    </div>
                  ) : (
                    <>
                      {selected.error && (
                        <p role="alert" className="text-danger">
                          {selected.error}
                        </p>
                      )}
                      <fieldset
                        className="release-editor"
                        disabled={busy || !editable}
                      >
                        <label htmlFor="release-notes">更新说明</label>
                        <textarea
                          id="release-notes"
                          className="form-control"
                          rows={7}
                          maxLength={16000}
                          value={notes}
                          onChange={(e) => {
                            setNotes(e.target.value);
                            setConfirm(false);
                          }}
                        />
                        <label htmlFor="release-minimum">
                          最低支持版本（可留空）
                        </label>
                        <input
                          id="release-minimum"
                          className="form-control"
                          value={minimum}
                          onChange={(e) => {
                            setMinimum(e.target.value);
                            setConfirm(false);
                          }}
                          placeholder="例如 1.0.0"
                        />
                        <label className="release-checkbox">
                          <input
                            type="checkbox"
                            checked={urgent}
                            onChange={(e) => {
                              setUrgent(e.target.checked);
                              setConfirm(false);
                            }}
                          />
                          标记为紧急更新
                        </label>
                      </fieldset>
                      <div className="release-downloads">
                        <h3>下载文件</h3>
                        {Object.entries(selected.payload.release.downloads).map(
                          ([key, url]) => (
                            <a
                              key={key}
                              href={String(url)}
                              target="_blank"
                              rel="noreferrer"
                            >
                              {String(url).split("/").pop()}{" "}
                              <ArrowUpRight size={14} />
                            </a>
                          ),
                        )}
                        <small>
                          更新器沿用发布包签名；紧急标记与最低版本是否影响提示，取决于客户端支持。
                        </small>
                      </div>
                      {editable && (
                        <div className="release-buttons">
                          <button
                            className="btn"
                            disabled={busy || !dirty}
                            onClick={() =>
                              void run(async () => {
                                choose(
                                  await request(`drafts/${selected.id}/save`, {
                                    notes,
                                    urgent,
                                    minimumSupported: minimum || null,
                                    expectedPayload: selected.payload,
                                  }),
                                );
                                setMessage("草稿已保存。");
                              })
                            }
                          >
                            保存草稿
                          </button>
                          <button
                            className="btn btn-primary"
                            disabled={busy || !!dirty || confirm}
                            onClick={() => setConfirm(true)}
                          >
                            发布版本
                          </button>
                          <button
                            className="btn"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                await request(
                                  `drafts/${selected.id}/discard`,
                                  {},
                                );
                                setSelected(null);
                                setConfirm(false);
                                setMessage("草稿已丢弃。");
                              })
                            }
                          >
                            丢弃草稿
                          </button>
                        </div>
                      )}
                      {dirty && <p className="muted">保存修改后才能发布。</p>}
                      {confirm && (
                        <div
                          className="publish-confirm"
                          role="region"
                          aria-label="发布确认"
                        >
                          <strong>
                            将公开更新清单切换至 v{selected.version}
                          </strong>
                          <p>
                            缓存最多约 5
                            分钟后更新；已安装的客户端仍按各自配置检查更新。
                          </p>
                          <button
                            className="btn btn-primary"
                            disabled={busy}
                            onClick={() =>
                              void run(async () => {
                                choose(
                                  await request(
                                    `drafts/${selected.id}/publish`,
                                    {
                                      expectedRevision:
                                        data.live.revision ?? null,
                                      expectedPayload: selected.payload,
                                    },
                                  ),
                                );
                                setMessage("清单已发布，下载仍使用 GitHub。");
                              })
                            }
                          >
                            {busy ? "正在发布…" : "确认发布"}
                          </button>
                          <button
                            className="btn"
                            disabled={busy}
                            onClick={() => setConfirm(false)}
                          >
                            取消
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </div>
              </Panel>
            </div>
            <Panel title="发布操作记录">
              <div className="table-responsive">
                <table className="table">
                  <thead>
                    <tr>
                      <th>时间</th>
                      <th>版本</th>
                      <th>来源</th>
                      <th>操作</th>
                      <th>结果</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.operations.map((op: Row) => (
                      <tr key={op.id}>
                        <td>{timestamp(op.occurred_at)}</td>
                        <td>{op.version}</td>
                        <td>
                          {op.source === "ci" ? "GitHub Actions" : "控制台"}
                        </td>
                        <td>
                          {(
                            {
                              import: "导入",
                              publish: "发布",
                              discard: "丢弃",
                            } as Row
                          )[op.action] || op.action}
                        </td>
                        <td>
                          {states[op.state] || op.state}
                          {op.detail && (
                            <small className="d-block text-danger">
                              {op.detail}
                            </small>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!data.operations.length && (
                  <p className="release-empty">暂无操作记录</p>
                )}
              </div>
            </Panel>
          </>
        )}
      </QueryState>
    </>
  );
}
