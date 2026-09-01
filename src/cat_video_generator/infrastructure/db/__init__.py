"""PostgreSQL持久化实现。"""

from .models import SCHEMA_NAME, Base

__all__ = ["Base", "SCHEMA_NAME"]
