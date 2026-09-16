import { useSyncExternalStore } from "react";
import { type Route, views, type View } from "./types";
const subscribe = (cb: () => void) => {
  window.addEventListener("hashchange", cb);
  return () => window.removeEventListener("hashchange", cb);
};
export function parseRoute(hash: string): Route {
  const [raw, search = ""] = hash.replace(/^#/, "").split("?");
  const p = new URLSearchParams(search);
  return {
    ...Object.fromEntries(
      [
        "build",
        "component",
        "reason",
        "run",
        "generation",
        "group",
        "start",
        "end",
      ].map((k) => [k, p.get(k) || undefined]),
    ),
    includeTest: p.get("includeTest") === "true",
    view: views.includes(raw as View) ? (raw as View) : "overview",
    days: [1, 7, 30, 90].includes(Number(p.get("days")))
      ? Number(p.get("days"))
      : 30,
    query: p.get("q") || "",
    severity: p.get("severity") || "",
    version: p.get("version") || "",
    platform: p.get("platform") || "",
    report: p.get("report_id") || undefined,
    installation: p.get("installation_id") || undefined,
    operation: p.get("operation_id") || undefined,
    tab: p.get("tab") || "groups",
  };
}
export function useRoute() {
  return parseRoute(
    useSyncExternalStore(subscribe, () => window.location.hash),
  );
}
export function href(patch: Partial<Route>, base = parseRoute(location.hash)) {
  const r = { ...base, ...patch };
  const p = new URLSearchParams({ days: String(r.days) });
  const fields = {
    ...Object.fromEntries(
      [
        "build",
        "component",
        "reason",
        "run",
        "generation",
        "group",
        "start",
        "end",
      ].map((k) => [k, r[k as keyof Route]]),
    ),
    includeTest: r.includeTest ? "true" : "",
    q: r.query,
    severity: r.severity,
    version: r.version,
    platform: r.platform,
    report_id: r.report,
    installation_id: r.installation,
    operation_id: r.operation,
    tab: r.tab === "groups" ? "" : r.tab,
  };
  Object.entries(fields).forEach(([k, v]) => {
    if (v) p.set(k, String(v));
  });
  return `#${r.view}?${p}`;
}
export const navigate = (patch: Partial<Route>, replace = false) => {
  const next = href(patch);
  if (replace) {
    history.replaceState(null, "", next);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  } else location.hash = next;
};
