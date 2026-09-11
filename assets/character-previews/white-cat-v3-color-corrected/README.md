# v3 白猫：参照原始白猫的校色预览

状态：此目录由助手误将 V4 校色请求作用于 V3 产生，不是用户本次要求的版本。文件仅作历史保留，正确结果见 [V4 校色预览](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/README.md)。以下为当时的处理记录。

本轮只解决色彩问题。保留 v3 的站姿与构图作为编辑约束，并不代表对其身形、比例或新增背纹重新定稿。新文件另存于本目录，状态为校色预览。

## 五项交付

| 内容 | 输入图 | 当前结果 | 实际尺寸 |
|---|---|---|---|
| 正面站姿 | [校色前](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/front-standing.png) | [校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/front-standing.png) | 1254 × 1254 |
| 侧面站姿 | [校色前](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/side-standing.png) | [校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/side-standing.png) | 1536 × 1024 |
| 背面站姿 | [校色前](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/rear-standing.png) | [校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/rear-standing.png) | 1254 × 1254 |
| 三视图 | [校色前](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/three-view-board.png) | [校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/three-view-board.png) | 2172 × 724 |
| 人猫同框比例 | [校色前](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/pair-scale.png) | [校色后](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/pair-scale.png) | 941 × 1672 |

[color-source.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/color-source.png) 是原始白猫的直接副本，保留 2048 × 2048、RGBA 透明通道与原色彩配置；SHA256 与源文件一致。

## 调整内容

白毛的颜色依据回到原始白猫近中性的亮部，去掉猫身上覆盖过广的奶油色、米色和桃色。白毛仍保留浅灰明暗及毛束细节。灰纹降低过强的红黄与棕色倾向，保留原图少量自然暖灰成分，维持当前条纹位置与深浅层次。

正面、侧面、背面和三视图以现有图为编辑目标。人猫图经过局部校色与校准正面图的插入尝试；插入尝试未取得更好的颜色结果，因此未采用，最后以明确白色要求完成选定版本。人物与背景的外观及构图作为保留目标。

本轮没有给整图套用统一降饱和或增亮滤镜，也没有把原始彩铅排线全部重新加回。全部图片编辑均由内置 imagegen 完成。

## 局部颜色检查

每项在校色前后相同坐标取 13 × 13 像素区域，按 RGB 各通道中位数记录。下表为白毛代表性样本；背面取颈背白毛阴影，其明度自然低于正面亮部。

| 图片 | 校色前白毛样本 | 校色后白毛样本 | 红通道减蓝通道 |
|---|---|---|---|
| 正面站姿 | #FCF5EE | #FDFCFA | 14 → 3 |
| 侧面站姿 | #FCF6F1 | #FBFAF9 | 11 → 2 |
| 背面站姿 | #F0E6DF | #F0EFEF | 17 → 1 |
| 三视图 | #FDF8F2 | #FDFDFD | 11 → 0 |
| 人猫同框比例 | #FBF3ED | #FCFBFA | 14 → 2 |

所选白毛区域红蓝差从 11–17 降至 0–3，说明这些局部的暖色偏移减轻。该指标只用于辅助观察所选白毛，不能当作全身平均、标准色差值或身份相似度评分。不同图的局部光照、纹理和取样部位不同，不要求所有白毛像素都是同一个颜色。

完整取样坐标、RGB、灰纹样本和方法保存在 [color-check.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/color-check.json)。原始白猫胸口参考 #FFFFFD，取样区域完全不透明；原图 ICC 为 sRGB，转换检查未改变该代表值。

## 检查范围与边界

五张最终 PNG 均通过解码检查，尺寸与各自的 v3 输入相同。已目视检查白毛、灰纹冷暖、现有站姿、面部位置与三视图构图。正式 Canon 的四张素材仍与其 manifest 的 SHA256 相符。

生成式局部编辑存在笔触和少量非猫区域像素变化。尤其人猫图，儿童区域只读比较非零，因此不能声称儿童或背景逐像素完全不变。当前结果没有经过视频转身一致性或严格几何校准。

完整提示词及实际引用顺序见 [generation-prompts.md](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/generation-prompts.md)，文件哈希与输出来源见 [preview-index.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/preview-index.json)。drafts/ 为未采用的中间稿，不属于当前交付。

文件检查与像素取样使用只读图像分析；原图没有通过脚本重绘、调色或覆盖。
