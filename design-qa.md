# Design QA — Toonflow 式“一人一猫”生产系统（2026-09-01）

## 对照目标

- 剧本文档工作区：`D:\soft\code\PycharmProjects\Toonflow-app\docs\screenshot\2.png`，原始像素 `3840×2100`。
- 空间生产工作区：`D:\soft\code\PycharmProjects\Toonflow-app\docs\screenshot\6.png`，原始像素 `1920×1050`。
- 视频生成工作区：`D:\soft\code\PycharmProjects\Toonflow-app\docs\screenshot\9.png`，原始像素 `1920×1050`。
- 对照目标是 Toonflow 的单任务信息架构、媒体优先级、空间生产关系和参考／Prompt／结果／版本同屏结构；当前产品保留自己的深色视觉、Canon、安全任务状态和费用确认语义。

## 实现证据

- 项目列表：`design-qa-assets/toonflow-production-system-20260901/01-projects-1440x900.png`。
- 生产画布：`design-qa-assets/toonflow-production-system-20260901/03-production-1440x900-fixed.png`。
- 剧本工作区：`design-qa-assets/toonflow-production-system-20260901/04-script-1440x900.png`。
- 角色资产工作区：`design-qa-assets/toonflow-production-system-20260901/05-assets-1440x900.png`。
- 视频工作台预览：`design-qa-assets/toonflow-production-system-20260901/08-workbench-preview-fit.png`。
- 剪辑与交付：`design-qa-assets/toonflow-production-system-20260901/09-delivery-1440x900.png`。
- 专业分镜编辑：`design-qa-assets/toonflow-production-system-20260901/10-storyboard-1440x900.png`。
- 视频生成同屏工作区：`design-qa-assets/toonflow-production-system-20260901/11-generate-1440x900.png`。

实现截图实际像素为 `1440×887`，对应内置浏览器 `1440×900` CSS 视口中的应用内容区，`deviceScaleFactor=1`。对照图采用 `contain`等比缩放到 `1440×900`槽位，保留完整画面、不裁切、不拉伸；实现图同样置入 `1440×900`槽位。最终并排图为 `2880×900`：

- `design-qa-assets/toonflow-production-system-20260901/comparison-script-1440x900.png`
- `design-qa-assets/toonflow-production-system-20260901/comparison-production-1440x900.png`
- `design-qa-assets/toonflow-production-system-20260901/comparison-video-1440x900.png`

## 验证状态

- 项目：`一人一猫-旧版看板Web全链路试验-20260829-r1`。
- 剧本：Revision 1，可编辑标题、摘要和完整长正文。
- 生产画布：六个稳定业务产物；选中产物使用左侧上下文面，不出现大型通用节点浮层。
- 分镜：Storyboard Revision 4，`1`镜、`8/8s`；主编辑区显示人类可读场景名，原始 ID仅位于审计折叠区。
- 视频生成：`reference_media`，五张真实 Provider 输入，专业自然语言 Prompt，视频版本 V1，当前镜头与两条历史镜头分离。
- 视频工作台：预览、视频生成、剪辑与交付三个可恢复标签。
- 历史链接：旧地址只做一次规范化转换，不加载第二套页面。
- 本轮没有确认费用、创建任务或调用付费 Provider。

## 并排比较结论

### 全视图

- 剧本工作区保留了参考中的“助手＋长文档”主关系，同时把候选、Revision、保存和正式剧情选择收进产品现有的版本语义。正文占据主要空间，内部技术信息没有与正文竞争。
- 生产画布与参考一样把空间关系作为主要表达，但将画布收敛为六个稳定产物；左侧检查面、中央关系图、右侧可折叠助手和底部镜头条形成单一生产任务。
- 视频工作台延续参考的顶部素材条、Prompt、结果、历史版本和胶片条同屏结构，并增加冻结输入职责、真实任务状态、审计信息和费用安全边界。
- 深色视觉是明确的产品差异，不属于设计漂移。层级通过背景明度、边界和间距建立，没有复制参考产品的品牌、图标或素材。

### 重点区域

- 视频参考条：五张输入的缩略图、顺序、职责和权威状态在并排图中可直接辨认；绿色叶片来源图没有进入实际 Provider 输入。
- Prompt 区：可见内容为模型实际读取的专业自然语言；数据库 ID、Revision、Hash 和内部 Schema 未混入正文。
- 分镜区：标题、完整镜头描述、时长、场景、总时长和保存状态在同一编辑层；不再通过抽屉或底部窗口拆散上下文。
- 媒体区：角色、猫咪、同框、环境和净化画风板使用真实资产缩略图，没有使用占位图、手绘图标或伪造素材。

