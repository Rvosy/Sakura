export type Row = Record<string, any>;
export type View =
  "diagnostics" | "overview" | "errors" | "models" | "installation" | "report";
export interface Route {
  view: View;
  days: number;
  query: string;
  severity: string;
  version: string;
  platform: string;
  report?: string;
  installation?: string;
  operation?: string;
  tab: string;
  build?: string;
  component?: string;
  reason?: string;
  run?: string;
  generation?: string;
  group?: string;
  cursor?: string;
  start?: string;
  end?: string;
  includeTest?: boolean;
}
export const views: View[] = [
  "diagnostics",
  "overview",
  "errors",
  "models",
  "installation",
  "report",
];
