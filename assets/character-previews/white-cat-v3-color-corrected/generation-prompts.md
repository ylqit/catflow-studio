# v3 白猫校色：完整实际提示词

使用内置 imagegen。共八次调用：五项交付，人猫比例图进行了额外校色尝试。当前采用的比例图为最后的白毛校色结果。各提示词中的不变要求是编辑约束，不代表像素完全相同。

本轮 v3 图片是用户明确点名的校色编辑目标；毛色依据为 assets/canon/v4/白猫.png，未继续采用暖色适配稿作为毛色标准。

注意：最后一次调用输入路径中的 pair-scale.png 当时保存的是第二次校色结果，现已归档至 drafts/pair-scale-second-pass.png；当前 pair-scale.png 是最终交付。

## 1. 正面站姿

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

输入顺序：

1. [front-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/front-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)

```text
Use case: precise-object-edit.
IMAGE 1 is the FRONT STANDING CAT EDIT TARGET. IMAGE 2 is the ORIGINAL WHITE CAT and the sole COLOR AUTHORITY.

Perform localized COLOR AND TONE correction on the cat in IMAGE 1. Preserve its exact face geometry, eye size and position, head/body proportions, fur silhouette, standing pose, paws, tail occlusion, stripe positions/shapes, framing, background and contact-shadow placement. Do not reconstruct the pose or copy the seated pose of image 2.

Restore the original white-cat color character:
- White fur must read as clean nearly NEUTRAL WHITE, not cream, ivory, beige or peach. Large lit areas on muzzle, chest, belly and paws should approach image 2's white (sampled original chest #FFFFFD and muzzle #FFFFFF). Retain delicate light-gray modeling and individual fur layers rather than clipping everything to flat white. Remove the blanket yellow-red wash from the cat only.
- Gray markings retain their existing pattern and boundaries but lose the excessive taupe/brown saturation. Match the original's low-chroma gray with a faint natural warm undertone, not blue/cyan gray. Reduce the heavy opaque block appearance with subtle pale fur breaks within the same existing markings. Preserve darker fine accents; do not simply wash out all markings.
- Eyes keep the same geometry and highlights, but read as the original's near-black/deep charcoal eyes with only restrained brown tint. Keep the nose and inner ears naturally pink and their existing shapes.
- Retain the target's finished soft digital illustration and delicate contours. The source supplies color and lightness, not a demand to restore its dense pencil hatching.

Keep IMAGE 1's neutral warm-gray background unchanged; this is not a global brightness, white-balance or saturation filter. Preserve the complete single standing cat, no new objects, text or watermark. Return a square PNG at the same framing.
```

## 2. 侧面站姿

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-4a7a1980-a6ef-48ff-9500-5c7b3951a862.png)

输入顺序：

1. [side-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/side-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

```text
Use case: precise-object-edit.
IMAGE 1 is the EDIT TARGET. IMAGE 2 is the ORIGINAL WHITE CAT and sole natural fur-color authority. IMAGE 3 is this round's color-corrected front view, used only to keep the finished digital-illustration palette consistent.

Make localized FUR COLOR AND TONE corrections to image 1, preserving its geometry, exact pose, head/body proportions, limb positions, tail shape, fur silhouette, stripe locations and boundaries, camera, layout and warm-gray background. Do not change the character design or adopt another reference's pose.

Replace the cream/ivory/peach wash on white fur with clean nearly neutral WHITE. Target the original source's near-white muzzle/chest (#FFFFFF/#FFFFFD), with gentle light-gray shading preserving fur layers. The corrected-front white example has lit chest around #FDFCFA. Keep volume; no flat clipping, excessive bloom, cyan or blue cast.
Reduce the excessive red/yellow/brown chroma of the gray markings and contour shading. Match image 2's low-chroma gray with a faint natural warm undertone, leaving existing stripe locations intact. Use subtle pale fur breaks within the same markings to reduce solid-block heaviness. Preserve darker fine accents. Do not erase stripes or invent new markings, and do not restore dense pencil hatching.
Keep pink skin accents naturally pink. Preserve finished matte digital illustration and delicate fur contours. The background must not undergo a global desaturation, cooling or exposure shift. Complete cat, no new objects, text, watermark, labels or collage.

This target is the exact LEFT PROFILE STANDING cat with a LOW rear-extending tail. Keep its exact torso length, four legs, rear hocks, single visible eye, low tail and current markings. The eye keeps its shape/highlights but reads deep charcoal with restrained brown. Make the large white chest and belly neutral white, and side/tail gray markings less brown. Preserve original landscape 3:2 framing; PNG.
```

## 3. 背面站姿

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-29a31933-723d-406a-a01a-0a8bd5356957.png)

输入顺序：

