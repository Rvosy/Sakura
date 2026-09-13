import { ThemeSelect } from "./ThemeSelect";
import React from "react";
import { createRoot } from "react-dom/client";
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
  useIsFetching,
} from "@tanstack/react-query";
import {
  ChartNoAxesCombined,
  PackageCheck,
  Layers,
  Monitor,
  FileSearch,
  RefreshCw,
  Clock3,
  Menu,
  X,
} from "lucide-react";
import "@tabler/core/dist/css/tabler.min.css";
import "./theme.css";
import "./styles.css";
import iconUrl from "./assets/sakura-icon.png";
import { Overview } from "./Overview";
import { Diagnostics } from "./Diagnostics";
import { Releases, ReleaseSummary } from "./Releases";
import { Models } from "./Models";
import { Installation, ReportDrawer, Lookup } from "./Details";
import { useRoute, href, navigate } from "./state";
import { Panel } from "./components";
import { useMediaQuery } from "./useMediaQuery";
import type { View } from "./types";
const nav = [
  { id: "overview", label: "运行总览", icon: ChartNoAxesCombined },
  { id: "releases", label: "版本发布", icon: PackageCheck },
  { id: "diagnostics", label: "诊断与分析包", icon: FileSearch },
  { id: "models", label: "模型与 Context", icon: Layers },
  { id: "installation", label: "安装实例", icon: Monitor },
  { id: "report", label: "报告查询", icon: FileSearch },
];
function App() {
  const route = useRoute();
  const client = useQueryClient();
  const fetching = useIsFetching();
  const [menu, setMenu] = React.useState(false);
  const mobile = useMediaQuery("(max-width: 720px)");
  const active = nav.find(
    (n) => n.id === (route.view === "errors" ? "diagnostics" : route.view),
  )!;
  React.useEffect(() => {
    document.title = `${active.label} · Sakura 控制台`;
    setMenu(false);
  }, [active.label]);
  return (
    <div className="workspace">
      <a
        className="skip-link"
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        跳到内容
      </a>
      <aside
        inert={mobile && !menu}
        className={`sidebar ${menu ? "is-open" : ""}`}
      >
        <a
          className="brand"
          href={href({ view: "overview", report: undefined })}
        >
          <img
            className="brand-icon"
            src={iconUrl}
            alt=""
            width={40}
            height={40}
          />
          <span className="brand-text">
            Sakura<small>控制台</small>
          </span>
        </a>
        <span className="nav-label">管理</span>
        <nav aria-label="主导航">
          {nav.map((n) => (
            <a
              key={n.id}
              aria-current={route.view === n.id ? "page" : undefined}
              href={href({
                view: n.id as View,
                report: undefined,
                query: "",
                severity: "",
                version: "",
                platform: "",
                installation: undefined,
                operation: undefined,
                tab: "groups",
              })}
            >
              <n.icon size={18} />
              {n.label}
            </a>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span>Sakura Console</span>
          <p>私人管理空间</p>
        </div>
      </aside>
      {mobile && menu && (
        <button
          className="mobile-scrim"
          aria-label="收起导航"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace-main">
        <header className="topbar">
          <div>
            <button
              className="icon-btn mobile-menu"
              aria-label={menu ? "关闭导航" : "打开导航"}
              onClick={() => setMenu(!menu)}
            >
              {menu ? <X size={20} /> : <Menu size={20} />}
            </button>
            <span className="muted">Sakura</span>
            <Chevron />
            <span>{active.label}</span>
          </div>
          <div className="topbar-right">
            <Clock3 size={14} />
            <span>北京时间 UTC+8</span>
          </div>
        </header>
        <main id="main" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <h1>{active.label}</h1>
            </div>
            <div className="page-actions">
              {!["diagnostics", "errors", "releases"].includes(route.view) && (
                <ThemeSelect
                  id="range"
                  label="时间范围"
                  value={route.days}
                  onChange={(days) => navigate({ days: Number(days) })}
                  options={[1, 7, 30, 90].map((n) => ({
                    value: n,
                    label: `最近 ${n} 天`,
                  }))}
                />
              )}
              <button
                className="btn refresh-btn"
                onClick={() =>
                  client.invalidateQueries({ refetchType: "active" })
                }
                disabled={!!fetching}
              >
                <RefreshCw size={16} className={fetching ? "spinning" : ""} />
                <span>{fetching ? "读取中" : "刷新"}</span>
              </button>
            </div>
          </div>
          <div className="page-content" key={route.view}>
            {route.view === "overview" && (
              <>
                <ReleaseSummary />
                <Overview route={route} />
              </>
            )}
            {route.view === "releases" && <Releases />}{" "}
            {(route.view === "errors" || route.view === "diagnostics") && (
              <Diagnostics route={route} />
            )}{" "}
            {route.view === "models" && <Models route={route} />}{" "}
            {route.view === "installation" && <Installation route={route} />}{" "}
            {route.view === "report" && (
              <Panel
                title="查询错误报告"
                note="输入完整 Report ID。查询覆盖整个保留期，不受顶部时间范围影响。"
              >
                <Lookup kind="report" />
              </Panel>
            )}
          </div>
          <footer className="page-footer">
            Sakura Console<span>时间均为北京时间 · 汇总排除验收样本</span>
          </footer>
        </main>
      </div>
      <ReportDrawer route={route} />
    </div>
  );
}
function Chevron() {
  return <span className="breadcrumb-divider">/</span>;
}
const client = new QueryClient();
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>,
);
