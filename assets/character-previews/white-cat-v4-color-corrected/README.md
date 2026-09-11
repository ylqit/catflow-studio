# V4 白猫校色预览

本轮正确编辑对象为 **white-cat-v4-template-edit 中的五张 V4 图片**，以[原始白猫](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)校正毛色。上一轮 white-cat-v3-color-corrected 属于版本理解错误产生的历史结果，本轮没有使用其中任何图片。

## 五项 V4 结果

| 内容 | 编辑目标 | 当前交付 | 实际尺寸 |
|---|---|---|---|
| V4 正面站姿 | [V4 原图](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/front-standing.png) | [V4 校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/front-standing.png) | 969 × 1623 |
| V4 侧面站姿 | [V4 原图](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/side-standing.png) | [V4 校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/side-standing.png) | 1137 × 1383 |
| V4 背面站姿 | [V4 原图](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/rear-standing.png) | [V4 校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/rear-standing.png) | 982 × 1602 |
| V4 三视图 | [V4 原图](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/three-view-board.png) | [V4 校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/three-view-board.png) | 1774 × 887 |
| V4 人猫同框比例 | [V4 原图](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/pair-scale.png) | [V4 校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/pair-scale.png) | 941 × 1672 |

本轮保留 V4 的站姿与构图作为约束：侧面和背面为上扬尾巴，比例图里的猫在儿童右侧朝左站立，三视图维持原有 2:1 排列。颜色调整不等于重新核准 V4 的身形和背纹设计。

## 校色内容

白毛减轻奶油色、米黄和浅桃色倾向，亮部恢复为接近中性的白色，保留浅灰阴影与毛束细节。灰纹减少过强的棕黄成分，保留低饱和灰色层次。粉鼻、耳内和深色眼睛维持原有识别特征。

猫咪的脸型、站姿、尾巴动作、花纹位置和画面布局为保留目标。背景及人猫图中儿童的外观和构图也作为保留目标；生成式局部编辑仍带来少量笔触与非猫区域像素变化，不能称为无损校色。

[color-source.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/color-source.png) 是原始白猫的直接副本，RGBA、2048 × 2048，透明通道及色彩配置保留。源文件与副本 SHA256 一致。

## 代表性白毛检查

每项在校色前后相同坐标取 13 × 13 像素区域，以各通道中位数记录：

| 图像 | 校色前 | 校色后 | 红通道减蓝通道 |
|---|---|---|---|
| V4 正面站姿 | #F8F2EC | #FAF9F7 | 12 → 3 |
| V4 侧面站姿 | #F7F1EC | #FBFAFA | 11 → 1 |
| V4 背面站姿 | #F4EDE8 | #FAF9F8 | 12 → 2 |
| V4 三视图 | #F7F1EB | #FAF8F6 | 12 → 4 |
| V4 人猫同框比例 | #F5EFE9 | #F9F7F6 | 12 → 3 |

所选白毛区域红蓝差从 11–12 降到 1–4，支持这些局部的暖色偏移已减轻。此表不是整只猫的平均色、标准色差或身份相似度评分。不同局部光照与笔触仍存在差异，不要求全身白毛像素相同。

完整坐标、RGB、灰纹样本和比较方法见 [color-check.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/color-check.json)。人猫图儿童区域比较非零，已经明确记录像素未完全保持的限制。

## 文件与来源检查

五项最终 PNG 均通过解码检查，尺寸与相应 V4 输入一致。全部编辑目标、原始白猫及本轮校色参考已列入 [generation-prompts.md](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/generation-prompts.md)。使用内置 imagegen，共五次调用，未引用 V3 图片。

原 V4 目录内六张图片仍与其保存的清单哈希一致；正式 Canon 四张素材仍与 manifest 相符。本轮没有替换正式生产素材或运行时引用，也没有进行视频转身验证。

输出文件尺寸、SHA256 与工具来源见 [preview-index.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/preview-index.json)。Python 只用于只读解码、取样与文件检查，未用于图像重绘、调色或合成。