1. [rear-standing.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/rear-standing.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

```text
Use case: precise-object-edit.
IMAGE 1 is the EDIT TARGET. IMAGE 2 is the ORIGINAL WHITE CAT and sole natural fur-color authority. IMAGE 3 is this round's color-corrected front view, used only to keep the finished digital-illustration palette consistent.

Make localized FUR COLOR AND TONE corrections to image 1, preserving its geometry, exact pose, head/body proportions, limb positions, tail shape, fur silhouette, stripe locations and boundaries, camera, layout and warm-gray background. Do not change the character design or adopt another reference's pose.

Replace the cream/ivory/peach wash on white fur with clean nearly neutral WHITE. Target the original source's near-white muzzle/chest (#FFFFFF/#FFFFFD), with gentle light-gray shading preserving fur layers. The corrected-front white example has lit chest around #FDFCFA. Keep volume; no flat clipping, excessive bloom, cyan or blue cast.
Reduce the excessive red/yellow/brown chroma of the gray markings and contour shading. Match image 2's low-chroma gray with a faint natural warm undertone, leaving existing stripe locations intact. Use subtle pale fur breaks within the same markings to reduce solid-block heaviness. Preserve darker fine accents. Do not erase stripes or invent new markings, and do not restore dense pencil hatching.
Keep pink skin accents naturally pink. Preserve finished matte digital illustration and delicate fur contours. The background must not undergo a global desaturation, cooling or exposure shift. Complete cat, no new objects, text, watermark, labels or collage.

This target is the exact REAR STANDING cat with a LOW right-curving tail. Keep its exact shoulders, hindquarters, legs, tail root and all dorsal markings. Show the same furry backs of ears, no face or pink inner-ear bowls. Make nape, white back passages and lower legs clean neutral white; reduce beige/brown in dorsal and tail stripes without redesigning their pattern. Preserve square framing; PNG.
```

## 4. 人猫比例初次校色

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-a232d258-f17d-4820-b65e-ad38d8829962.png)

输入顺序：

1. [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/pair-scale.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

```text
Use case: precise-object-edit.
IMAGE 1 = EDIT TARGET, the existing HUMAN-CAT SCALE picture.
IMAGE 2 = original WHITE CAT, the true fur-color authority.
IMAGE 3 = color-corrected front cat, supporting finished-illustration palette reference only.

Color-correct ONLY THE CAT at the lower right of image 1. Preserve the entire child's face, hair, skin, cream shirt, navy shorts, sandals, scale, pose and position. Preserve background color, lighting, floor and canvas. Do not apply any whole-image cooling, exposure or desaturation.

Keep the cat exactly where it is, at the same size and front-standing pose, with its same face geometry, proportions, paws, stripe shapes/positions and contact-shadow position. Do not replace it with the seated source or another pose.

Remove the cat's cream/peach/yellow fur wash. White chest, muzzle, belly and paws should read near-neutral WHITE, like original image 2, with only delicate light-gray modeling. Use the corrected image 3 as an example of bright but still detailed white fur. Retain full fur detail, no bloom or blank blown-out areas.
Reduce red-yellow-brown saturation of the gray stripes and their shading to a low-chroma gray with a restrained natural warm undertone. Match original image 2's gray character, not pure cyan/blue gray or sepia. Reduce heavy fill appearance with delicate pale fur breaks within the existing markings, not new stripes or pencil hatching.
The cat's eyes retain shape/highlights and become deep charcoal with only restrained brown. Its pink nose/ears remain natural pink. Preserve the finished matte soft illustration style.
One child and one cat exactly as composed in image 1. No new text, rulers, labels, objects or watermark. Output same portrait 9:16 PNG framing.
```

## 5. 人猫比例第二次校色

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-f2416d77-bf20-42e3-923c-d138ded84450.png)

输入顺序：

1. [exec-a232d258-f17d-4820-b65e-ad38d8829962.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-a232d258-f17d-4820-b65e-ad38d8829962.png)
2. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)
3. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)

```text
Precise localized color correction, second pass.
IMAGE 1 is the edit target human-cat image. IMAGE 2 is the correctly neutral-white front cat reference. IMAGE 3 is the original white-cat color source.

The first correction barely changed the small cat. The cat STILL HAS CREAM/PEACH WHITE FUR and must be visibly corrected. On the 941x1672 target the cat occupies roughly x=580..790, y=1080..1490. Edit ONLY this cat, preserving all positions, anatomy, face, stripes and size. Do not alter the child, background or floor.

Technical color target: the white CHEST near x=674,y=1321 currently samples approximately RGB(250,242,238), #FAF2EE. Change that white fur to approximately RGB(253,252,250), #FDFCFA, like reference 2. Across ALL cat white fur, raise blue and green relative to red to remove the peach/yellow cast while preserving shading and fine fur detail. Lit white muzzle, chest and paws should be neutral off-white; shadowed white fur should be low-chroma gray rather than beige. This must be a visible change, not a tiny brightness adjustment. Do NOT brighten the entire picture.
Also reduce brown/yellow saturation in the cat's existing gray markings, keeping their same positions and darkness structure. Keep nose/inner ears naturally pink and eyes deep dark. No blue or cyan tint.

Keep the original child completely unchanged in appearance, color, clothing, position and lighting. Keep the image canvas and its original warm-gray backdrop unchanged. Do not move, resize, redraw or restyle either subject. Only correct the CAT'S FUR COLORS to match the neutral white in reference 2 and original source 3. Same portrait PNG.
```

