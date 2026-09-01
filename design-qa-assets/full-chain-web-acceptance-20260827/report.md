# 纯 Web 端完整链路真实验收报告

> 2026-08-28 修订说明：第 1～10 节保留 2026-08-27 第一轮验收的原始事实和停止边界。完成运行时与视觉控制策略修复后，已在同一 Chrome、同一项目上执行第二轮纯 Web 验收；续验结果见第 11～17 节。第二轮已消除 405、素材“跳过”失效、Scene Look 强制门禁、参考清单丢失和多参考镜头错误要求首帧等问题，但在费用确认交互中意外创建了一个本地持久队列任务。该任务只显示“等待提交”，页面没有 Provider task ID、视频候选仍为 0，无法确认是否已经或将会产生费用；由于 Web 没有取消入口，本轮立即停止，未启动 Worker、未重复提交、未进入时间线或导出。

## 1. 验收结论

- 项目：`一人一猫-视频前Web真实验收-20260825-r3`
- Project ID：`a374e3fe-9b1f-4368-b491-0d93f87e3ee3`
- 执行窗口：2026-08-27 15:58:26Z 至 16:11:32Z（香港时间 2026-08-27 23:58:26 至 2026-08-28 00:11:32）
- 执行方式：用户当前 Chrome 中的真实“猫咪视频生产台”Web UI
- 最终结果：**BLOCKED（未完成完整链路）**
- 停止位置：分镜区 → “确认分镜制作方案”
- 直接停止原因：Web 确认对话框提交后返回 `ApiError: Method Not Allowed`；页面状态仍为 `awaiting_review`，生产区仍受阻。
- 付费调用：**0 次**
- Provider task ID：无
- 本地媒体生成任务：无
- 重复 Provider 请求：未发生
- 代码修改：无
- 后端/数据库/CLI 状态变更：无

本次严格执行“Web 页面缺少可用路径或交互报错时停止，不从后端绕过”的约束。因此没有继续编译生产 Prompt、批准生产包、调用 Seedream、调用 Seedance、加入时间线、合成或导出。

## 2. 执行前状态与冷启动

### 2.1 操作

- 在 Chrome 中普通刷新项目页面。
- 初始 URL：`http://127.0.0.1:5173/canvas/a374e3fe-9b1f-4368-b491-0d93f87e3ee3?zone=story&node=127ae7cc-93ff-41b0-901a-68ed34f90467`
- 等待页面恢复四个导演区与画布节点。

### 2.2 实际结果

- 页面最终恢复成功，没有永久停留在空白画布。
- 四区状态恢复为：
  - 剧情与脚本：已完成；
  - 人物、猫咪与视觉圣经：已完成；
  - 分镜与镜头设计：待审核；
  - 视频生成与成片：受阻。
- 页面显示“任务 0”“本地待同步”。
- 冷启动期间产生未处理异常，但页面随后仍显示了画布。

### 2.3 Console 记录

时间：`2026-08-27T15:58:32.272Z`

```text
[Vue warn]: Unhandled error during execution of mounted hook
at <AigcCanvasView ...>

ApiError: Internal Server Error
at request (.../src/api/client.ts:19:11)
at async Promise.all (index 0)
at async .../src/views/AigcCanvasView.vue:63:43
```

分类：`error`。页面具备部分恢复能力，但“冷启动控制台零未处理异常”的验收门槛未通过。

### 2.4 证据缺口

`01-cold-start.png` 未成功持久化。冷启动状态已通过 URL、页面状态和带时间戳的 Console 原文记录；遇到后续硬阻塞后未用另一轮操作伪造执行前截图。

## 3. 进入分镜区与场景参考审核

### 3.1 节点与资产

- 导演区：`shots`
- Scene 节点 ID：`c0e976e5-ee97-4a5c-87ef-f101300f0f26`
- 场景参考资产节点/资产 ID：`7bc7c45a-ad65-498b-a336-93bd03360252`
- 资产名称：`雨后小院角落空场景`
- 资产业务键：`scene:c0e976e5-ee97-4a5c-87ef-f101300f0f26:reference:environment:1`

### 3.2 Web 操作

1. 通过底部四区定位器进入“分镜与镜头设计”。
2. 在画布中选中场景/图片节点。
3. 通过节点编辑表面的“查看图片”打开真实素材。
4. 在 Web 资产工作台点击“批准”。
5. 在 Web 资产工作台点击“加入场景参考包”。

