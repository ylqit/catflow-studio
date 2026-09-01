# CAT-VIDEO-GENERATOR V5 完整替换实施 Checklist

> 历史阶段记录，当前验收状态由 `v5-staged-creative-workflow.md` 接管；本文件中的完成标记不代表分阶段 LLM 替换已最终验证。

本清单是角色与画风一致性修订后的唯一验收依据。状态说明：

- `[ ]` planned：尚未实现。
- `[i]` implemented：代码已经落盘，尚未完成最终数据链路验证。
- `[u]` unit-checked：隔离单元或组件检查通过，不代表本地数据库/媒体链路已验证。
- `[x]` final-verified：仅在全部功能完成后的最终一次本地数据链路通过后使用。

实施阶段不调用真实 Ark；在所有代码完成前，不升级本地数据库、不执行 Canon repair、不运行 `scripts/local_dataflow_smoke.py`。

## 阶段 1：运行时与资产读取

- [x] FFmpeg/FFprobe 按有效显式配置、系统 PATH、不可用的顺序发现。
- [x] doctor 与健康接口分别报告数据库、Ark、FFmpeg、FFprobe、视频生成和本地合成能力。
- [x] `shot_card_id` 稳定序列化为公开 `shotId`，项目图不再因旧字段返回 500。
- [x] `legacy:` 资产只显示为内容不可用，不读取旧机器路径，也不拖垮项目图。
- [x] Canon 接口只返回已批准 Canon，所有预览与选择统一使用 `contentReady`。

## 阶段 2：Canon 与项目视觉档案

- [x] 增加 `0017_v5_visual_profile`，不改写既有 `0016`。
- [x] 项目视觉档案按内容版本化，历史生成快照继续指向旧 revision。
- [x] 默认人物统一为 5–7 岁短发儿童，样片只定义画风，不定义角色身份。
- [x] Canon repair 保留 11 个运行时资产 ID、状态和历史。
- [x] 引用同时按资产 ID 与 SHA-256 去重。
- [x] Canon Web 页面支持编辑、恢复默认和保存项目视觉档案 revision。

## 阶段 3：场景定妆草稿与 Prompt

- [x] `SceneLookPlan` 增加环境、人物姿态、猫咪姿态、构图和补充要求。
- [x] 场景持久化定妆草稿、草稿 revision 和专用参考绑定。
- [x] 定妆参考区分人物身份、人物体型、猫咪身份、画风、服装、道具和构图职责。
- [x] Prompt 预览显示固定引用顺序、职责、数量和预检警告，不调用 Ark。
- [x] 定妆生成前要求人物、猫咪和画风三类参考齐全，文件可用且总数不超过 14。
- [x] 生成审计保存视觉档案 revision、草稿 revision、资产 ID、SHA、职责和 Prompt SHA。

## 阶段 4：Web 定妆工作台与版本画廊

- [x] 场景定妆使用独立编辑对话框，提供视觉锁定、造型、参考资产和 Prompt 预览。
- [x] 首次生成和重新生成都使用可编辑草稿；重做另存单项修正原因。
- [x] 生成完成后可查看大图、状态、Prompt、参考缩略图和历史版本。
- [x] 支持批准并选择、拒绝和切换已批准版本。
- [x] 图片缺失或加载失败显示资产 ID、HTTP 状态和修复建议，不显示破图。

## 阶段 5：AI 建议、视频继承与文档

- [x] AI 建议包含环境、姿态和构图，接受前可以编辑。
- [x] 原始 `providerOutput` 与编辑后 `acceptedOutput` 分开审计。
- [x] 已有图片或视频历史时整批覆盖返回 409。
- [x] 视频引用按片段自定义、场景定妆、项目视觉档案排序，并按 ID/SHA 去重，最多 9 张。
- [x] 文档同步视觉档案版本、定妆编辑器、Canon repair 和故障恢复流程。

## 阶段 6：最终一次本地数据链路

- [x] 停止并重新启动本项目旧 Uvicorn/Vite 进程。
- [x] 数据库升级到 `0017` 并执行 Canon repair。
- [x] 11 张 Canon 均可读取，项目视觉档案和定妆草稿可保存及回读。
- [x] fake Seedream 收到正确顺序的角色/画风参考，真实 Ark 调用次数为 0。
- [x] Web/API 链路可读取、预览、批准并选择定妆候选。
- [x] fake 导演覆盖单片段、多片段、编辑接受和双稿审计。
- [x] 引用继承、ID/SHA 去重、开关和 14/9 张上限正确。
- [x] `docs/采茶叶.mp4` 通过 FFprobe 并完成 FFmpeg 本地合成和媒体回读。
- [x] pytest、Ruff、前端组件检查和生产构建全部通过。
- [x] 脱敏结果写入 `var/diagnostics/v5-local-dataflow.json`。
