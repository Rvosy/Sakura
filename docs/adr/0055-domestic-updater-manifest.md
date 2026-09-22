---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-16
---

# ADR-0055：国内服务器提供更新清单，GitHub 继续分发安装包

## 背景

国内用户可能无法连接 GitHub，即使只读取很小的版本清单也会检查失败。已有杭州服务器可以直接提供静态 JSON，
无需等安装包迁移到云存储后再改善检测。其 3 Mbps 带宽不适合承担主程序下载。

## 决策

本决策 supersedes [ADR-0041](0041-static-sakura-service-control-plane.md) 中“更新清单只由 GitHub 提供”及
相应客户端入口条款；原 ADR 的静态服务、受限 SSH 身份、本地功能独立和安装包托管决定继续适用。

- 新客户端从 `https://sakura.cialloo.cn/service/v1/latest.json` 检查更新，下载仍指向 GitHub 固定版本资产。
- CI 把最终清单与版本资料通过现有受限 SSH 通道提交。服务器先验证全部字段、平台、版本与 URL，再原子写入
  各文件。复用现有发布流程，不增加 GitHub 轮询任务或请求时代理。
- 保留 Tauri 安装包验签和用户明确安装操作；只更换清单入口，不建立第二套更新器或签名信任根。
- 旧 GitHub 清单继续发布。国内端点必须先部署、初始化并验证，随后才发行切换入口的新客户端。

## 取舍与后果

这一步改善更新检测，不改善 GitHub 安装包下载。服务器故障时检查会失败，但不影响聊天等本地功能。
两份 JSON 独立消费；先写清单、后写版本资料，第二次写失败允许同版本补发，不宣称跨文件事务。

选择 CI 主动推送而非 Nginx 实时代理，避免客户端每次检查仍依赖 GitHub 连通性。没有采用服务器定时抓取，
因为现有发行流程已有发布通道，再引入轮询会增加一份运行维护责任。

本决策对应的代码可在部署前完成；文档不作为线上已切换的证据。旧客户端需要升级才能切换内置入口，完全无法
访问 GitHub 的用户需要从可访问入口手动取得过渡版本。

合同见 [Sakura Service](../specs/runtime-v2/sakura-service.md)，部署见
[运维与发布](../devdocs/SAKURA_SERVICE.md)。
