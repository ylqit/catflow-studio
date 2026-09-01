# V5 分阶段 LLM 创作工作流替换 Checklist

状态说明：`planned` 尚未实现；`implemented` 已落盘；`unit-checked` 已通过隔离测试；`final-verified` 已纳入最终一次完整数据链路验证。

## 阶段 0：基线与边界

- [final-verified] 保留 Alembic HEAD `0018_v5_shot_assistance`，不增加故事物理字段或 `0019` 迁移。
- [final-verified] 四阶段历史复用 `workflow_steps`、`prompt_records`、`providerOutput`、`acceptedOutput`、`acceptedAt` 和 `inputHash`。
- [final-verified] 同一 Ark 规划模型串行承担不同角色，不引入多智能体或多进程创作编排。

## 阶段 1：剧情诊断与剧情重写

- [final-verified] 增加剧情医生和剧本编辑的严格输出契约与独立 Prompt。
- [final-verified] 增加显式付费授权、上游接受门槛、过期输入保护和双稿审计。
- [final-verified] 接受剧情重写后更新现有 `Scene.sourceText`，不覆盖历史生成快照。
- [final-verified] 增加场景四阶段聚合查询。

## 阶段 2：分镜导演与视觉 Prompt 审稿

- [final-verified] 分镜导演只读取已接受剧情重写，或用户显式保留的当前剧情。
- [final-verified] 视觉审稿查看实际有效参考图片、相邻片段、项目视觉档案和场景剧情。
- [final-verified] 视觉审稿候选仅来自当前实际上下文，并按后端真实来源顺序重排后提交同一规划模型。
- [final-verified] 审稿输出可编辑 Seedance 创作正文与候选正文，并继续逐字段接受。
- [final-verified] 接受创作正文写回现有 `ShotCard.direction`。

## 阶段 3：素材职责与最终 Prompt

- [final-verified] Canon、场景定妆、批准锚点的职责由资产来源确定，不允许任意改写。
- [final-verified] Provider 输入按模式分流：批准锚点作为唯一 `first_frame`；无锚点时普通参考图按“片段自定义 → 场景定妆 → 项目 Canon”合并。
- [final-verified] 项目默认为空时使用视觉档案 Canon 引用回退，并按场景环境筛选互斥画风图。
- [final-verified] Prompt 预览拆分 LLM 创作正文、系统技术外壳、实际素材和最终 Provider Prompt。
- [final-verified] 为当前项目提供人工触发的 Canon 默认引用修复，不删除历史。

## 阶段 4：Web 创作工作台

- [final-verified] 增加原始剧情、剧情诊断、剧情重写、分镜设计、视觉与 Prompt 审稿五步视图。
- [final-verified] 每次付费调用前显示模型、输入和费用确认。
- [final-verified] 支持方案选择、接受稿编辑、保留当前稿、历史版本和失败重试。
- [final-verified] 素材或定妆策略变化后清空旧 Prompt 预览，用户重新预览时按真实输入编译。

## 阶段 5：验证

- [final-verified] fake LLM 隔离测试覆盖四角色、门槛、失败、过期、双稿和素材职责。
- [final-verified] 前端组件测试覆盖分阶段门槛、视觉正文候选和分层 Prompt 类型。
- [final-verified] 扩展本地数据链路脚本，真实 Ark 调用计数保持 0。
- [final-verified] 完成唯一一次 pytest、Ruff、前端测试、生产构建和完整本地数据链路。