## 必查视觉面

- 字体与排版：中文使用系统无衬线字体栈；标题、正文、小型状态文字和审计文字层级稳定，长正文行高充足，没有影响任务的截断或错误换行。
- 间距与布局节奏：应用轨、项目栏、工作区和近全屏 Workbench 的间距一致；主任务获得主要面积；按钮目标不小于 `44×44px`。
- 颜色与视觉令牌：深色背景、较亮工作表面、蓝色主操作、绿色完成、橙色失效／注意和红色阻断具有一致语义；状态不只依赖颜色。
- 图片质量与资产一致性：所有可见人物、猫咪、同框、环境和视频画面来自项目真实资产；缩略图使用正确裁切，播放器使用完整画面适配。
- 文案与内容：用户可见文案使用“剧本、角色资产、生产画布、视频工作台”等直接任务语言；内部素材键、场景 UUID 和历史技术名称已经从主要创作面移除。

## Findings

- 没有未解决的 P0、P1 或 P2 问题。
- [P3] 正式构建仍提示主入口包大于 `500 kB`。这不影响当前交互和视觉验收，但后续可继续把 Element Plus 和任务中心拆入独立 chunk。
- [P3] 生产画布允许用户平移后只保留部分产物在视口内；冷启动和普通总览会在 Vue Flow 初始化完成后重新适配六个产物，深链接仍优先聚焦指定产物。

## Open Questions

- 无阻塞问题。当前历史制作包仍保留不可变审计内容；当其旧 Prompt 使用内部素材键时，界面要求用户从生产画布重新编译并确认，而不是静默改写历史记录。

## Comparison History

1. 初始实现仍让生产关系依赖大量通用节点和重复导航。
   - 修复：项目内入口收敛为剧本、角色资产、生产画布；生产关系固定为六个业务产物。
   - 证据：`02-production-1440x900-before.png` → `03-production-1440x900-fixed.png`。
2. 初始视频预览缩放使竖屏画面不能完整阅读，工作台的主次区域也不够稳定。
   - 修复：播放器改为完整画面适配，Workbench 使用近全屏三标签结构。
   - 证据：`06-workbench-preview-1440x900-before.png` → `08-workbench-preview-fit.png`。
3. 初始参考素材与 Prompt 仍可能暴露内部素材键，并把当前镜头与历史镜头混在同一胶片条。
   - 修复：用户可见名称在读取边界投影；实际五张输入显示职责；历史镜头进入显式“历史 2”；旧 Prompt 保持不可变并在提交前阻断重编译。
   - 证据：`comparison-video-1440x900.png`与`11-generate-1440x900.png`。
4. 分镜主编辑区曾显示原始场景 UUID。
   - 修复：领域投影增加人类可读场景标题；UUID移动到审计折叠区，保存时不写入派生标题。
   - 证据：`10-storyboard-1440x900.png`。
5. 异步模块首次加载可能出现无说明空区，生产画布恢复历史视口时可能在 Vue Flow 尚未初始化前执行适配。
   - 修复：模块宿主增加 Suspense 可见回退；视口适配归入 Vue Flow `init`生命周期；程序化适配不产生重复布局写入。
   - 证据：`ProductionWorkspace.spec.ts`、`ProjectWorkspaceView.vue`及内置浏览器冷启动／模块切换记录。

## Implementation Checklist

- [x] 三个项目工作区与统一外壳。
- [x] 六个稳定生产产物节点。
- [x] 专业分镜编辑层与 Revision／stale 保护。
- [x] 五张真实输入、专业 Prompt、结果和版本同屏。
- [x] 视频预览、局部编辑、时间线、合成和导出复用同一 Workbench。
- [x] 历史链接规范化转换。
- [x] 费用、幂等、任务取消、Provider 状态和审计语义保持。
- [x] 468 个后端测试、103 个前端测试、Ruff、TypeScript 和正式构建通过。
- [x] 内置浏览器核心路径、键盘可编辑字段、历史恢复和无付费路径通过。

## Follow-up Polish

- 后续仅需处理包体拆分等 P3 性能优化，不影响本次产品切换。

final result: passed