### 3.3 素材检查

素材满足基本制作要求：

- 竖屏构图；
- 雨后小院环境；
- 水泥台阶、湿润地面/积水、草与灌丛可见；
- 雨后柔和自然光；
- 无错误人物、猫咪、文字、水印、黑边或明显破图；
- 留有放置儿童和猫咪的空间。

非阻塞 warning：图片偏写实摄影质感，与分镜文本中的“细腻柔和数字插画材质”存在风格偏差，但仍可作为空间/环境参考。

### 3.4 审核实际结果

- 候选状态变为：`V1 · approved`。
- 图片节点显示：`IMAGE approved`。
- 环境参考显示：`雨后小院角落空场景 · 已就绪`。
- 分镜 Beat 的“视觉参考图”状态变为“执行完成”。
- 分镜区待处理数从 4 降为 2。
- 此过程没有创建新的图片生成调用。

截图：[02-scene-review.png](./02-scene-review.png)

## 4. 场景资产硬门禁核对

### 4.1 页面实际要求

环境参考批准并绑定后，资产工作台仍显示：

```text
2/6 槽位
本场景尚不能合成片段 Prompt
只有已批准并绑定到当前场景的素材才计入就绪状态。
```

仍被列为缺失的四个局部素材：

1. 带未干水痕的水泥矮阶局部；
2. 深浅湿痕的湿润泥土地局部；
3. 挂细小水珠的零星细草；
4. 缀满饱满雨珠的卵形阔叶。

同时显示：`尚未选择已批准的场景视觉基准（Scene Look）`。

### 4.2 “跳过”路径核对

为了避免把可用性验收扩大为四次额外付费道具生成，依次在真实 Web 下拉框中把上述四个局部素材设为“跳过”。这是页面公开提供的操作，不涉及 DOM/Vue 状态修改。

实际结果：

- 四个条目均显示“跳过”；
- 就绪数仍为 `2/6`；
- 四个条目仍出现在 blocker 原文中；
- “本场景尚不能合成片段 Prompt”未解除；
- Scene Look 仍为必需缺失项。

分类：`blocker / over-gating`。页面允许选择“跳过”，但执行门禁不承认该选择，造成 UI 语义和门禁语义不一致。四个局部小物原本可以由镜头长文本和已批准环境参考描述，不应被强制拆成四张图片才能继续。

## 5. 分镜制作方案确认

### 5.1 分镜内容核对

- URL：`http://127.0.0.1:5173/canvas/a374e3fe-9b1f-4368-b491-0d93f87e3ee3?zone=shots&node=4c7f8296-19ea-52b7-ad18-c8ca77204f72`
- 分镜节点 ID：`4c7f8296-19ea-52b7-ad18-c8ca77204f72`
- 当前剧情：`雨后叶珠`
- Story Revision ID：`127ae7cc-93ff-41b0-901a-68ed34f90467`
- Storyboard Revision：`1`
- 页面显示结构哈希短值：`31eeb514f075`
- 镜头数：1
- 镜头标题：`小院角落守叶等晴`
- 时长：15 秒
- Beat 状态：`approved`
- Storyboard 状态：`awaiting_review`
- Generation Plan：Revision 1、1 个真实片段（由确认对话框说明）

完整镜头描述存在，包含儿童、猫咪、环境、空间关系、雨后光线、声音和温暖收尾；没有必要为了通过门禁填写无意义字段，因此未修改分镜正文。

### 5.2 节点编辑交互问题

- 单击/双击媒体或 Scene 节点没有稳定打开节点编辑表面；
- 对选中节点按 `Enter` 可以打开锚定编辑器；
- 与既定“单击可编辑节点立即打开并聚焦主输入”的交互契约不一致。

分类：`warning / usability`。

### 5.3 确认层内容

点击 Web 页面“确认分镜制作方案”后出现真实确认层，原文：

```text
将一次锁定 Storyboard Revision 1 的 1 个导演分镜，以及
GenerationPlan Revision 1 的 1 个真实片段。
此确认不调用图片或视频模型。
```

确认层提供“继续检查”和“确认制作方案”。确认内容与当前 1 镜、15 秒、Revision 1 一致，因此点击“确认制作方案”。

