"""Static allow-listed workspace error definitions."""

from services.workspace_asset_error_definitions import ASSET_ERROR_DEFINITIONS
from services.workspace_operation_error_definitions import OPERATION_ERROR_DEFINITIONS
from services.workspace_core_error_definitions import CORE_ERROR_DEFINITIONS
from services.workspace_additional_error_definitions import ADDITIONAL_ERROR_DEFINITIONS
from services.workspace_error_types import WorkspaceErrorDefinition


# Keep this registry metadata-only. Messages never include payloads or storage paths.
WORKSPACE_ERROR_REGISTRY: dict[str, WorkspaceErrorDefinition] = {
    item.code: item
    for item in (
        *CORE_ERROR_DEFINITIONS,
        *ASSET_ERROR_DEFINITIONS,
        *ADDITIONAL_ERROR_DEFINITIONS,
    )
}
WORKSPACE_ERROR_REGISTRY.update(OPERATION_ERROR_DEFINITIONS)
