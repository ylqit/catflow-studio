# V4 校色实际提示词与输入来源

本轮共五次内置 imagegen 调用。每次编辑目标均来自 white-cat-v4-template-edit。颜色依据为原始 assets/canon/v4/白猫.png；没有引用 V3 图片或 V3 校色结果。

## 1. V4 侧面站姿

当前结果：[side-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/side-standing.png)

工具输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-3a2f99ad-c29a-4432-85e1-47ca57257e76.png)

实际输入顺序：

1. [side-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/side-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)

```text
局部毛色校正。图1是需要编辑的 V4 侧面站姿图；图2仅提供原始白猫的毛色依据。

把图1猫身上偏奶油、米黄、桃色的白毛改成干净的中性白。口鼻、胸毛、腹部和爪端的亮部接近 RGB(255,255,253)，阴影用很浅的中性灰，保留毛束层次。明显去除猫身的黄红色偏，不能仍是象牙白。灰纹降低偏棕、偏土黄的色度，保留细微自然暖灰成分与深浅层次，不染成蓝灰，不改变花纹形状位置。鼻子与耳内保持粉色，眼睛保持深色。

严格保留图1的脸型、眼睛大小、毛发轮廓、头身比例、朝左的单眼侧视角、躯干长度、四脚落点、后腿关节和竖起上弯的尾巴。尾巴必须维持 V4 原图上扬姿态。不要重画新猫，不采用图2的坐姿。保留图1柔和数字插画的完成度，不加回浓密彩铅排线。

仅给猫咪毛发去偏色；保留原背景、构图、画布比例与接触阴影。不是整幅图调色，不能整体提亮、降饱和或冷却背景。全身完整，无新增文字、物品、水印。输出原画面比例的 PNG。
```

## 2. V4 正面站姿

当前结果：[front-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/front-standing.png)

工具输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-4413c26f-2c8a-4dd4-b926-f666aa5e7dd2.png)

实际输入顺序：

1. [front-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/front-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)

```text
局部毛色校正。图1是 V4 正面站姿编辑底图；图2仅提供原始白猫的毛色。

把图1白毛上的奶油色、米黄和浅桃色改成干净的中性白。口鼻、胸口、四肢和爪端亮部接近 RGB(255,255,253)，保留浅灰阴影、分层胸毛与细节，明显去除黄红色偏。灰纹降低过强的棕黄色度，保留少量自然暖灰与深浅层次，条纹位置、形状和面积维持原样。粉鼻、粉耳保留，眼睛维持原有深色观感。

严格保留 V4 底图的圆脸、眼睛大小和位置、头身比例、毛发轮廓、正面站姿、脚掌落点、右侧可见尾巴以及原画布构图。不要重画新猫，不采用图2的坐姿，不重新设计花纹。沿用底图的柔和数字插画完成度，不恢复浓密彩铅排线。

只调整猫毛颜色；背景、接触阴影位置和画布长宽比保持原样。不是全图提亮或全图降饱和。无新增文字、物品、水印。输出与图1相同构图比例的 PNG。
```

## 3. V4 背面站姿

当前结果：[rear-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/rear-standing.png)

工具输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-1503d0e4-daa4-4cba-984c-46bcb5277976.png)

实际输入顺序：

1. [rear-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/rear-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)

```text
局部毛色校正。图1是 V4 背面站姿编辑底图；图2仅提供原始白猫的毛色。

把图1颈背、背部白色间隔、后腿、爪端与尾巴白毛段的奶油色和米黄偏色改为干净的中性白。亮部接近 RGB(255,255,253)，阴影使用很浅的中性灰，保持毛发层次。灰纹去掉过强的棕黄成分，保留低饱和暖灰的少许自然色感。保持每一处背纹和尾纹原来的形状、位置与深浅关系，不加新纹、不重设计背部。

严格保留 V4 底图的后脑轮廓、毛发覆盖的耳背、肩背、骨盆、后腿、脚掌落点、尾根，以及竖起后向右弯曲的尾巴。不要改为低垂尾巴，不露正脸、眼睛或粉色耳内。保留身体比例与原站姿。

只做猫毛校色。保留原数字插画风格、毛束细节、背景颜色、阴影位置、构图和画布长宽比。不恢复密集彩铅排线，不整体增亮或冷却背景。无新增文字、物品、水印。输出原构图比例的 PNG。
```

