# CAT-VIDEO-GENERATOR V5 片段视觉与 LLM 辅助 Checklist

> 历史阶段记录，当前验收状态由 `v5-staged-creative-workflow.md` 接管；本文件中的完成标记不代表分阶段 LLM 替换已最终验证。

状态：`[ ]` planned、`[i]` implemented、`[u]` unit-checked、`[x]` final-verified。
只有全部功能完成后的正式本地数据链路通过，才能使用 `[x]`。阶段实施期间不升级
本地数据库、不执行 Canon repair、不运行完整 smoke，也不调用真实 Ark。

## 阶段 1：片段视觉策略与数据契约

- [x] 增加 `sceneLookUsage` 四种策略，并兼容旧 `useSceneLook`。
- [x] 增加片段 `draftRevision` 和 `0018_v5_shot_assistance` 迁移。
- [x] `appearance_only/full_reference/derive_anchor/off` 编译为不同素材职责。
- [x] `derive_anchor` 生成锚点后不把基础定妆重复提交给视频。

## 阶段 2：LLM 创作分析与节奏诊断

- [x] 保存片段与付费分析分离，分析失败不回滚人工稿。
- [x] 多模态分析覆盖当前及相邻片段、实际参考图和尾帧候选。
- [x] 本地规则给出动作密度、子镜头、声音和稳定收尾提示。
- [x] LLM 输出字段级建议，保存 `providerOutput/acceptedOutput` 审计。
- [x] 过期 revision 接受建议返回 409，允许逐项应用补丁。

## 阶段 3：跨片段尾帧锚点

- [x] 批准视频后本地抽取并追溯 `shot_tail_frame`。
- [x] 下一片段可一键采用上一批准片段尾帧作为唯一锚点。
- [x] 来源视频变更后旧尾帧显示过期，不再静默沿用。

## 阶段 4：Web 工作台

- [x] 编辑器提供四种场景定妆策略和逐次付费确认。
- [x] LLM 建议以字段差异展示并支持逐项接受。
- [x] 展示相邻片段衔接、尾帧预览和一键采用。
- [x] Prompt 区展示本地诊断、定性节奏、素材来源与职责。

## 阶段 5：教程适配与文档

- [x] 新增课程思想到本项目约束的适配说明，不修改原课程文件。
- [x] 同步完整工作流、HTTP API 和 Windows 恢复手册。

## 阶段 6：最终本地数据链路

- [x] 数据库升级到 `0018`，Canon 和历史片段迁移正确。
- [x] fake 多模态 LLM、差异接受、409 和零真实 Ark 调用通过。
- [x] fake Seedream 的派生锚点引用顺序和视频去重通过。
- [x] 本地视频尾帧抽取、下一片段锚点与 FFmpeg 合成通过。
- [x] pytest、Ruff、前端类型/组件测试和生产构建通过。
- [x] 脱敏诊断报告落盘。
