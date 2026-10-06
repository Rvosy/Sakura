import { useMemo, useState } from "react";
import { useApi } from "./api";
import {
  Panel,
  QueryState,
  DataTable,
  textCol,
  numCol,
  pct,
} from "./components";
import { href } from "./state";
import type { Route, Row } from "./types";

const stages: Record<string, string> = {
  engine_start: "引擎启动",
  preload: "模型预加载",
  synthesis: "语音合成",
};
const counts = [
  numCol("installations", "安装实例"),
  numCol("attempts", "尝试"),
  numCol("succeeded", "成功"),
  numCol("failed", "失败"),
  numCol("cancelled", "取消"),
  {
    accessorKey: "failureRate",
    header: "失败率",
    cell: ({ row }: { row: { original: Row } }) =>
      pct(row.original.failureRate),
  },
  numCol("averageMs", "平均耗时（ms）"),
];

export function Tts({ route }: { route: Route }) {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const start = useMemo(
    () => new Date(Date.now() - route.days * 86400000).toISOString(),
    [route.days],
  );
  const params = new URLSearchParams({ start, ...filters });
  if (route.version) params.set("version", route.version);
  if (route.platform) params.set("platform", route.platform);
  const query = useApi(`v2/tts?${params}`);
  return (
    <>
      <Panel
        title="SakuraTTS 运行结果"
        note="失败率按成功与失败次数计算，不含取消。仅统计收到的结果；未上报或未结束的尝试不计入。"
      >
        <form
          className="diagnostic-filters"
          onSubmit={(event) => {
            event.preventDefault();
            setFilters(
              Object.fromEntries(
                [...new FormData(event.currentTarget)]
                  .map(([key, value]) => [key, String(value)])
                  .filter(([, value]) => value),
              ),
            );
          }}
        >
          <label>
            整合包版本
            <input className="form-control" name="bundle" />
          </label>
          <label>
            运行后端
            <input
              className="form-control"
              name="backend"
              placeholder="cuda / cpu / mlx"
            />
          </label>
          <label>
            检测到的显卡
            <input className="form-control" name="gpu" />
          </label>
          <label className="diagnostic-test-toggle">
            <input type="checkbox" name="includeTest" value="true" />{" "}
            包含开发与验收样本
          </label>
          <button className="btn btn-primary" type="submit">
            筛选
          </button>
        </form>
        <a href={href({ view: "diagnostics", query: "sakura.tts.sakuratts" })}>
          查看相关错误
        </a>
      </Panel>
      <QueryState query={query}>
        {(data) => (
          <>
            <Panel title="按阶段统计">
              <DataTable
                label="TTS 阶段统计"
                data={data.stages.map((row: Row) => ({
                  ...row,
                  stage: stages[row.stage] || row.stage,
                }))}
                columns={[textCol("stage", "阶段"), ...counts]}
              />
            </Panel>
            <Panel
              title="环境与配置"
              note="显卡信息为检测到的首张 NVIDIA 显卡，不代表实际选用设备；缺失字段不作推断。"
            >
              <DataTable
                label="TTS 环境统计"
                data={data.items.map((row: Row) => ({
                  ...row,
                  stage: stages[row.stage] || row.stage,
                }))}
                columns={[
                  textCol("stage", "阶段"),
                  textCol("app_version", "Sakura 版本"),
                  textCol("platform", "系统"),
                  textCol("arch", "架构"),
                  textCol("bundleVersion", "整合包"),
                  textCol("bundleSourceCommit", "引擎提交"),
                  textCol("requestedBackend", "请求后端"),
                  textCol("backend", "实际后端"),
                  textCol("profile", "推理模式"),
                  textCol("runtimeMode", "休眠模式"),
                  textCol("gpuName", "显卡"),
                  numCol("gpuMemoryMib", "显存（MiB）"),
                  textCol("gpuDriver", "驱动"),
                  ...counts,
                ]}
              />
            </Panel>
          </>
        )}
      </QueryState>
    </>
  );
}