## 4. V4 人猫同框比例

当前结果：[pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/pair-scale.png)

工具输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-4d3d6e40-df4b-43e8-ae59-13e728fd8f66.png)

实际输入顺序：

1. [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/pair-scale.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-3a2f99ad-c29a-4432-85e1-47ca57257e76.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-3a2f99ad-c29a-4432-85e1-47ca57257e76.png)

```text
只修改图1右下角猫咪的毛色。图1是 V4 人猫比例图，图2是原始白猫的颜色依据，图3是本轮校正后的 V4 侧面猫颜色范例。

把图1猫咪口鼻、胸口、腹部、腿和爪端上所有米白、奶油白、浅桃色改为干净的中性白，亮部接近 RGB(255,255,253)，阴影用浅中性灰，保留毛束细节。白毛必须明显去除黄红色偏，不能仍是米色。灰纹降低棕黄饱和度，保留自然低饱和灰色及少量暖灰，不变蓝灰。粉鼻粉耳和深色眼睛保留。

严格保留图1猫的大小、位置、朝左单眼侧面站姿、躯干比例、四脚落点和竖起上扬尾巴，花纹形状与位置不变；图3只帮助保持颜色一致。猫仍在儿童右侧，不能改成正面、坐姿或低垂尾巴。

儿童的脸、头发、肤色、衣服、身材、姿势、大小和位置保持原样。背景、地面与整个画布保持原样。只给猫去偏色，不对全图提亮、降饱和或调白平衡。无新增文字、物品、水印。输出与图1相同的 9:16 竖版 PNG。
```

## 5. V4 三视图

当前结果：[three-view-board.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-color-corrected/three-view-board.png)

工具输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-3db5469d-9274-4f4e-8d83-65636de3a32e.png)

实际输入顺序：

1. [three-view-board.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/three-view-board.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-4413c26f-2c8a-4dd4-b926-f666aa5e7dd2.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-4413c26f-2c8a-4dd4-b926-f666aa5e7dd2.png)
4. [exec-3a2f99ad-c29a-4432-85e1-47ca57257e76.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-3a2f99ad-c29a-4432-85e1-47ca57257e76.png)
5. [exec-1503d0e4-daa4-4cba-984c-46bcb5277976.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-1503d0e4-daa4-4cba-984c-46bcb5277976.png)

```text
局部毛色校正。图1是 V4 三视图编辑底图；图2是原始白猫的毛色依据；图3、图4、图5分别是本轮完成校色的 V4 正面、侧面、背面颜色参考。

只调整图1三只猫的毛色，保留整张 V4 三视图的 2:1 横版布局、各视角位置、尺寸、落脚线与原有姿势。左侧正面站姿、中间朝左侧面站姿、右侧背面站姿；侧面与背面的尾巴必须保持竖起上弯。各视角的轮廓、比例、脸型、脚掌、毛束和花纹形状位置不变。

把三只猫白毛上的米黄、奶油色、浅桃色统一改成干净的中性白。亮部接近 RGB(255,255,253)，浅灰阴影保留体积与毛束细节。白毛不能再泛米黄。降低灰纹中过强的棕黄色度，保留自然低饱和灰色与少量暖灰，不染蓝灰；参考图3至5的校色效果，保持深浅层次。鼻子耳内仍为粉色，眼睛维持深色；背面只能显示毛发覆盖的耳背，不露正脸。

保留 V4 原图柔和数字插画的完成度，背景颜色、留白、接触阴影位置与画布长宽比保持原样。不是重新生成一套猫，不恢复浓密彩铅排线，不全图提亮、降饱和或冷却背景。全身、尾巴完整，无文字、网格、水印或新物体。输出相同 2:1 横版构图 PNG。
```

提示词中的保持原样为编辑约束，不表示生成结果逐像素无损。

