import React, { useState, useEffect } from "react";
import { createRoot } from "react-dom/client";
import { createPortal } from "react-dom";
import { Theme, defineTheme } from "@astryxdesign/core/theme";
import { neutralTheme } from "@astryxdesign/theme-neutral";
import { Button } from "@astryxdesign/core/Button";
import { Slider } from "@astryxdesign/core/Slider";
import { Switch } from "@astryxdesign/core/Switch";
import { Selector } from "@astryxdesign/core/Selector";
import { Card } from "@astryxdesign/core/Card";
import { Badge } from "@astryxdesign/core/Badge";
import { SideNav, SideNavItem, SideNavSection } from "@astryxdesign/core/SideNav";
import { TextInput } from "@astryxdesign/core/TextInput";
import { NumberInput } from "@astryxdesign/core/NumberInput";
import { SegmentedControl, SegmentedControlItem } from "@astryxdesign/core/SegmentedControl";
import { Dialog, DialogHeader } from "@astryxdesign/core/Dialog";
import { InternationalizationProvider } from "@astryxdesign/core/i18n";
import zhCN from "@astryxdesign/core/locales/zh-CN.json";
import { ToolActivity } from "./tool-activity.jsx";
import { LogRecords } from "./log-records.jsx";
import { themeVariables, editableColors, readThemeColors } from "./theme-model.js";
import { normalizeColorText } from "../../core/theme-runtime.js";
import "./components.css";

