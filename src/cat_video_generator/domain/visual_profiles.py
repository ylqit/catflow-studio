"""系列人物、猫咪与画风的长期视觉档案。

本模块只描述跨项目稳定的视觉不变量，不保存服装、鞋帽、背包等场景外观。
这些档案属于业务资产元数据，不从 ``.env`` 读取，也不依赖数据库或 Ark。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class VisualProfileModel(BaseModel):
    """视觉档案的严格契约基类。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SeriesVisualProfile(VisualProfileModel):
    """固定人物与猫咪的系列级身份边界。"""

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    person_identity: str = Field(min_length=8, max_length=300)
    person_hair: str = Field(min_length=4, max_length=160)
    person_body: str = Field(min_length=4, max_length=160)
    cat_identity: str = Field(min_length=8, max_length=300)


class StyleProfile(VisualProfileModel):
    """可执行的系列画风定义。"""

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")
    positive_features: tuple[str, ...] = Field(min_length=3, max_length=10)
    excluded_features: tuple[str, ...] = Field(min_length=2, max_length=10)

    def prompt_positive(self) -> str:
        return "、".join(self.positive_features)

    def prompt_negative(self) -> str:
        return "、".join(self.excluded_features)


DEFAULT_SERIES_VISUAL_PROFILE = SeriesVisualProfile(
    profile_id="final-neutral-short-hair-child-gray-cat-v1",
    person_identity=(
        "同一个偏中性呈现的东亚儿童，保持批准人物正面图中的柔和椭圆脸、五官比例、"
        "肤色和自然儿童年龄感，不强化男性或女性特征"
    ),
    person_hair=(
        "保持深棕黑色、齐耳至下颌长度的顺直短波波头与轻薄刘海，不得无故变成长发、马尾或发髻"
    ),
    person_body="保持约五至七岁儿童的身高感、头身比例和纤细自然体型，可由剧情自然换装",
    cat_identity=(
        "同一只圆润灰白短毛猫，保持白色口鼻胸腹与四肢、灰色头顶和背部虎斑、"
        "灰白环纹、自然中等粗细且从后躯正常连接的尾巴、圆形琥珀棕眼睛及稳定体型"
    ),
)

DEFAULT_STYLE_PROFILE = StyleProfile(
    profile_id="final-healing-2d-watercolor-v1",
    positive_features=(
        "日系二维治愈生活插画",
        "细腻干净的手绘轮廓线",
        "柔和哑光的水彩式数字绘制",
        "清新低至中饱和自然色",
        "温和自然光与空气透视",
        "克制景深和轻微远景虚化",
    ),
    excluded_features=(
        "真人写实摄影",
        "CG或PBR三维材质",
        "塑料高光",
        "强烈3D体积塑形",
        "过度油亮平滑表面",
        "高反差商业动画灯光",
        "光滑商业动画渲染",
    ),
)
