# CAT-VIDEO-GENERATOR V5 视觉制作工作台 Checklist

状态：`planned` 尚未落盘；`implemented` 已完成实现和代码自检；`final-verified` 已完成最终前端点击验收。

本轮不增加 Alembic 迁移，数据库 HEAD 保持 `0018_v5_shot_assistance`；不补充批量测试文件，不调用真实 Ark。所有功能完成后只执行一次浏览器页面点击验证。

## 阶段 1：视觉制作看板

- [implemented] 分镜确认后进入五阶段制作看板：场景视觉基准、片段开场、视频生成、审核与衔接、成片编排。
- [implemented] 片段卡显示中文制作状态、封面、六类视觉来源、实际提交数量、版本数量、阻塞原因和唯一主要操作。
- [implemented] `GET /projects/{id}/production-board` 只聚合现有数据，不持久化派生状态。

## 阶段 2：场景视觉基准

- [implemented] 工作台采用左侧大图与版本带、右侧造型/参考/Prompt 的聚焦布局。
- [implemented] 场景视觉基准明确标注“不是视频首帧”，只承担本场共同服饰、环境、道具和画风。
- [implemented] 每次生成或重试追加 V1/V2/V3 候选，保留历史 Prompt、输入快照和审核状态。

## 阶段 3：片段生成台

- [implemented] 移除固定 390px 右栏，片段改用全屏生成台。
- [implemented] 左侧统一预览开场图、视频和历史候选；右侧提供开场设计、参考素材、分镜与 Prompt、生成设置和版本历史。
- [implemented] 五种业务开场方式映射到现有 `anchorMode` 和 `sceneLookUsage`，不新增业务字段。
- [implemented] 依赖独立开场图但没有批准锚点时，在付费提交前显示阻断原因。

## 阶段 4：真实素材与 Prompt

- [implemented] 开场图和视频分别显示实际参考图、`@图片N`、来源层级、职责和文件状态。
- [implemented] 看板分别识别人物、猫咪、画风、场景、道具和开场来源，不再用项目参考总数冒充每类就绪状态。
- [implemented] 默认展示人工创作正文；素材映射、技术外壳、输入哈希和 Provider Prompt 放入专家折叠区。
- [implemented] 预览和真实提交继续复用同一个生成规格编译入口。

## 阶段 5：任务、版本与连续性

- [implemented] 长任务提交后立即交给全局任务中心，不在 Studio 定时刷新整页或等待任务完成。
- [implemented] 锚点和视频版本统一展示运行中、候选、批准、拒绝、失败、待对账及基于旧输入状态。
- [implemented] 生成台显示上一批准片段尾帧、可采用/已采用/过期状态和显式采用操作。
- [implemented] 成片编排继续支持硬切、淡黑和短叠化，并使用现有 FFmpeg 本地合成。

## 阶段 6：唯一最终验证

- [final-verified] 使用 Ark 禁用的本地验收服务实际点击“湖泊钓鱼”：查看 2 个场景基准版本、4 个片段制作卡、开场/视频双目标参考、旧输入视频版本，并批准 V1 视频、抽取尾帧、在下一片段显式采用尾帧。
- [final-verified] 在浏览器中进入成片编排并完成一次本地 FFmpeg 合成；任务提交后页面保持可操作，全局任务中心显示“本地成片合成 · 成功”。
- [final-verified] 修复点击中发现的首屏 Prompt 预加载、未配置 Ark 返回 500、尾帧已采用状态识别三项问题；新浏览器会话控制台 0 error / 0 warning。
- [final-verified] 验收进程显式清空 `ARK_API_KEY`，真实 Ark 新调用数为 0；关键截图保存在 `output/playwright/`（该目录不纳入产品数据模型）。