该确认层未展示计划要求中的 Canon 版本、参考资产清单、Provider、模型、完整输入哈希或费用状态。由于它明确声明本步不调用媒体模型，这被记录为 `warning / incomplete review context`，不是本次点击前的付费 blocker。

### 5.4 实际错误与状态

时间：`2026-08-27T16:11:32.824Z`

```text
[Vue warn]: Unhandled error during execution of component event handler
at <StoryboardNodeConsole ...>
at <CanvasLocalConsole title="分镜编译" preset="storyboard" ...>

ApiError: Method Not Allowed
at request (.../src/api/client.ts:19:11)
at async confirmStoryboardProductionPlan
(.../src/components/canvas/AigcCanvasWorkspace.vue:2099:9)
```

- HTTP 状态语义：`405 Method Not Allowed`。
- 请求路径与实际 HTTP 方法：当前页面日志未暴露；为遵守禁止 CLI/直接 API 调查的约束，没有从后端、源码或手工 `fetch()` 补查。
- 对话框关闭，但分镜仍显示 `awaiting_review`。
- 分镜区仍为“待审核 · 2 项需处理”。
- 生产区仍为“受阻”。
- 页面没有提供可执行的 Web 修复入口。
- 没有重试提交，避免重复请求。

分类：`blocker / Web API integration error`。

截图：[03-storyboard-review.png](./03-storyboard-review.png)

## 6. 停止后的项目状态

| 项目环节 | 最终状态 | 说明 |
|---|---|---|
| 剧情与脚本 | 已完成 | 保持原状态 |
| 人物、猫咪与视觉圣经 | 已完成 | 保持原状态 |
| 场景环境参考 | 已批准、已绑定 | 完全通过 Web 完成 |
| 四个局部道具槽位 | 跳过但仍被判缺失 | UI 选择与门禁不一致 |
| Scene Look | 缺失 | 页面仍列为必需 blocker |
| 分镜 Beat | approved | 1 镜，15 秒 |
| 分镜制作方案 | awaiting_review | Web 确认请求 405，未生效 |
| 生产 Prompt | 未执行 | 按停止条件未继续 |
| 制作包批准 | 未执行 | 分镜确认未成功 |
| 视觉锚点 | 0 个候选 | 未调用 Seedream |
| 视频候选 | 0 个 | 未调用 Seedance |
| 时间线 | 未进入 | 无已批准视频版本 |
| 合成/导出 | 未执行 | 无可用时间线输入 |

## 7. 错误与 warning 汇总

| ID | 分类 | 严重度 | 现象 | 影响 |
|---|---|---|---|---|
| WEB-001 | 冷启动/API | error | mounted hook 中出现 `Internal Server Error` | 控制台验收不通过；页面随后恢复 |
| WEB-002 | 交互 | warning | 单击/双击节点未稳定打开编辑器，`Enter` 才能打开 | 与节点就地编辑契约不一致 |
| WEB-003 | 素材质量 | warning | 场景图偏写实，与数字插画目标有偏差 | 可作为环境参考，不阻塞 |
| WEB-004 | 资产门禁 | blocker | 四个条目选择“跳过”后仍被计为缺失，2/6 不变 | Prompt 无法进入可编译状态 |
| WEB-005 | Scene Look 门禁 | blocker | 已批准环境参考后仍强制要求额外 Scene Look | 扩大素材/付费前置条件 |
| WEB-006 | 审核上下文 | warning | 分镜确认层只显示 Revision 和片段数 | 缺少 Canon、资产、哈希等核对信息 |
| WEB-007 | Web 接口集成 | blocker | “确认制作方案”返回 `405 Method Not Allowed` | 分镜保持 awaiting_review，完整链路停止 |

## 8. 未执行步骤及原因

以下步骤没有执行，原因不是人为省略，而是满足计划规定的立即停止条件：

- 编译生产 Prompt；
- 批准生产分镜包；
- 视觉锚点费用确认与 Seedream 调用；
- 视觉锚点审核；
- 视频费用确认与 Seedance 调用；
- 视频播放、审核和版本选择；
- 加入时间线；
- 本地合成；
- 导出；
- 完成态冷刷新恢复验证。