## 6. 三视图

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-79aedd45-0d70-446e-899f-402fe6e38bff.png)

输入顺序：

1. [three-view-board.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/three-view-board.png)
2. [白猫.png](D:/soft/code/OpenGit/catflow-studio/assets/canon/v4/白猫.png)
3. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)
4. [exec-4a7a1980-a6ef-48ff-9500-5c7b3951a862.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-4a7a1980-a6ef-48ff-9500-5c7b3951a862.png)
5. [exec-29a31933-723d-406a-a01a-0a8bd5356957.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-29a31933-723d-406a-a01a-0a8bd5356957.png)

```text
Use case: precise-object-edit.
IMAGE 1 = the existing THREE-VIEW SHEET, EDIT TARGET.
IMAGE 2 = original white cat, fur-color authority.
IMAGE 3 = corrected FRONT view palette.
IMAGE 4 = corrected SIDE view palette.
IMAGE 5 = corrected REAR view palette.

Perform COLOR AND TONE correction on the three cats in IMAGE 1, using images 3–5 as corresponding finished-color examples. Preserve the original sheet's composition, camera, geometry of each cat, spacing, shared ground line, ear heights, stripe patterns, tail gestures and pale warm-gray background. Do not regenerate new views or change the existing arrangement.

In ALL THREE CATS remove the cream/beige/peach cast on white fur. White muzzle/chest/abdomen/legs/nape should be clean nearly neutral white, matching original image 2 (near #FFFFFD lit white), with the delicate low-chroma gray shading visible in corrected references 3–5. Keep fur layers and volume, not blank clipped shapes.
Remove excessive brown/yellow saturation from the existing gray stripe areas and contours; use muted low-chroma gray with only a slight natural warm undertone, not sepia or blue/cyan. Preserve the exact stripe placement and outline; retain delicate white breaks and texture within markings rather than a heavy solid brown-gray fill. No return to dense pencil hatching.
Front and side eyes stay deep charcoal with restrained brown and their existing highlights; skin accents remain pink. Rear ears show furry backs and no face.
Keep the left FRONT STANDING cat, middle LEFT PROFILE STANDING cat with its low tail, and right REAR STANDING cat with its low curving tail. Do not change any pose or proportions. Do not globally recolor the background or raise whole-image exposure.
Return one high-resolution wide 3:1 PNG, same composition, complete ears/paws/tails, no labels, rulers, text or new objects.
```

## 7. 人猫比例插入尝试（未采用）

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-88fd7475-8af4-44e6-b2d6-038614c9ed0a.png)

输入顺序：

1. [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3/pair-scale.png)
2. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

```text
Use case: compositing.
IMAGE 1 is the original human-cat scale illustration to edit.
IMAGE 2 is the exact color-corrected FRONT STANDING cat artwork to place into image 1.

Replace ONLY the small cat in image 1 with the cat artwork supplied in image 2. Treat this as placing a faithful cutout of image 2, not repainting it to suit the warm scene. Copy image 2's actual NEUTRAL WHITE fur colors and gray stripe colors without applying warm light, beige tint, peach wash, color grading or scene-color blending. The white chest should remain around #FDFCFA as supplied. Fine light-gray fur detail must remain visible.

Place this same front-standing cat at the existing cat's exact size and location: on the 941x1672 canvas, ear tips roughly y=1086, paw bottoms roughly y=1488, horizontal center roughly x=677. Scale uniformly to fit those bounds. Keep its source face, head/body proportions, eyes, stripes, paws and standing pose. Do not stretch it, enlarge it, sit it down or add a visible new tail. Its floor contact shadow can blend into the existing floor; its FUR COLOR MUST NOT be warmed.

Preserve all other parts of image 1: the child's exact face/hair/clothes/body/skin colors/pose/location, the warm-gray backdrop, floor and canvas. Do not globally cool or brighten the image. One child and one cat, no new elements or text. Output same portrait PNG.
```

## 8. 人猫比例最终白毛校色

工具原始输出：[PNG](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-a45d1190-fa77-4663-8e4f-c159a413b8a2.png)

输入顺序：

1. [pair-scale.png](D:/soft/code/OpenGit/catflow-studio/assets/character-previews/white-cat-v3-color-corrected/pair-scale.png)
2. [exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png](C:/Users/wwwab/.codex/generated_images/01a08aa5-2e14-7ff3-a2aa-5bb0d5e9d5aa/exec-e52619fd-03a7-4dc2-bfc8-bac86a796990.png)

```text
只修改图1右下角猫咪的毛色。把猫身上所有米白、奶油白、浅桃色的白毛改为干净的纯白，亮部接近 RGB(255,255,255)，阴影使用中性浅灰。猫咪不能再带米黄或粉褐色滤镜。灰纹改为低饱和灰色，保持纹路位置；粉鼻和粉耳保留。图2是正确的白毛颜色范例，请采用它的白色。保持图1猫的大小、位置、正面站姿、脸型和毛发细节。儿童、衣服、背景和地面维持原样。只给猫去除黄红偏色，不要给整幅图调色。输出原尺寸竖版图片。
```