const review = window === parent ? window.__SAKURA_VISUAL_REVIEW__ : parent.__SAKURA_VISUAL_REVIEW__;
const theme = defineTheme({ name: "sakura-review", extends: neutralTheme,
  typography: { body: { family: "Sakura Noto Sans SC", fallbacks: '"Microsoft YaHei UI", sans-serif' } },
  tokens: {
    "--color-accent": "var(--review-accent)", "--color-text-accent": "var(--review-accent-text)",
    "--color-icon-accent": "var(--review-accent-text)", "--color-on-accent": "var(--review-on-accent)",
    "--color-accent-muted": "color-mix(in srgb, var(--review-accent) 16%, transparent)",
    "--color-background-body": "var(--review-body)", "--color-background-card": "var(--review-card)",
    "--color-background-surface": "var(--review-card)", "--color-background-popover": "var(--review-card)",
    "--color-background-muted": "var(--review-input)", "--color-text-primary": "var(--review-text)",
    "--color-text-secondary": "var(--review-secondary)", "--color-border": "var(--review-border)",
  },
});
function syncTheme() {
  const o = review.options;
  const values = themeVariables(o.mode, o.accent, matchMedia("(prefers-color-scheme: dark)").matches);
  const root = document.documentElement;
  root.dataset.reviewVersion = o.version;
  root.dataset.reviewMode = values.scheme;
  for (const key of ["accent", "body", "sidebar", "card", "input", "border", "text", "secondary"]) root.style.setProperty(`--review-${key}`, values[key]);
  root.style.setProperty("--review-hover", o.hover);
  root.style.setProperty("--review-highlight", o.highlight);
  const rgb = values.accent.slice(1).match(/../g).map(v => parseInt(v, 16) / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
  const luminance = rgb[0] * .2126 + rgb[1] * .7152 + rgb[2] * .0722;
  root.style.setProperty("--review-on-accent", luminance > .179 ? "#101713" : "#ffffff");
  root.style.setProperty("--review-accent-text", `color-mix(in srgb, ${values.accent} 78%, ${values.scheme === "dark" ? "white" : "black"})`);
  root.style.colorScheme = o.version === "original" && window !== parent ? "light" : values.scheme;
}
function Segments({ label, value, options, onChange }) {
  return <SegmentedControl label={label} value={value} onChange={onChange} size="sm">{options.map(([id, text]) => <SegmentedControlItem key={id} value={id} label={text} />)}</SegmentedControl>;
}
function Modes() { return <Segments label="界面模式" value={review.options.mode} onChange={mode => review.update({mode})} options={[["light","浅色"],["dark","深色"],["system","跟随系统"]]} />; }
function ColorChoice() { return <div className="theme-colors-demo">{[["#4b9ac4","天空蓝"],["#cd6888","樱花粉"],["#629679","苔绿"],["#a482ca","鸢尾紫"]].map(([color,name]) => <button key={color} aria-label={name} aria-pressed={review.options.accent === color} style={{background:color}} onClick={() => review.update({accent:color})} />)}<input type="color" aria-label="自定义主题色" value={review.options.accent} onChange={event => review.update({accent:event.target.value})} /></div>; }
function ColorEditor({ field }) {
  const value = review.options[field.option];
  const [draft, setDraft] = useState(value);
  const [invalid, setInvalid] = useState(false);
  useEffect(() => { setDraft(value); setInvalid(false); }, [value]);
  return <div className="theme-row"><span>{field.label}</span><div className="theme-color-editor">
    <input type="color" aria-label={`${field.label}取色`} value={value} onChange={event => review.update({[field.option]:event.target.value})} />
    <TextInput label={`${field.label}色值`} isLabelHidden value={draft} width={142} size="sm" status={invalid ? {type:"error",message:"请输入六位十六进制色值"} : undefined} onChange={text => {
      setDraft(text); setInvalid(false);
      const color = normalizeColorText(text);
      if (color) review.update({[field.option]:color});
    }} onBlur={() => { const color = normalizeColorText(draft); setInvalid(!color); if (color) setDraft(color); }} />
  </div></div>;
}
function Appearance() {
  const [enabled, setEnabled] = useState(true);
  return <div className="theme-demo">
    <Card padding={6}><h2>界面主题</h2><div className="theme-row"><span>颜色模式</span><Modes /></div><div className="theme-row"><span>常用主题色</span><ColorChoice /></div>{editableColors.map(field => <ColorEditor key={field.option} field={field} />)}<p className="theme-description">背景与文字随颜色模式切换；旧主题中的其他色值保留。</p></Card>
    <Card padding={6}><h2>效果预览</h2><div className="theme-row"><span className="theme-highlight-preview">Sakura</span><Badge label="已启用" variant="success" /></div><div className="theme-example"><Button label="主要操作" variant="primary" /><Button label="次要操作" /><Switch label="开关示例" value={enabled} onChange={setEnabled} /></div></Card>
  </div>;
}
function ShellControls() { return <div className="review-theme-controls"><Segments label="演示页面" value={review.options.page} onChange={page => review.update({page})} options={[["settings","设置"],["history","聊天记录"],["logs","运行日志"]]} /><Modes /><input className="color-choice" type="color" aria-label="主题色" value={review.options.accent} onChange={event => review.update({accent:event.target.value})} /></div>; }

let historyView = "history";
function ToolPreview() {
  const [status, setStatus] = useState("complete");
  return <div className="tool-preview">
    <div className="tool-preview-head"><Badge label="工具调用交互示例" /><Segments label="调用状态" value={status} onChange={setStatus} options={[["pending","等待"],["running","执行中"],["complete","完成"],["error","失败"]]} /></div>
    <div className="history-entry history-entry-right"><div className="entry-column entry-column-human"><p className="entry-meta">你</p><p className="entry-bubble entry-bubble-human">帮我找一下今天的工作笔记，再整理成待办。</p></div></div>
    <div className="history-entry history-entry-left"><div className="entry-column entry-column-assistant"><p className="entry-meta">Sakura</p><p className="entry-bubble entry-bubble-assistant">我先查一下笔记。</p></div></div>
    <div className="tool-activity-block"><ToolActivity calls={[
      {id:"demo-search",pluginName:"笔记插件",name:"查找笔记",target:"今天",status:"complete",duration:"0.3 秒",arguments:{date:"today"},result:"找到 2 篇笔记：项目进度、会议记录。"},
      {id:"demo-todo",pluginName:"待办插件",name:"整理待办",target:"工作清单",status,arguments:{source:"项目进度、会议记录"},...(status === "complete" ? {duration:"0.8 秒",result:["核对界面交互", "整理测试反馈"]} : status === "error" ? {error:"待办插件暂时不可用，清单没有写入。"} : {})},
    ]} /></div>
    {status === "complete" && <div className="history-entry history-entry-left"><div className="entry-column entry-column-assistant"><p className="entry-bubble entry-bubble-assistant">整理好了，共两项。你可以展开上面的工具记录查看结果。</p></div></div>}
  </div>;
}

const rootNode = document.createElement("div");
rootNode.className = "astryx-root";
document.body.append(rootNode);
const root = createRoot(rootNode);
let dialog = null;
window.__SAKURA_DEMO_INFO__ = (title, message) => { dialog = {title, message}; render(); };
const mounts = [];
function addMount(source, content, className = "") {
  const mount = document.createElement("div");
  mount.className = "astryx-mount " + className;
  source.after(mount);
  source.setAttribute("data-review-original", "");
  mounts.push({source, mount, content});
}
function labelFor(input) { return input.labels?.[0]?.textContent.trim() || input.getAttribute("aria-label") || input.closest(".form-row, .setting-row")?.querySelector("label, .setting-title")?.textContent.trim() || input.id; }
function change(input, value, type = "change") {
  if (input.type === "checkbox") input.checked = value;
  else input.value = String(value);
  input.dispatchEvent(new Event(type, {bubbles:true}));
  render();
}
function installSettings() {
  const navigation = document.querySelector(".nav-card");
  addMount(navigation, () => <SideNav aria-label="设置分类" className="review-side-nav" header={<div className="review-nav-brand">Sakura<span /></div>} style={{width:"100%",height:"100%",background:"var(--review-sidebar)"}}>
    {Array.from(navigation.querySelectorAll(".nav-group"), group => <SideNavSection key={group.getAttribute("aria-labelledby")} title={group.querySelector(".nav-group-label").textContent}>
      {Array.from(group.querySelectorAll("button[data-page]")).filter(button => !button.hidden).map(button => <SideNavItem key={button.dataset.page} label={button.querySelector(".nav-item-label").textContent} icon={<span className={button.querySelector(".sakura-icon")?.className} aria-hidden="true" />} isSelected={button.classList.contains("is-active")} isDisabled={button.disabled} onClick={() => {button.click(); document.querySelector(".page-scroll").scrollTop = 0; render();}} />)}
    </SideNavSection>)}
  </SideNav>, "review-nav-mount");
  for (const input of document.querySelectorAll("input.layout-slider")) {
    addMount(input, () => <Slider label={labelFor(input)} isLabelHidden value={Number(input.value)} min={Number(input.min)} max={Number(input.max)} step={Number(input.step) || 1} isDisabled={input.disabled} valueDisplay="none" onChange={value => change(input,value,"input")} onChangeEnd={value => change(input,value)} />, "astryx-range");
  }
  for (const input of document.querySelectorAll(".setting-toggle > input[type=checkbox]")) addMount(input, () => <Switch label={labelFor(input)} isLabelHidden value={input.checked} isDisabled={input.disabled} onChange={value => change(input,value)} />);
  for (const input of document.querySelectorAll("#characterSelect, #visualSelect")) addMount(input.closest(".custom-select") || input, () => <Selector size="sm" label={labelFor(input)} isLabelHidden value={input.value} isDisabled={input.disabled} options={Array.from(input.options, option => ({value:option.value,label:option.textContent}))} onChange={value => change(input,value)} placement="below" width="100%" />, "astryx-select");
  for (const button of document.querySelectorAll("#page-character button.secondary-button, .detail-card > footer > button")) addMount(button, () => <Button label={button.textContent} variant={button.id === "saveButton" ? "primary" : "secondary"} isDisabled={button.disabled} onClick={() => button.click()} />);
  const oldTheme = document.querySelector("#page-appearance > fieldset");
  addMount(oldTheme, () => <Appearance />);
  installSettingsFields();
  const observer = new MutationObserver(records => {
    if (records.some(record => !record.target.closest?.(".astryx-mount, .astryx-root"))) queueMicrotask(() => {installSettingsFields(); render();});
  });
  observer.observe(document.querySelector(".settings-shell"), {subtree:true, childList:true, attributes:true, attributeFilter:["disabled", "hidden", "value", "aria-current"], characterData:true});
  document.addEventListener("input", event => {
    const field = editableColors.find(field => field.legacy === event.target.dataset.themeField);
    if (field && /^#[0-9a-f]{6}$/i.test(event.target.value) && review.options[field.option] !== event.target.value) review.update({[field.option]:event.target.value});
    render();
  });
  document.addEventListener("change", render);
  window.addEventListener("pagehide", () => observer.disconnect(), {once:true});
}
function installSettingsFields() {
  for (let index = mounts.length - 1; index >= 0; index--) {
    const {source,mount} = mounts[index];
    if (source && !source.isConnected) mount.remove();
    if (!mount.isConnected) mounts.splice(index,1);
  }
  for (const input of document.querySelectorAll(".settings-page select, .settings-page input")) {
    // The slider owns this focused, temporary editor and replaces it on blur.
    // Hiding it to mount another input would commit it immediately and orphan the island.
    if (input.matches(".slider-value-editor")) continue;
    // Some plugin forms enhance a select after inserting it into the document.
    // Move the island next to that final wrapper and hide the whole old control.
    if (input.tagName === "SELECT") {
      const wrapper = input.closest(".custom-select");
      const existing = mounts.find(item => item.source === input);
      if (wrapper && existing) {
        wrapper.setAttribute("data-review-original", "");
        wrapper.after(existing.mount);
        existing.source = wrapper;
      }
    }
    if (input.closest(".astryx-mount, [data-review-original]") || input.dataset.themeField || input.type === "hidden") continue;
    const fieldLabel = labelFor(input) || input.closest(".setting-row")?.querySelector(".setting-title")?.textContent || input.placeholder || "设置项";
    if (input.tagName === "SELECT") {
      addMount(input.closest(".custom-select") || input, () => <Selector size="sm" label={fieldLabel} isLabelHidden value={input.value} isDisabled={input.disabled} options={Array.from(input.options, option => ({value:option.value,label:option.textContent,disabled:option.disabled}))} onChange={value => change(input,value)} placement="below" width="100%" />, "astryx-select");
    } else if (["text", "password"].includes(input.type)) {
      addMount(input, () => <TextInput label={fieldLabel} isLabelHidden type={input.type} value={input.value} isDisabled={input.disabled} isReadOnly={input.readOnly} placeholder={input.placeholder} onChange={value => change(input,value,"input")} onBlur={() => input.dispatchEvent(new Event("change",{bubbles:true}))} width="100%" />, "astryx-text-input");
    } else if (input.type === "number") {
      addMount(input, () => <NumberInput label={fieldLabel} isLabelHidden value={input.value === "" ? null : Number(input.value)} hasClear min={input.min === "" ? undefined : Number(input.min)} max={input.max === "" ? undefined : Number(input.max)} step={Number(input.step) || 1} isDisabled={input.disabled} isReadOnly={input.readOnly} onChange={value => { change(input,value ?? "","input"); input.dispatchEvent(new Event("change",{bubbles:true})); }} width="100%" />, "astryx-number-input");
    } else if (input.type === "checkbox" && input.closest(".setting-toggle")) {
      addMount(input, () => <Switch label={fieldLabel} isLabelHidden value={input.checked} isDisabled={input.disabled} onChange={value => change(input,value)} />);
    }
  }
}
function installLogs() {
  const tabs = Array.from(document.querySelectorAll(".log-tab"));
  addMount(document.querySelector(".log-tabs"), () => <Segments label="日志分类" value={tabs.find(tab => tab.getAttribute("aria-selected") === "true")?.dataset.scope} options={tabs.map(tab => [tab.dataset.scope, tab.textContent.trim().replace(/\s+/g, " ")])} onChange={scope => { tabs.find(tab => tab.dataset.scope === scope).click(); render(); }} />);
  const problems = document.getElementById("problem-filter");
  addMount(problems.closest("label"), () => <div className="log-problem-switch"><Switch label="只看问题" value={problems.checked} onChange={value => change(problems,value)} /><Badge label={document.getElementById("count-problems").textContent} variant="warning" /></div>);
  const autoScroll = document.getElementById("auto-scroll");
  addMount(autoScroll.closest("label"), () => <Switch label="自动滚动" value={autoScroll.checked} onChange={value => change(autoScroll,value)} />);
  const plugin = document.getElementById("plugin-filter");
  addMount(plugin.closest(".custom-select"), () => <Selector size="sm" label="选择插件" isLabelHidden value={plugin.value} options={Array.from(plugin.options, option => ({value:option.value,label:option.textContent}))} onChange={value => change(plugin,value)} placement="below" width="100%" />);
  for (const button of document.querySelectorAll(".action-buttons > button")) addMount(button, () => <Button label={button.textContent} isDisabled={button.disabled} variant={button.id === "close" ? "primary" : "secondary"} onClick={() => button.click()} />);
  const list = document.getElementById("log-list");
  addMount(list, () => <LogRecords list={list} onChange={render} />);
  const observer = new MutationObserver(records => {
    if (records.some(record => !record.target.closest?.(".astryx-mount, .astryx-root"))) queueMicrotask(render);
  });
  observer.observe(document.querySelector(".log-shell"), {subtree:true, childList:true, attributes:true, attributeFilter:["disabled", "hidden", "aria-selected"], characterData:true});
  document.getElementById("log-scroll").addEventListener("scroll", render);
  window.addEventListener("pagehide", () => observer.disconnect(), {once:true});
}
function installHistory() {
  const head = document.querySelector(".history-head");
  const controls = document.createElement("div");
  controls.className = "astryx-mount history-review-controls";
  head.after(controls);
  mounts.push({mount:controls, content:() => <><Segments label="聊天内容" value={historyView} onChange={view => {historyView = view; document.documentElement.dataset.historyView = view; render();}} options={[["history","聊天记录"],["tools","工具调用预览"]]} /><span className="history-source">{historyView === "tools" ? "演示数据" : window.__SAKURA_HISTORY__?.source === "local" ? `本地记录 · ${window.__SAKURA_HISTORY__.assistantName}` : "演示记录"}</span></>});
  const preview = document.createElement("div");
  preview.className = "astryx-mount tool-preview-mount";
  document.getElementById("history-scroll").append(preview);
  mounts.push({mount:preview, content:() => <ToolPreview />});
  document.documentElement.dataset.historyView = historyView;
  for (const button of document.querySelectorAll(".history-actions > button")) addMount(button, () => <Button label={button.textContent} isDisabled={button.disabled} variant={button.id === "close" ? "primary" : "secondary"} onClick={() => button.click()} />);
  const count = document.getElementById("history-count");
  addMount(count, () => <Badge label={historyView === "tools" ? "UI 示例" : count.textContent} />);
  const observer = new MutationObserver(records => {
    if (records.some(record => !record.target.closest?.(".astryx-mount, .astryx-root"))) queueMicrotask(render);
  });
  observer.observe(document.querySelector(".history-shell"), {subtree:true, childList:true, attributes:true, attributeFilter:["disabled"], characterData:true});
  window.addEventListener("pagehide", () => observer.disconnect(), {once:true});
}
function render() {
  if (window !== parent && !document.body.dataset.demoPage) {
    const inputs = editableColors.map(field => document.querySelector(`[data-theme-field="${field.legacy}"]`));
    if (inputs.every(input => input && /^#[0-9a-f]{6}$/i.test(input.value))) {
      const colors = readThemeColors(Object.fromEntries(inputs.map(input => [input.dataset.themeField,input.value])));
      if (Object.entries(colors).some(([key,value]) => review.options[key] !== value)) review.update(colors);
    }
  }
  syncTheme();
  const mode = review.options.version === "original" && window !== parent ? "light" : review.options.mode;
  let children;
  if (window === parent) children = createPortal(<ShellControls />, document.getElementById("theme-controls"));
  else children = mounts.map(({source,mount,content}, index) => createPortal(<React.Fragment key={index}>{content()}</React.Fragment>, mount));
  root.render(<Theme theme={theme} mode={mode}><InternationalizationProvider locale="zh-CN" messages={{"zh-CN":zhCN}}>{children}
    <Dialog isOpen={Boolean(dialog)} onOpenChange={open => { if (!open) { dialog = null; render(); } }} width={460} padding={6}>
      <DialogHeader title={dialog?.title || ""} onOpenChange={() => { dialog = null; render(); }} />
      <p style={{lineHeight:1.8,marginTop:20,whiteSpace:"pre-wrap",overflowWrap:"anywhere",maxHeight:"55vh",overflowY:"auto"}}>{dialog?.message}</p>
      <div style={{display:"flex",justifyContent:"flex-end",marginTop:24}}><Button label="关闭" onClick={() => { dialog = null; render(); }} /></div>
    </Dialog>
  </InternationalizationProvider></Theme>);
}
if (window !== parent && !document.body.dataset.demoPage) installSettings();
if (document.body.dataset.demoPage === "logs") installLogs();
if (document.body.dataset.demoPage === "history") installHistory();
window.addEventListener("review-theme", render);
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
render();
