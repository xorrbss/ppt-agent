import pytest

from services.database import validate_public_database_authority


def test_public_database_roles_are_process_separated_and_never_bypass_rls() -> None:
    request_role = {
        "role_name": "presenton_public_api_login",
        "is_superuser": False,
        "bypass_rls": False,
        "is_controller": False,
    }
    controller_role = {
        "role_name": "presenton_public_controller_login",
        "is_superuser": False,
        "bypass_rls": False,
        "is_controller": True,
    }

    validate_public_database_authority(request_role, controller=False)
    validate_public_database_authority(controller_role, controller=True)

    with pytest.raises(RuntimeError, match="public_api_database_role_must_not_be_controller"):
        validate_public_database_authority(controller_role, controller=False)
    with pytest.raises(RuntimeError, match="public_controller_database_role_required"):
        validate_public_database_authority(request_role, controller=True)
    with pytest.raises(RuntimeError, match="public_database_role_bypasses_rls"):
        validate_public_database_authority(
            {**request_role, "is_superuser": True},
            controller=False,
        )
    with pytest.raises(RuntimeError, match="public_database_role_bypasses_rls"):
        validate_public_database_authority(
            {**controller_role, "bypass_rls": True},
            controller=True,
        )
