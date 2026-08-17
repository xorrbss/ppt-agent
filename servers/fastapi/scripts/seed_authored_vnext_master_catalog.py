"""Seed the owner-scoped local authored master catalog without network access."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from models.authored_vnext.master.base import stable_slug
from models.sql.user import User
from services.authored_vnext.integration.sql_repository import (
    AuthoredVNextSqlRepository,
)
from services.authored_vnext.master.assets import OwnerMasterAssetStore
from services.authored_vnext.master.catalog_seed import (
    DEFAULT_IBIZ_LOGO_SVG,
    seed_catalog,
)
from services.authored_vnext.master.file_repository import (
    FileAuthoredVNextMasterRepository,
)
from services.authored_vnext.master.service import AuthoredVNextMasterService
from utils.db_utils import get_database_url_and_connect_args, to_sync_sqlalchemy_url


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--owner-id",
        default=os.getenv("AUTHORED_VNEXT_SEED_OWNER_ID"),
        help="Owner UUID. In SQL mode it may be omitted only when one active user exists.",
    )
    parser.add_argument(
        "--repository-mode",
        choices=("sql", "file"),
        default=os.getenv("AUTHORED_VNEXT_REPOSITORY_MODE", "sql").lower(),
    )
    parser.add_argument(
        "--logo-svg",
        type=Path,
        default=DEFAULT_IBIZ_LOGO_SVG,
    )
    return parser.parse_args()


def _application_engine():
    url, connect_args = get_database_url_and_connect_args()
    sync_url = to_sync_sqlalchemy_url(url)
    if "sqlite" in sync_url:
        connect_args = {"check_same_thread": False, "timeout": 30.0}
    else:
        connect_args = {}
    return create_engine(sync_url, connect_args=connect_args)


def _single_active_owner_id() -> str:
    engine = _application_engine()
    try:
        with Session(engine) as session:
            owner_ids = list(
                session.scalars(
                    select(User.id).where(
                        User.is_active.is_(True),
                        User.deletion_state == "active",
                    )
                )
            )
    finally:
        engine.dispose()
    if len(owner_ids) != 1:
        raise RuntimeError(
            "seed_owner_id_required_when_active_user_count_is_not_one"
        )
    return str(owner_ids[0])


def main() -> int:
    args = _arguments()
    app_data = Path(os.getenv("APP_DATA_DIRECTORY", "app_data")).resolve()
    tenant_id = os.getenv("AUTHORED_VNEXT_TENANT_ID", "default")

    if args.repository_mode == "sql":
        owner_id = args.owner_id or _single_active_owner_id()
        canonical_owner = str(uuid.UUID(owner_id))
        repository = AuthoredVNextSqlRepository.from_application_database(
            owner_scope=canonical_owner,
            tenant_id=tenant_id,
            owner_id=uuid.UUID(canonical_owner),
        )
    else:
        owner_id = args.owner_id or "local"
        canonical_owner = owner_id
        repository = FileAuthoredVNextMasterRepository(
            app_data
            / "authored_vnext"
            / "master"
            / stable_slug(tenant_id)
            / stable_slug(canonical_owner)
        )

    asset_store = OwnerMasterAssetStore(
        app_data
        / "authored_vnext"
        / "master-assets"
        / "users"
        / canonical_owner
    )
    results = seed_catalog(
        AuthoredVNextMasterService(repository),
        asset_store,
        ibiz_logo_svg=args.logo_svg.resolve(),
    )
    print(
        json.dumps(
            {
                "ownerId": canonical_owner,
                "repositoryMode": args.repository_mode,
                "logoSvg": str(args.logo_svg.resolve()),
                "masters": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
