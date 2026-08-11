"""Canonical, policy-driven asset admission boundary."""

from services.assets.contracts import (
    AssetBinding,
    AssetGatewayError,
    AssetKind,
    AssetPolicy,
)
from services.assets.gateway import AssetGateway
from services.assets.compatibility import (
    ImageAssetBackfillResult,
    backfill_legacy_image_assets,
    canonicalize_legacy_image_asset,
    dangling_canonical_image_count,
)
from services.assets.policies import DEFAULT_ASSET_POLICY_REGISTRY, AssetPolicyRegistry

__all__ = [
    "AssetBinding",
    "AssetGateway",
    "AssetGatewayError",
    "AssetKind",
    "AssetPolicy",
    "ImageAssetBackfillResult",
    "backfill_legacy_image_assets",
    "canonicalize_legacy_image_asset",
    "dangling_canonical_image_count",
    "AssetPolicyRegistry",
    "DEFAULT_ASSET_POLICY_REGISTRY",
]
