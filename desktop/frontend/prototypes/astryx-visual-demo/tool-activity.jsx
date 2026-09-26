import React from "react";
import { ChatToolCalls } from "@astryxdesign/core/Chat";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";

const text = value => typeof value === "string" ? value : JSON.stringify(value, null, 2);

/**
 * A presentation-only adapter for future plugin call events.
 * Calls provide id, pluginName, name, status (pending/running/complete/error),
 * target, duration, arguments, result and error. It never executes a tool.
 */
export function ToolActivity({ calls }) {
  return <ChatToolCalls defaultIsExpanded calls={calls.map(call => ({
    key: call.id,
    name: call.name,
    node: call.pluginName,
    status: call.status,
    target: call.target,
    duration: call.duration,
    errorMessage: call.error,
    resultDetail: <div className="tool-call-details">
      {call.arguments !== undefined && <CodeBlock title="参数" code={text(call.arguments)} language="json" hasLanguageLabel={false} hasCopyButton={false} isWrapped width="100%" size="sm" />}
      {call.result !== undefined && <CodeBlock title="结果" code={text(call.result)} hasLanguageLabel={false} hasCopyButton={false} isWrapped width="100%" size="sm" />}
      {call.error && <p className="tool-call-error">{call.error}</p>}
    </div>,
  }))} />;
}
