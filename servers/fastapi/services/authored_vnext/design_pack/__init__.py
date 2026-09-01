"""Source-native Design Pack ingestion and dynamic registry."""

from .compiler import (
    CompiledDesignSource,
    DesignPackCompileError,
    compile_authored_design_source,
    compile_design_source,
    compile_pptx_design_source,
)
from .ingestion import DesignPackIngestionError, ingest_design_pack
from .models import DesignPackManifest, DesignPackStructure
from .registry import (
    DesignPackRegistry,
    DesignPackRegistryError,
    RegisteredDesignPack,
)

__all__ = [
    "CompiledDesignSource",
    "DesignPackCompileError",
    "DesignPackIngestionError",
    "DesignPackManifest",
    "DesignPackRegistry",
    "DesignPackRegistryError",
    "DesignPackStructure",
    "RegisteredDesignPack",
    "compile_authored_design_source",
    "compile_design_source",
    "compile_pptx_design_source",
    "ingest_design_pack",
]
