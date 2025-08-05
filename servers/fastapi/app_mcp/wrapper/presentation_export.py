from typing import Literal, Dict, Any
from utils.export_utils import export_presentation


# Standalone function for workflow orchestrator  
async def export_presentation_and_get_path(
    presentation_id: str,
    title: str,
    export_as: Literal["pptx", "pdf"] = "pptx"
) -> Dict[str, Any]:
    """
    Export the presentation and return the export path and edit path.
    """
    presentation_and_path = await export_presentation(
        presentation_id, title, export_as
    )
    # model_dump() is assumed to return a dict with the export path and related info
    data = presentation_and_path.model_dump()
    print("Exported presentation data:", data)
    # Map export_path to path if needed
    return {
        **data,
        "edit_path": f"/presentation?id={presentation_id}",
        "export_path": data["path"],
    }
