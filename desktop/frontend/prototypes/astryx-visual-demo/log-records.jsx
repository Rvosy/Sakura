import React from "react";
import { Card } from "@astryxdesign/core/Card";
import { Badge } from "@astryxdesign/core/Badge";
import { MetadataList, MetadataListItem } from "@astryxdesign/core/MetadataList";
import { CodeBlock } from "@astryxdesign/core/CodeBlock";
import { viewerFailureText, viewerPluginName } from "../../runtime-log/runtime-log-presentation.js";

export function LogRecords({ list, onChange }) {
  return <div className="astryx-log-list">{Array.from(list.children, source => {
    const item = source.viewerItem;
    if (!item) return null;
    const { record } = item;
    const failure = viewerFailureText(record);
    const disclosure = source.querySelector("details");
    const selected = source.getAttribute("aria-selected") === "true";
    const details = [
      {label:"事件代码", value:record.eventCode},
      ...record.details.filter(detail => !["原始报错", "诊断", "异常链", "调用栈"].includes(detail.label)),
      ...(record.pluginId ? [{label:"插件标识",value:record.pluginId}] : []),
      ...(record.correlationId ? [{label:"关联编号",value:record.correlationId}] : []),
    ];
    const traces = record.details.filter(detail => ["异常链", "调用栈"].includes(detail.label));
    const select = () => { source.click(); onChange(); };
    const detailId = `log-detail-${source.dataset.itemKey}`;
    const expanded = Boolean(disclosure?.open);
    return <Card key={source.dataset.itemKey} padding={5} className="astryx-log-card" data-selected={selected} tabIndex={0} role="group" aria-label={`${record.timestamp} ${record.message}`} onClick={select} onKeyDown={event => { if (event.target === event.currentTarget && ["Enter", " "].includes(event.key)) { event.preventDefault(); select(); } }}>
      <button type="button" className="log-record-toggle" aria-expanded={expanded} aria-controls={detailId} onClick={() => {
        if (disclosure) { disclosure.open = !expanded; disclosure.dispatchEvent(new Event("toggle")); }
        onChange();
      }}><span className="log-record-heading">
        <time>{record.timestamp}</time>
        <Badge label={record.category === "PLUGIN" ? viewerPluginName(record) : record.category} />
        {record.severity !== "info" && <Badge variant={record.severity === "error" ? "error" : "warning"} label={record.severity === "error" ? "错误" : "提醒"} />}
        <strong>{record.message}</strong>
        {item.repeatCount > 1 && <Badge label={`×${item.repeatCount}`} />}
      </span><svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true"><path d={expanded ? "M4 10l4-4 4 4" : "M4 6l4 4 4-4"} fill="none" stroke="currentColor" strokeWidth="1.5" /></svg></button>
      {failure && <CodeBlock code={failure} hasLanguageLabel={false} hasCopyButton={false} isWrapped width="100%" size="sm" />}
      {!failure && record.description && <p className="log-record-description">{record.description}</p>}
        <div id={detailId} className="log-record-details" hidden={!expanded}>
          <MetadataList columns="multi" label={{position:"top"}}>{details.map((detail,index) => <MetadataListItem key={index} label={detail.label}>{detail.value}</MetadataListItem>)}</MetadataList>
          {traces.map(detail => <CodeBlock key={detail.label} title={detail.label} code={detail.value} hasLanguageLabel={false} hasCopyButton={false} isWrapped width="100%" size="sm" />)}
        </div>
    </Card>;
  })}</div>;
}
