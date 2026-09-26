export const historyEntries = [
  { entryId: "demo-1", turnId: "turn-1", kind: "human", origin: "user", createdAt: "2026-09-27T10:12:00+08:00", payload: { text: "今天想把桌面整理一下，顺便换个清爽一点的主题。" } },
  { entryId: "demo-2", turnId: "turn-1", kind: "assistant", origin: "assistant", createdAt: "2026-09-27T10:12:02+08:00", payload: { segments: [{ text: "好呀。先把暂时用不到的窗口收起来吧。\n主题可以保留你喜欢的天空蓝，背景就用干净的纯白色。" }] } },
  { entryId: "demo-3", turnId: "turn-2", kind: "human", origin: "user", createdAt: "2026-09-27T10:13:00+08:00", payload: { text: "那晚上呢？我不想整片背景都跟着变成蓝色。" } },
  { entryId: "demo-4", turnId: "turn-2", kind: "assistant", origin: "assistant", createdAt: "2026-09-27T10:13:04+08:00", payload: { segments: [{ text: "晚上切到深色就好。底色和正文保持中性，选中的按钮、开关和焦点才用主题色。" }] } },
  { entryId: "demo-5", turnId: "turn-3", kind: "observation", origin: "scheduled_screen", createdAt: "2026-09-27T10:14:00+08:00", payload: { text: "桌面上打开了设置窗口，没有其他待处理通知。" } },
  { entryId: "demo-6", turnId: "turn-3", kind: "assistant", origin: "assistant", createdAt: "2026-09-27T10:14:03+08:00", payload: { segments: [{ text: "整理好了。你可以在上面切换浅色、深色，再试试另一种主题色。" }] } },
];

export async function loadHistory() {
  const response = await fetch("/__sakura_review/history", {cache:"no-store"});
  if (response.status === 404) return {source:"demo", assistantName:"N.A.V.I.", characterId:"navi", entries:historyEntries};
  if (!response.ok) throw new Error("本地聊天记录读取失败");
  return response.json();
}
