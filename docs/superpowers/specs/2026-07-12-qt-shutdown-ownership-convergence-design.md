# Qt 退出所有权收敛设计

## 1. 背景

Sakura 已用 `ResourceRegistry` / `ResourceManager` 统一关闭 QThread、Python thread、进程、async loop 和长期服务，但当前 Qt 退出边界仍保留第一阶段迁移时的兼容表面和三处所有权漏洞。

1. `ResourceManager.stop_qt_thread()` 已无生产调用，PetWindow 的 `_retain_qobject_wrappers_until_deleted()` 也无调用；它们只让旧阶段 API 看似仍受支持。
2. `QtWorkerResource.stop()` 超时后把 thread/worker 转交 lingering 列表，却保留宿主的 `worker_thread` / `worker` 属性。线程真正结束后 manager 会 `deleteLater()`，宿主则继续指向可能失效的 Shiboken wrapper。
3. lingering 完成路径直接 `deleteLater()`，没有像正常完成路径一样先保留 wrapper；运行中的 `QThread` 还保持 PetWindow parent，窗口销毁可以绕过 Python lingering 引用直接销毁 C++ QThread。
4. 启动 TTS 整合包迁移使用 `register=False`，设计上不能被 `stop_all()` 中断，但 PetWindow 仍可关闭。迁移 worker 没有 cancel 检查，关闭会让运行中的 QThread 跟随窗口销毁。

## 2. 目标与非目标

### 目标

- 删除无生产调用的 Qt 第一阶段兼容入口和 ResourceManager 测试 facade。
- QThread 超时转 lingering 时，宿主属性立即失效，manager 成为唯一所有者。
- lingering QThread 脱离窗口 QObject parent，避免窗口销毁强制删除运行中线程。
- 正常完成与 lingering 完成共用同一套 wrapper 保留和 `deleteLater()` 收尾。
- wrapper 仍有效时继续安排 prune，直到底层 C++ 对象失效后释放 Python 引用。
- 启动 TTS 迁移线程运行期间拒绝关闭 PetWindow；迁移完成后关闭行为不变。
- 保持有限等待、不强杀线程、shutdown 顺序和正常 UI 流程不变，生产删除量大于新增量。

### 非目标

- 不把不可取消的文件迁移强行改成可中断复制。
- 不调用 `QThread.terminate()`，不把有限等待改成无限等待。
- 不改变 Python thread、process、async loop 或插件资源的关闭语义。
- 不在 lingering 超时后执行 worker 的业务 `on_finished`；这些回调可能重启聊天、移动端队列或自动任务。
- 不重写 TTS Provider、ResourceRegistry shutdown order 或 pytest 全局 Qt 清理夹具。

## 3. 方案比较

### 方案 A：显式所有权转移（采用）

QThread wait 超时时，先把 owner 属性置空，再把 thread 从窗口 parent 脱离，由 ResourceManager 的 lingering 列表独占引用。线程完成后共用 wrapper retire helper。迁移期间阻止窗口关闭。

优点：保留“不强杀、不阻塞 UI”的安全原则；修复 stale wrapper 与 parent 销毁竞态；可删除旧兼容 API。缺点：极端 lingering 线程会在窗口关闭后继续自然结束，期间由 Python manager 引用保活。

### 方案 B：超时后强制 terminate

优点：退出快。缺点：QThread 可能正在 Python、Qt Multimedia、文件或网络库内部执行，强杀会放大 native 崩溃和数据损坏风险。

### 方案 C：所有线程无限等待

优点：对象销毁顺序直观。缺点：worker 若等待 UI queued signal 或外部 I/O，关闭窗口会永久卡死。

### 方案 D：把迁移加入 stop_all

优点：表面上统一。缺点：迁移 worker 没有取消点，`quit()` 不能中断正在执行的 Python 文件复制，仍会 timeout；若随后关闭窗口，原生竞态仍在。

## 4. 删除旧兼容面

删除：

- `ResourceManager.stop_qt_thread()`；
- 仅为旧入口存在的三项单元测试；
- `PetWindow._retain_qobject_wrappers_until_deleted()`；
- `ResourceManager._resources` 和 `_lingering_threads` 代理属性。

测试直接检查真实所有者 `manager.registry._resources` / `manager.registry._lingering_threads`。这些名称仍是内部测试观察点，但不再要求 QObject wrapper 重复暴露 pure-Python registry 状态。

`_stop_thread_mechanics()` 保留，它是 `QtWorkerResource.stop()` 的唯一机制实现。

## 5. lingering 所有权转移

`QtWorkerResource.stop()` 在 wait 超时时按以下顺序执行：

1. `_stop_thread_mechanics()` 已把原 thread/worker 放入 manager lingering；
2. `_null_owner_attrs()` 只在 owner 属性仍指向本资源时置空；
3. 标记资源 finalized 并从 registry 注销；
4. 清空资源自身 thread/worker 引用。