因此没有创建 `04-prompt-package.png` 至 `10-export-refresh.png`。生成这些文件会伪装为已到达实际未到达的业务状态，本报告保留真实中止边界。

## 9. 合规核对

- 所有项目状态改变均来自真实 Web UI：通过。
- 未使用 Python、PowerShell、curl 或其他 CLI 调用后端接口：通过。
- 未直接调用 `/api/v1`、`/api/v2` 的写接口：通过。
- 未调用 repository、service、Provider gateway、SQL：通过。
- 未手工构造任务、审核、资产或 Provider 结果：通过。
- 未通过 DevTools 执行 `fetch()`、修改 DOM、修改 Vue 状态或模拟响应：通过。
- 未修改前端或后端代码：通过。
- 仅使用本地文件操作保存本报告和浏览器截图：通过。
- 报告未保存 API Key、Authorization、Cookie、浏览器存储或 Provider 签名 URL：通过。

## 10. 继续验收前必须恢复的 Web 条件

1. “确认分镜制作方案”的 Web 请求必须使用后端允许的方法/路由，并在成功后使 `awaiting_review` 状态刷新为已确认。
2. 对“跳过”的局部素材，门禁必须明确采用其中一种一致语义：真正从 required slots 移除，或不提供不可生效的“跳过”选项。
3. 明确 Scene Look 是否为硬必需；若已批准环境参考、人物/猫咪 Canon 和完整镜头 direction 已足够生成，则应降级为可选或 warning。
4. 修复冷启动 mounted hook 的 500 与未处理异常。
5. 修复完成后从“分镜制作方案确认”重新开始纯 Web 验收；不得通过数据库或接口手工补成已确认状态。

## 11. 第二轮：运行时版本恢复

### 11.1 执行方式

- 仅重启开发 API 进程，使正在运行的后端加载当前源码；没有通过 CLI 修改任何项目业务数据。
- 在 Chrome 中对目标项目执行普通冷刷新，并继续只通过真实 Web UI 改变项目状态。
- 页面恢复后，重新执行“确认分镜制作方案”。

### 11.2 实际结果

- 冷刷新成功恢复四区、节点、连线、URL 指定区域和节点，没有静默空白画布。
- 原 `405 Method Not Allowed` 消失，“确认分镜制作方案”通过 Web 成功完成。
- 这确认第一轮 405 的直接原因是前端已热更新、Python API 仍运行旧路由版本，而不是项目数据或数据库 Revision 错误。
- 新的运行时兼容检查会在 API 缺少所需 feature 时，在业务 POST 之前显示“前后端版本不一致，请重启 API”，避免用户点击后才得到 405。

截图：[01-cold-start.png](./01-cold-start.png)

## 12. 第二轮：视觉规划人工修订与 Scene Look

### 12.1 已接受规划的人工修订

首次打开已接受的视觉规划时，页面暴露了两个真实交互问题：

1. “保存修订”在没有进入明确草稿态时可见但不可完成有效保存；
2. 后台刷新会覆盖正在编辑的人工选择。

修复后，通过 Web 执行：

1. 点击“人工修订”；
2. 将四个非必需局部素材设为 `skip`；
3. 保存为 `source=manual` 的新版本 V2；
4. 冷刷新页面。

实际结果：

- V2 成为当前采用版本；
- 四个 `skip` 选择刷新后仍存在；
- required slot 从原来的 6 个收敛为 2 个，就绪状态显示 `2/2`；
- 保存过程中没有调用规划模型、图片模型或媒体 Provider；
- 原 V1 保留，没有覆盖历史版本。

截图：

- [04-manual-revision-save-disabled.png](./04-manual-revision-save-disabled.png)
- [05-manual-revision-ready.png](./05-manual-revision-ready.png)
- [06-manual-revision-persisted.png](./06-manual-revision-persisted.png)

### 12.2 Scene Look

- 在节点锚定编辑器中将 `Scene Look` 明确保存为“关闭”。
- 关闭时页面显示“未启用”，不再显示“缺失”。
- Prompt 编译、制作包确认和多参考视频准备不再因 Scene Look 缺失而阻塞。
- 已批准的普通环境参考仍作为 `scene` 参考进入多参考输入；关闭 Scene Look 不等于移除环境参考。

## 13. 第二轮：视频视觉控制策略

### 13.1 页面配置

