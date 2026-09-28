import os
import uuid


from api.v1.auth.assets import is_app_data_path_authorized
from utils.owner_context import (
    reset_current_owner_id,
    reset_current_owner_is_admin,
    set_current_owner_id,
    set_current_owner_is_admin,
)
from utils.asset_directory_utils import resolve_app_path_to_filesystem


def test_browser_asset_paths_are_owner_scoped_and_traversal_safe():
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()

    assert is_app_data_path_authorized(
        f"/app_data/images/users/{owner_id}/slide.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert not is_app_data_path_authorized(
        f"/app_data/images/users/{other_id}/slide.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/%252e%252e/userConfig.json",
        user_id=owner_id,
        is_admin=True,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/%2525252525252525252e%2525252525252525252e"
        "/userConfig.json",
        user_id=owner_id,
        is_admin=True,
    )
    assert not is_app_data_path_authorized(
        "/app_data/images/legacy.png",
        user_id=owner_id,
        is_admin=False,
    )
    assert is_app_data_path_authorized(
        "/app_data/images/legacy.png",
        user_id=owner_id,
        is_admin=True,
    )
    assert is_app_data_path_authorized(
        f"/app_data/workspace-artifacts/users/{owner_id}/operation/artifact.pptx",
        user_id=owner_id,
        is_admin=False,
    )
    assert not is_app_data_path_authorized(
        f"/app_data/workspace-artifacts/users/{other_id}/operation/artifact.pptx",
        user_id=owner_id,
        is_admin=True,
    )
    assert not is_app_data_path_authorized(
        "/app_data/workspace-artifacts/operation/artifact.pptx",
        user_id=owner_id,
        is_admin=False,
    )


def test_server_side_file_resolution_rejects_other_users_and_symlink_escape(
    monkeypatch, tmp_path
):
    app_data = tmp_path / "app_data"
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    own_file = app_data / "uploads" / "users" / str(owner_id) / "own.pptx"
    other_file = app_data / "uploads" / "users" / str(other_id) / "secret.pptx"
    own_file.parent.mkdir(parents=True)
    other_file.parent.mkdir(parents=True)
    own_file.write_bytes(b"own")
    other_file.write_bytes(b"secret")
    monkeypatch.setenv("APP_DATA_DIRECTORY", str(app_data))

    owner_token = set_current_owner_id(owner_id)
    admin_token = set_current_owner_is_admin(False)
    try:
        assert resolve_app_path_to_filesystem(
            f"/app_data/uploads/users/{owner_id}/own.pptx"
        ) == os.path.realpath(own_file)
        assert (
            resolve_app_path_to_filesystem(
                f"/app_data/uploads/users/{other_id}/secret.pptx"
            )
            is None
        )
        assert (
            resolve_app_path_to_filesystem(
                "/app_data/uploads/users/"
                f"{owner_id}/../../{other_id}/secret.pptx"
            )
            is None
        )
    finally:
        reset_current_owner_is_admin(admin_token)
        reset_current_owner_id(owner_token)