manager lingering 列表此后是唯一运行时所有者。owner 若已把属性换成新对象，现有身份比较继续保护新值。

`_keep_lingering()` 在 UI 线程调用 `thread.setParent(None)`。QThread 对象本身属于创建它的 UI 线程，因此可以安全脱离 PetWindow parent；worker 不改 parent 和 thread affinity。

若 `setParent(None)` 因 wrapper 已失效抛 `RuntimeError`，记录并继续保留引用，不把异常扩散到关闭链。

`thread.finished` 不再连接无 QObject 上下文的 lambda，而是连接 ResourceManager 的 bound slot；slot 通过 `sender()` 取得 QThread，再调用 release。这样 lingering 列表修改、wrapper retention timer 和 `deleteLater()` 调度回到 manager 所在的 UI 线程。若 receiver 已随应用退出销毁，Qt 会自动断开；已脱离 parent 的 thread 不会被窗口强制删除。

## 6. 统一 QObject retire

新增一个私有 helper 接收 thread/worker：

1. 调用 `_retain_wrappers()`；
2. 分别容错调用 `deleteLater()`。

正常 `_finalize()` 与 `_release_lingering()` 都使用该 helper，删除两套收尾顺序。

`retain_wrappers()` 改为内部 `_retain_wrappers()`，因为 PetWindow 兼容委托删除后只有 ResourceManager 自己调用。`_prune_wrappers()` 每次删除已经失效的 wrapper；若仍有有效 wrapper，重新安排一次 retention timer。这样“保留窗口”不会退化成“碰巧等到下次有别的 wrapper 才再次 prune”，也不会立即丢弃仍由 Qt 延迟删除管理的对象。

## 7. 启动迁移退出门禁

`PetWindow.closeEvent()` 在 TTS 下载检查之前读取 `tts_migration_thread`：

- 属性不存在、为 `None`、wrapper 已失效或 `isRunning()` 为 False：继续现有关闭链；
- 正在运行：显示“迁移完成后再退出”的信息，`event.ignore()`，不设置 `_shutdown_in_progress`，不调用 `stop_all()`。

迁移线程正常完成后，`spawn_qt_worker(register=False)` 的 finalize 会把 `tts_migration_thread` / `tts_migration_worker` 置空，因此门禁自然解除。迁移 Dialog 自己继续禁止关闭，不新增取消语义。

## 8. 错误处理与兼容边界

- clean QThread：仍 cancel → interruption → quit → wait → 正常 finalize → 业务 cleanup。
- timeout QThread：不强杀；owner 属性立即置空；不运行业务 cleanup；自然完成后 retire wrapper。
- owner 属性已被新 worker 复用：身份比较避免误清新对象。
- thread parent 脱离失败：记录错误，仍保留 lingering 引用。
- 迁移状态检查 RuntimeError：按“不在运行”处理，让已有关闭链继续，避免无效 wrapper 永久挡住退出。
- `ResourceRegistry.stop_all()` 的顺序、异常隔离和幂等性不变。

## 9. 测试策略

严格 TDD：

1. 结构测试先要求死 `stop_qt_thread`、PetWindow delegate 和 ResourceManager facade 消失。
2. timeout 测试先把旧期望“owner 仍指向 thread”改为 thread/worker 均为 None，并断言 thread parent 被移除；当前实现因此失败。
3. lingering release 测试断言 wrapper 先进入 retired 列表，再 deleteLater；当前路径没有 retain，因此失败。
4. wrapper prune 测试用 timer stub：第一次 wrapper 有效时必须重新安排，第二次失效后列表清空且不再安排。
5. PetWindow close 测试用正在运行的 migration thread，断言 event ignored、`close_external_tools()` 零调用和提示出现；当前实现会进入关闭链，因此失败。
6. 保留正常完成、重新赋值保护、真实 QThread finalize、shutdown order、late callback guard 和全量回归。

## 10. 提交组织与验收

计划提交：

1. `refactor: remove legacy qt resource facades`
2. `fix: transfer lingering qt worker ownership`
3. `fix: block shutdown during tts migration`

验收标准：

1. 无生产 `stop_qt_thread`、PetWindow wrapper delegate 或 ResourceManager registry facade。
2. timeout 后 owner 不再持有 thread/worker wrapper。
3. lingering thread 不再以 PetWindow 为 QObject parent。
4. 正常与 lingering 完成都先 retain wrapper 再 deleteLater。
5. 有效 wrapper 会继续 prune，失效后引用释放。
6. TTS 迁移运行期间窗口不会进入 shutdown。
7. 相关、分层和全量测试通过，无 QThread/QWaitCondition 退出警告。
8. `app/`、`plugins/` 删除量大于新增量，工作树只剩用户 bat。