在真实节点锚定编辑器中保存：

```text
providerInputMode = reference_media
sceneLookUsage = off
```

页面结果：

- 新增视频默认显示“多参考图直接生成”；
- 首帧锚点不再是该镜头的必经门禁；
- Scene Look 为关闭；
- 视频卡片在制作包批准后显示“可生成”，不再要求先批准开场锚点；
- 高级系统图入口在导演模式中可见，可检查 Prompt、Recipe 和执行节点，但不是默认主入口。

截图：[07-visual-control-multiref.png](./07-visual-control-multiref.png)

### 13.2 冻结参考清单错误与修复

Prompt 编译后的首次页面检查发现，制作包冻结了人物、猫咪、风格、比例和场景等参考，但执行预览只显示 2 项实际图片输入，丢失了猫咪以及部分人物参考。该行为会造成“审核时看到一套输入、Provider 实际收到另一套输入”的严重一致性问题。

修复后，执行层直接恢复已批准制作包中的有序 Provider manifest，不再重新解析并缩减为另一套 shot reference。冷刷新后视频节点显示：

```text
reference_media · 9 项实际图片输入
```

有序清单为：

1. 人物大头照；
2. 人物全身；
3. 猫咪正面；
4. 猫咪侧面；
5. 线条与材质；
6. child character design；
7. cat character design；
8. pair scale；
9. 雨后小院角落空场景。

输入哈希页面短值：`1341b8db7906b0d3…`。

Provider 最大参考数检查发生在费用确认前；超过 9 项时页面保留完整清单并产生 blocker，不会静默裁掉人物或猫咪参考。

截图：[08-multireference-preview.png](./08-multireference-preview.png)

### 13.3 多参考镜头进度错误与修复

制作包批准后，Recipe 一度仍停留在首帧锚点阶段，导致视频操作报“当前阶段不能执行 recipe:video”。原因是进度投影仍按总镜头数计算必需锚点，没有区分 `reference_media` 镜头与真正需要 `first_frame`／`first_last_frame` 的镜头。

修复后：

- Recipe 投影单独计算 `requiredAnchorCount`；
- `reference_media` 镜头的必需锚点数为 0；
- 分镜制作包批准后直接进入视频阶段；
- 页面卡片显示 9 项真实参考、Scene Look 关闭、锚点非必需和“可生成”。

截图：

- [09-prompt-package.png](./09-prompt-package.png)
- [10-video-multireference-ready.png](./10-video-multireference-ready.png)

## 14. 费用确认交互事故与停止边界

### 14.1 事故经过

视频节点的主操作靠近底部四区定位器，真实点击区域与定位器发生遮挡。鼠标操作只将按钮置于焦点状态；随后尝试用键盘继续操作时，同一焦点／按键链触发了 Element Plus 确认框的默认确认行为，意外创建了一个视频 Recipe 本地任务。

这不是用户明确确认预算后的预期提交。发现后立即停止所有生成操作，并完成以下限制：

- 没有再次点击视频生成；
- 没有启动或重启媒体 Worker；
- 没有通过 CLI、直接 API、数据库或 DevTools 修改／取消任务；
- 没有伪造失败或成功终态；
- 没有继续时间线、合成或导出。

### 14.2 页面可观察状态

通过新增的导演模式“全局任务”入口，在真实 Web 任务中心观察到：

```text
任务：治愈短片逐镜视频 V1
类型：video
状态：等待提交
进度：0%
提示：任务已进入持久队列
创建时间：2026-08-28 13:09:22（香港时间）
```

同时：

- 页面未显示 Provider task ID；
- 对应视频节点仍显示 0 个视频候选；
- 未观察到 Provider 完成结果；
- 任务中心没有“取消／停止”入口。

因此本报告不能断言“付费调用为 0”，也不能断言已经产生费用。准确结论是：**已创建 1 个本地持久队列任务；页面没有证据证明 Provider 已提交，但该任务若之后被 Worker 消费，可能产生付费调用。**

截图：[10-unintended-queued-task.png](./10-unintended-queued-task.png)

### 14.3 防复发修复

事故后已调整费用确认交互：

