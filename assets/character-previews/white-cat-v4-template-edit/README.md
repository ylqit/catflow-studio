# 白猫：以原定稿图为底图的定向编辑预览

本轮完成正面站姿、侧面站姿、背面站姿、三视图、人猫同框比例五项。状态为模板定向编辑预览，未替换正式 Canon。white-cat-v1/v2/v3 均未作为本轮参考输入。

## 身份母图与编辑底图

唯一白猫身份依据为用户指定的[最终适配稿](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-cf668a2b-9f58-4836-a3f7-6757d515469d.png)。本目录 [identity-source.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/identity-source.png) 为直接复制，SHA256 与源文件相同：

`4eb638ad80effb9410abab5045c84e69abf5bda8a2080b8277183c888bdbefd1`

原定稿图提供站姿、视角、骨架、四肢落点、尾巴动作和画面构图。白猫适配稿提供圆脸、深色大眼、粉鼻、耳朵特征、白底浅灰褐纹和胸腹毛量。编辑中去除旧猫的金棕眼睛、深灰头罩和大片灰底。

| 内容 | 本项原定稿底图 | 当前编辑结果 | 实际像素尺寸 |
|---|---|---|---|
| 正面站姿 | [原定稿底图](D:/soft/code/OpenGit/catflow-studio/风格定稿/Canon-v1/猫咪-正面.png) | [front-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/front-standing.png) | 969 × 1623 |
| 侧面站姿 | [原定稿底图](D:/soft/code/OpenGit/catflow-studio/风格定稿/Canon-v1/猫咪-侧面.png) | [side-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/side-standing.png) | 1137 × 1383 |
| 背面站姿 | [原定稿底图](D:/soft/code/OpenGit/catflow-studio/风格定稿/Canon-v1/猫咪-背面.png) | [rear-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/rear-standing.png) | 982 × 1602 |
| 三视图 | [原定稿底图](D:/soft/code/OpenGit/catflow-studio/风格定稿/Canon-v1/猫咪三视图.png) | [three-view-board.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/three-view-board.png) | 1774 × 887 |
| 人猫同框比例 | [原定稿底图](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/pair-scale.png) | [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/pair-scale.png) | 941 × 1672 |

保留原图长宽比和构图方向；未统一放大到 2048 × 2048。

## 本轮具体处理

- 正面：以原猫正面站姿为底图，将母图的圆脸、深色眼睛、额头与颊纹以及白色分层胸毛应用到站姿中；四肢和尾巴位置受原图约束。
- 侧面：保留原图朝左侧视角、躯干长度、前后腿关系与上扬尾巴动作；应用白毛底色、浅灰褐纹、白色胸腹和深色眼睛。侧面保持单眼可见。
- 背面：保留原图背视角、肩背、骨盆、后腿及尾根结构；将旧猫灰底改为白底浅灰褐纹，显示毛发覆盖的耳背。本轮侧面图仅辅助花纹衔接，原背面底图仍负责姿态，白猫母图仍负责身份。
- 比例图：编辑原人猫比例图的猫咪区域，保持原人物外观与站位作为目标，使用本轮侧面猫替换旧猫，沿用原比例与朝向。
- 三视图：以原三视图为布局底图，分别放入本轮正面、侧面和背面。完整输入包含三个独立结果和身份母图，避免重新自由设计整套角色。

## 背部补全的边界

原猫背面提供现成的身体结构。白猫隐藏毛色则沿肩背到臀部组织浅灰褐斑纹，并保留白毛间隔，与侧面可见纹路相接。耳背与尾根保持背视角所需的外观和位置。

背部毛色仍属于新增设计，单张坐姿母图不能证明隐藏纹路的真实形态。本轮不将其描述成从母图精确恢复的花纹，也不将原灰猫的整片深灰背色直接沿用。

## 检查结果与限制

五项成品与身份母图副本均通过 PNG 解码检查，实际尺寸和 SHA256 记录于 [preview-index.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/preview-index.json)。正式 Canon 四张图片仍与原 manifest 的 SHA256 一致。

已目视核对正面识别点、侧面单眼、背面耳背、四足站姿、尾根连接、白色胸腹与灰纹，以及人猫位置关系。独立图和三视图仍属于生成式插画，轮廓、斑纹边缘及局部笔触可能变化；本轮没有进行几何正投影校准或视频转身验证。

比例图与原比例图画布同为 941 × 1672。儿童外观和构图基本沿用原图，但读图比较发现局部像素有变化，不能声称“猫咪之外像素完全不变”。JSON 保留了只读比较结果；该局部数值不等同于整幅图的身份相似度评分。

所有生成使用内置 imagegen；文件选定后直接复制进项目。Python 只用于文件解码、尺寸、哈希和只读差异检查，没有重绘、裁剪输出、调色或合成图片。完整实际提示词和每次引用顺序见 [generation-prompts.md](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v4-template-edit/generation-prompts.md)。

本目录的 v4 是预览版本编号，与 assets/canon/v4 的正式生产包分开保存。身份母图、旧定稿素材和正式运行时引用保留原状。

