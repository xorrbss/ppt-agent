"""Public Template V2 canonical compiler boundary."""

from .compiler import (
    TemplateCompilerError,
    compile_template_render_plan,
    materialize_render_plan,
)
from .decoder import TemplateAstDecodeError, decode_template_ast
from .models import (
    AST_SCHEMA_VERSION,
    RENDER_PLAN_SCHEMA_VERSION,
    CanonicalTemplateAst,
    RepeatNode,
    TemplateRenderPlan,
)
from .schema_projection import project_content_schema

__all__ = [
    "AST_SCHEMA_VERSION",
    "RENDER_PLAN_SCHEMA_VERSION",
    "CanonicalTemplateAst",
    "RepeatNode",
    "TemplateAstDecodeError",
    "TemplateCompilerError",
    "TemplateRenderPlan",
    "compile_template_render_plan",
    "decode_template_ast",
    "materialize_render_plan",
    "project_content_schema",
]
