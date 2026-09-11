# 白猫 v3：站姿三视图与人猫比例预览

状态：已被用户判定不合格，停止作为后续生成或定稿依据。文件保留用于审计。用户指定的最终适配稿仍是唯一猫咪身份依据；后续应以现有定稿猫图约束姿态与结构，避免继续从本目录的派生图扩展。以下为被否定版本的历史记录，尚未接入正式 Canon 或进行视频验证。

## 身份依据

唯一猫咪身份依据为用户指定的[最终适配稿](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-cf668a2b-9f58-4836-a3f7-6757d515469d.png)。[identity-source.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/identity-source.png) 是该图直接复制，SHA256 完全一致：

`4eb638ad80effb9410abab5045c84e69abf5bda8a2080b8277183c888bdbefd1`

母图是坐姿。本次五项成品均使用四足站姿；母图只作为身份对照。没有把 v1/v2 猫图输入本轮生成。

## 重新分析后的设计约束

1. **脸部识别点**：宽圆脸颊、很短的小口鼻、大而深色的圆眼、较小粉鼻、耳朵比例，以及额头分叉灰纹与双侧颊纹共同决定身份。只写“可爱白猫”不足以保留角色。本轮单独以母图修订了站姿正面的鼻口和额头花纹。
2. **毛色**：母图画面中的白毛是偏暖的象牙白，灰纹带灰褐色。胸毛亮部局部约为 #FCF6EF，尾纹局部约为 #B0A196。这是局部像素取样，包含光照和笔触影响，不是全身统一填色值。侧背纹继续使用低饱和灰褐色，胸腹、口鼻及爪端保留大面积白毛。
3. **身形与毛量**：坐姿的胸腹与臀部毛量不能简单解释成短腿胖桶身。站姿需要看见独立承重的前肢和后肢，同时保留胸前分层长毛、腹部毛边、圆润臀部及圆爪。侧面使用朝左的侧视角，只露出一只眼，便于判断躯干和腿部结构。
4. **尾巴**：母图卷在身前的蓬松尾巴，在站姿中改为从臀部低位延伸、自然下垂后轻弯，保留灰褐分段与渐细尾端。尾巴展开后的长度属于姿态重建，不能从坐姿单图精确量得。
5. **笔触与明暗**：保留柔和数字插画、细暖灰轮廓和轻纸感；在胸毛交叠、腹部与四肢处用浅阴影维持体积。猫与儿童沿用柔和漫射光和浅暖灰背景。

## 看不见的背部为什么补全、如何补全

转身与背向镜头需要一个可复用的背部设计，否则每次生成都可能出现新的花纹。本次完成了后脑、耳背、肩背、臀部到尾根的连接：

- 后脑以白毛为主，补入局部柔和灰褐斑，与头顶和耳根已有颜色衔接。
- 两侧可见的斜向灰纹延续到肩背，以有白毛间隔的斑带和毛束边缘组织；避免整片深灰背鞍、机械圆点和等距装饰环。
- 臀部斑纹向尾根衔接，尾根与骨盆位置一致；白毛继续保留在颈部、侧腹和腿部。
- 背视角修正为毛发覆盖的耳背，消除初稿背面大面积露出粉色耳内的问题。

**这部分是符合当前角色的补全设计，不是从原图恢复出的真实隐藏花纹。** 新背纹与侧面之间仍需以设计参考图持续约束；本轮没有建立可保证每个角度完全一致的三维贴图。

## 五项当前交付

| 文件 | 实际像素尺寸 | 内容 |
|---|---|---|
| [front-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/front-standing.png) | 1254 × 1254 | 正面四足站姿；正面身份细节已对照母图修订 |
| [side-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/side-standing.png) | 1536 × 1024 | 朝左侧面站姿；检查胸腹、后腿和尾巴 |
| [rear-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/rear-standing.png) | 1254 × 1254 | 背面站姿；补全背纹与耳背 |
| [three-view-board.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/three-view-board.png) | 2172 × 724 | 左正面、中侧面、右背面；统一展示站姿 |
| [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/pair-scale.png) | 941 × 1672 | 既有儿童与站姿猫同框；缩小猫并修订落脚位置 |

尺寸为实际输出，未统一放大成 2048 × 2048。独立图优先用于单一角度核对，三视图用于综合对照。

人猫比例以[既有儿童](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/episode-child.png)为人物依据。成品中猫从耳尖至地面的高度约为儿童全高的 30%，耳尖接近儿童膝部，两者落脚位置大致对齐。该比例是画面目测关系，不是精确的厘米标尺；原始生成目标与实际输出存在少量差异。

## 生成与检查记录

先生成共同站姿三视图，再分别导出三个视角；随后针对正面身份和背面耳背修订，并用这三个独立成品重新生成三视图板。人猫图参考修订后的正面站姿，另做大小与落脚位置两次调整。

全部图片使用内置 imagegen。选定输出直接复制到本目录。完整实际提示词、引用图顺序和输出来源见 [generation-prompts.md](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/generation-prompts.md)；尺寸、SHA256 与解码检查见 [preview-index.json](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/preview-index.json)。

五项成品与母图副本均通过 PNG 解码检查。视觉检查涵盖站姿、侧面单眼、背面耳背、四肢遮挡、尾根、胸腹毛量、暖灰褐纹和人猫相对大小。三视图是生成式插画参考，独立图与图板之间仍可能有局部线条、斑纹和轮廓差异，不能称为无损旋转或经过几何校准的建模图。

drafts/ 保存本轮部分被修订的中间稿，不属于当前交付。正式 assets/canon/v4、运行时引用与现有视频没有因本轮预览而替换。