- 明确区分“确认并创建视频任务”和“取消，不创建任务”；
- 关闭确认框默认 autofocus；
- 禁止点击遮罩关闭；
- 视频确认层展示 Provider、模型、时长、分辨率、有序参考、输入哈希和费用；
- 异步事件处理错误被页面捕获并显示，不再形成未处理的 Vue native handler 异常。

由于当前项目已经存在活动的“等待提交”任务，未在该项目上重新点击生成来验证此修复，以免创建第二个任务或扩大费用风险。

## 15. 第二轮 Console 与错误记录

第二轮浏览器日志中保留了修复前的两条历史错误：

```text
ApiError: 当前阶段不能执行 recipe:video
[Vue warn]: Unhandled error during execution of native event handler
```

它们发生在 `requiredAnchorCount` 和前端异步错误捕获修复之前。当前页面刷新后，多参考视频节点已经处于可生成阶段；但由于费用安全停止条件，未通过再次创建任务来做破坏性复测。因此不能把第二轮描述为“全过程 Console 零错误”，只能确认错误路径已经纳入页面处理和自动化测试。

## 16. 第二轮状态汇总

| 项目环节 | 第二轮最终状态 | 说明 |
|---|---|---|
| 冷刷新与四区恢复 | 通过 | 无静默空白画布 |
| API 路由兼容 | 通过 | 旧进程重启后 405 消失，新增 feature 预检 |
| 场景视觉规划 | V2 已采用 | 2/2 必需槽位，四个 skip 持久化 |
| Scene Look | 关闭 | 不再阻塞普通多参考视频 |
| 分镜制作方案 | 已确认 | 真实 Web 完成 |
| 生产 Prompt | 已编译 | 真实 Web 完成 |
| 制作包 | 已批准 | 真实 Web 完成 |
| 视频输入模式 | reference_media | 9 项有序实际图片输入 |
| 首帧锚点 | 非必需 | 普通新增视频不再被阻塞 |
| 视频本地任务 | 等待提交 | 意外创建 1 个；无页面取消入口 |
| Provider 提交 | 无法确认 | 页面无 Provider task ID，候选为 0 |
| 视频审核与版本选择 | 未执行 | 费用安全停止 |
| 时间线、合成、导出 | 未执行 | 无批准视频版本 |

## 17. 第二轮合规与后续必要条件

### 17.1 合规核对

- 所有项目业务状态改变均来自真实 Web UI：通过。
- 服务重启只用于让 API 加载新代码，没有直接修改业务数据：通过。
- 未用 CLI／直接 API／SQL／repository 改变项目状态：通过。
- 未用 DevTools `fetch()`、DOM/Vue 状态修改或模拟响应：通过。
- 未启动媒体 Worker、未重复提交视频任务：通过。
- 报告未保存密钥、Authorization、Cookie、浏览器存储或签名 URL：通过。

### 17.2 继续第三轮付费验收前的必要条件

1. 通过正式 Web 能力处理当前“等待提交”任务：要么安全取消，要么由用户明确确认预算后继续原任务；不能在状态不明时创建第二个任务。
2. 为任务中心增加 queued/submitted 状态的明确取消语义，并处理 Provider 已提交与尚未提交的竞态，避免把“删除本地记录”伪装成取消 Provider。
3. 修正视频主操作与底部四区定位器的点击区域重叠，并用真实键盘路径验证费用确认不会由打开确认框的同一按键意外接受。
4. 用户明确确认预算后，才继续视频生成、审核、版本选择、时间线、转场、本地合成、导出和最终冷刷新恢复。

## 18. 自动化与构建验证

第二轮修复完成后执行：

| 验证 | 结果 |
|---|---|
| Python 全量测试 | `455 passed`，1 条第三方 Starlette/httpx 弃用 warning |
| 前端 Vitest | `38` 个测试文件、`138 passed` |
| `vue-tsc --noEmit` | 通过 |
| Vite 正式构建 | 通过，1727 个模块完成转换 |
| `git diff --check` | 通过；仅有工作区既有的 LF/CRLF 转换提示 |

前端测试输出仍包含 Vue Flow 在 jsdom 测试环境中的 `Viewport not initialized yet` 提示，但不影响用例结果；正式构建仅报告第三方 `@vueuse/core` PURE 注释位置和主包大于 500 kB 的非阻塞 warning。本轮没有把这些既有构建提示伪装成业务验收通过，也没有为消除 warning 改动无关模块。
