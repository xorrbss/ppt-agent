"""Immutable, invocation-local physical units for native serialization.

The ContextVar is task/thread local, never a mutable process-wide scale. The
public converter scopes and resets it, including failures and nested calls.
Nonlegacy calls are deliberately serial until child propagation is supported.
"""
from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from inspect import signature

#: ``(text, font_size, font_weight, font_family) -> advance`` in canvas units,
#: or ``None`` when that family, weight or any glyph cannot be measured exactly.
TextAdvance = Callable[[str, float, str, str], "float | None"]


@dataclass(frozen=True)
class ConversionContext:
    width_units: int = 1280
    height_units: int = 720
    width_emu: int = 12192000
    height_emu: int = 6858000
    legacy: bool = True
    # Exact font metrics supplied by the caller. Without it, single-line text
    # frames are sized from the per-character estimate alone.
    text_advance: TextAdvance | None = field(default=None, compare=False)

    def __post_init__(self):
        values = (self.width_units, self.height_units, self.width_emu, self.height_emu)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("svg_conversion_context_invalid")
        if self.text_advance is not None and not callable(self.text_advance):
            raise ValueError("svg_conversion_context_invalid")
        aspect_error = abs(
            self.width_emu * self.height_units - self.height_emu * self.width_units
        )
        if aspect_error and (self.legacy or aspect_error * 2 > self.width_emu):
            raise ValueError("svg_conversion_context_nonuniform")
        if self.legacy and self.width_emu != self.width_units * 9525:
            raise ValueError("svg_conversion_context_legacy_scale")

    @property
    def emu_per_unit(self) -> float:
        return self.width_emu / self.width_units

    @property
    def hundredths_pt_per_unit(self) -> float:
        return self.emu_per_unit / 127


_ACTIVE_CONTEXT: ContextVar[ConversionContext] = ContextVar(
    "slide_master_conversion_context", default=ConversionContext()
)


def current_conversion_context() -> ConversionContext:
    return _ACTIVE_CONTEXT.get()


def scoped_conversion_context(function):
    call_signature = signature(function)

    @wraps(function)
    def call(*args, **kwargs):
        bound = call_signature.bind(*args, **kwargs)
        context = bound.arguments.get("conversion_context") or ConversionContext()
        if not isinstance(context, ConversionContext):
            raise ValueError("svg_conversion_context_invalid")
        if not context.legacy:
            if bound.arguments.get("workers") not in (None, 1):
                raise ValueError("svg_conversion_context_parallel_unsupported")
            bound.arguments["workers"] = 1
            if bound.arguments.get("pptx_structure", "structured") != "flat":
                raise ValueError("svg_conversion_context_structure_unsupported")
        token = _ACTIVE_CONTEXT.set(context)
        try:
            return function(*bound.args, **bound.kwargs)
        finally:
            _ACTIVE_CONTEXT.reset(token)

    return call
