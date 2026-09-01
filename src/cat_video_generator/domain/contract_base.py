"""Pydantic业务契约的共同严格基类。"""

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """禁止静默接收导演临时发明的字段，避免契约再次失控。"""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        populate_by_name=True,
    )
