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
const performanceContext = [
  textCol("app_version", "Sakura 版本"),
  textCol("bundleVersion", "整合包"),
  textCol("backend", "实际后端"),
  textCol("profile", "推理模式"),
  textCol("gpuName", "检测到的显卡"),
];
const rtfCol = (key: string, header: string) => ({
  accessorKey: key,
  header,
  cell: ({ row }: { row: { original: Row } }) =>
    row.original[key] == null ? "—" : Number(row.original[key]).toFixed(3),
});

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
              title="合成性能"
              note="仅统计完整成功且有音频时长的样本。请求耗时包含引擎唤醒与音频返回；RTF 为请求秒数除以音频秒数，越低越快。P95 表示 95% 的样本不超过该值。"
            >
              <DataTable
                label="TTS 合成性能"
                data={data.items.filter(
                  (row: Row) => row.stage === "synthesis",
                )}
                columns={[
                  ...performanceContext,
                  numCol("performanceSamples", "性能样本"),
                  rtfCol("p50Rtf", "RTF P50"),
                  rtfCol("p95Rtf", "RTF P95"),
                  numCol("averageAudioSeconds", "平均音频（s）"),
                  numCol("p50RequestMs", "请求 P50（ms）"),
                  numCol("p95RequestMs", "请求 P95（ms）"),
                  numCol("p95EndToEndMs", "含排队与启动 P95（ms）"),
                ]}
              />
            </Panel>
            <Panel
              title="合成阶段耗时"
              note="单位为毫秒，按已测得的完整成功样本计算平均值。推理包含参考准备、文本处理、GPT 与 SoVITS；缺失测量显示为 —。"
            >
              <DataTable
                label="TTS 合成阶段耗时"
                data={data.items.filter(
                  (row: Row) => row.stage === "synthesis",
                )}
                columns={[
                  ...performanceContext,
                  numCol("inferenceSamples", "推理样本"),
                  numCol("averageGptMs", "GPT"),
                  numCol("averageSovitsMs", "SoVITS"),
                  numCol("averageQueueMs", "排队"),
                  numCol("averageEngineTotalMs", "引擎总耗时"),
                  numCol("averageInferenceMs", "推理"),
                  numCol("averageReferenceMs", "参考准备"),
                  numCol("averageFrontendMs", "文本处理"),
                  rtfCol("p50InferenceRtf", "推理 RTF P50"),
                ]}
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
