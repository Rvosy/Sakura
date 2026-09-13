---
kind: adr
status: accepted
audience: maintainer
source_of_truth: self
updated: 2026-09-13
---

# ADR-0051：Release Action 导入草稿，维护者确认国内发布

## 决策

本决策 supersedes [ADR-0050](0050-private-console-and-service-domains.md) 与
[ADR-0049](0049-domestic-updater-manifest.md) 中 CI 直接发布国内公开清单的部分。
GitHub 正式资产全部上传后，Action 通过原受限 SSH 通道提交完整版本资料与 updater 清单，只创建私人草稿。
维护者登录控制台确认发布，国内更新入口才切换。GitHub 自身的 Release 和旧客户端更新入口继续原有行为。

CI 使用固定、无参数的导入程序。精确 sudo 规则允许该程序以控制台身份保存草稿，CI 身份没有公开目录写权限，
不能读取控制台数据库或运行一般命令。控制台使用自己目录下的发布锁；CI 导入只需要 SQLite 事务。

同版本重跑直接比较最初提交内容并返回原草稿，不覆盖编辑、重建已丢弃记录或重新发布。相同版本内容变化时失败，
由维护者在后台重新导入核对。导入映射与草稿同事务提交，不引入任务队列、轮询或内容摘要。

## 后果

GitHub 构建完成和国内正式发布成为两个明确步骤。Action Summary 提供后台入口，操作记录标明 GitHub Actions。
服务器无需在 CI 导入时再次访问 GitHub。旧工作流提交不完整资料会明确失败；修改必须进入实际运行 Release 的分支才能生效。

合同与运维步骤见 [Sakura Service](../specs/runtime-v2/sakura-service.md) 和
[运维文档](../devdocs/SAKURA_SERVICE.md)。
