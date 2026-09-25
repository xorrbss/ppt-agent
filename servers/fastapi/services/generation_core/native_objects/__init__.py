"""Format-neutral NativeObjects sidecar (tables/charts) shared by the drivers."""
from services.generation_core.native_objects.model import (
    NATIVE_OBJECTS_SCHEMA,
    SLIDE_CONTENT_KEY,
    NativeAnchor,
    NativeObject,
    NativeObjects,
    NativeObjectsError,
    slide_native_objects,
)

__all__ = [
    "NATIVE_OBJECTS_SCHEMA",
    "SLIDE_CONTENT_KEY",
    "NativeAnchor",
    "NativeObject",
    "NativeObjects",
    "NativeObjectsError",
    "slide_native_objects",
]
