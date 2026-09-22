import { useQuery } from "@tanstack/react-query";
import type { Row } from "./types";
export class ApiError extends Error {
  constructor(public status: number) {
    super(
      status === 401
        ? "登录已失效，请重新登录后刷新。"
        : status === 404
          ? "没有找到记录，可能已超出保留期，或所选时间内没有数据。"
          : status === 503
            ? "数据暂时无法读取，请稍后重试。"
            : `请求失败（${status}），请重试。`,
    );
  }
}
export function useApi(path: string, enabled = true) {
  return useQuery<Row>({
    queryKey: [path],
    enabled,
    queryFn: async ({ signal }) => {
      const res = await fetch(`/admin/api/${path}`, {
        signal,
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!res.ok) throw new ApiError(res.status);
      return res.json();
    },
    retry: false,
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });
}
