from typing import Dict, Set, Optional, Any
from dataclasses import dataclass

@dataclass
class StateContext:
    """Context data that travels with the state machine"""
    presentation_id: Optional[str] = None
    title: Optional[str] = None
    outlines: Optional[list] = None
    layout: Optional[str] = None
    file_paths: Optional[list] = None
    export_format: Optional[str] = None
    export_path: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}