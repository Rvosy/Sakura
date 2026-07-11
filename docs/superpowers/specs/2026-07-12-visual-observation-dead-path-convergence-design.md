# 视觉观察死路径收敛设计

## 1. 背景

Sakura 当前只通过一条生产链生成视觉观察记录：包含图片的 Agent 最终回复在同一个 JSON 中返回 `visual_observation`，`ChatPipeline` 将该摘要转换为 `VisualObservationRecord` 并写入角色级短期视觉存储。后续用户追问截图或画面时，PetWindow 从存储中读取最近记录并临时注入本轮消息。

`app/storage/visual_observation.py` 仍保留早期独立视觉摘要链：`summarize_visual_observation()` 会再次调用视觉模型，并拥有自己的提示词、图片拼装、格式解析和失败记录兜底。生产代码没有任何调用点，只有三条单元测试直接调用它。这个死入口隐藏了一个虚假的失败语义：若未来误接入，模型请求失败也会生成并可能保存“视觉摘要生成失败”的低置信度记录，而当前真实链明确在摘要缺失时跳过保存。

同一存储还暴露 `VisualObservationStore.search()`，但生产与测试均无调用。当前产品行为只按“最近记录”选择视觉记忆，关键词仅决定读取三条还是十分钟内的一条。

## 2. 目标与非目标

### 目标

- 删除零生产调用的独立视觉摘要入口及其专用提示词、图片拼装、失败兜底和测试。
- 删除零调用的视觉观察搜索方法。
- 保留当前同回复生成摘要、脱敏落盘、最近记录注入和屏幕感知批次行为。
- 保持视觉观察 JSONL 文件结构、保留期限、敏感信息脱敏、模型选择和 UI 不变。
- 严格 TDD，并使 `app/`、`plugins/` 生产删除量大于新增量。

### 非目标

- 不删除视觉观察存储或截图追问能力。
- 不改变 `visual_observation` 回复协议。
- 不重写 Agent 最终回复修复、视觉模型路由或屏幕截图编码。
- 不改变视觉记录并发、保留策略或磁盘格式。

## 3. 方案比较

### 方案 A：切除死入口（采用）

保留当前 in-band `visual_observation` 链，只删除独立 summarizer 和无调用搜索 API。

优点：生产净删最大；不改变任何真实调用路径；消除未来误接旧失败兜底的风险。缺点：本轮不处理当前链路之外的其他视觉架构问题。

### 方案 B：合并并重写视觉观察模块

把回复解析、记录构造、存储与消息注入重新拆分到多个模块。

优点：边界可能更整齐。缺点：改动面覆盖 Agent、ChatPipeline、PetWindow 和存储，容易改变模型请求与 UI 行为，不符合本轮删除优先目标。

### 方案 C：删除全部短期视觉记忆

优点：删除量最大。缺点：用户将无法追问刚才截图里的文字或画面，属于明确的产品行为变化。

## 4. 保留的真实链路

1. PetWindow 为手动截图、自动观察或主动屏幕感知创建 `VisualObservationJob`。
2. Agent 最终视觉回复返回顶层 `visual_observation`。
3. `extract_visual_observation_summary()` 从同一回复提取字典。
4. `ChatPipeline._record_visual_observation_from_result()` 调用 `visual_observation_record_from_summary()`，脱敏后写入 `VisualObservationStore`。
5. `_add_visual_context_to_messages()` 调用 `store.recent()`，构造只作用于本轮请求的系统上下文。

这些接口、调用顺序和错误语义全部保持不变。摘要缺失或为空时继续跳过保存，不生成虚假失败记录。

## 5. 删除范围

从 `app/storage/visual_observation.py` 删除：

- `summarize_visual_observation()`；
- `_build_visual_summary_prompt()`；
- `_build_visual_summary_user_text()`；
- `_job_image_parts()`；
- `_normalize_image_detail()`；
- `_fallback_record()`；
- `VisualObservationStore.search()`；
- 仅由上述死代码需要的 import。

从 `tests/unit/test_visual_observation.py` 删除三条只测试独立 summarizer 的测试和对应 import。保留记录构造、脱敏、存储和视觉上下文消息测试。

## 6. 测试策略

严格 TDD：

1. 先增加结构测试，要求模块不再暴露 `summarize_visual_observation`，并要求 `VisualObservationStore` 不再暴露 `search`；当前代码因此 RED。
2. 删除死生产代码及其自证测试，运行结构测试和整个视觉观察单元文件转 GREEN。
3. 运行 ChatPipeline、ChatWorker、AgentRuntime 与 PetWindow 视觉相关回归，证明真实 in-band 链仍工作。
4. 运行编译、unit、integration、UI 与全量测试。

## 7. 提交与验收

计划提交：

1. `refactor: remove legacy visual summarizer`
2. 若 review 发现独立问题，以 RED 驱动最小修复并 amend；不创建无内容提交。

验收标准：

1. 生产中不存在独立视觉模型摘要入口或专用提示词。
2. `VisualObservationStore` 只保留真实调用的 append/recent 生命周期 API。
3. Agent 同回复摘要、ChatPipeline 落盘、脱敏和后续截图追问保持绿色。
4. 全量测试通过，无新增原生退出警告。
5. 工作树只剩用户的 `link_sakura_runtime_tts.bat`。
6. 本轮 `app/`、`plugins/` 删除量大于新增量。
