"""Deterministic provider doubles imported only by automated tests."""

from .gateway import FakeArkGateway
from .storage import FakeProviderAssetStore

__all__ = ["FakeArkGateway", "FakeProviderAssetStore"]
