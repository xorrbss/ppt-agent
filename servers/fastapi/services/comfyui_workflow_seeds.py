"""Seed randomization for ComfyUI API-format workflow graphs.

This module owns everything Presenton knows about locating and rewriting the
seed widgets of a ComfyUI workflow: what counts as a seed key, a seed value or a
node link, how the node index is built, and how a fresh seed is drawn. It was
split out of ``services/image_generation_service.py`` because the graph walk is
pure dictionary manipulation with no provider, network or settings dependency,
and it dominated the length of an otherwise transport-oriented service class.

``ImageGenerationService`` keeps thin delegating methods so the seed factory can
still be pinned per instance in tests; that is why
:func:`inject_random_seeds_into_workflow` takes ``seed_factory`` rather than
calling :func:`generate_comfyui_seed` directly.
"""

from __future__ import annotations

from collections.abc import Callable
import secrets


COMFYUI_MAX_SEED = 0xFFFFFFFFFFFFFFFF
COMFYUI_SEED_SOURCE_VALUE_KEYS = {"value", "int", "integer", "number"}


def generate_comfyui_seed() -> int:
    return secrets.randbelow(COMFYUI_MAX_SEED + 1)


def normalize_comfyui_key(key: object) -> str:
    return str(key or "").strip().replace("-", "_").replace(" ", "_").lower()


def is_comfyui_seed_key(key: object) -> bool:
    normalized_key = normalize_comfyui_key(key).replace("_", "")
    return normalized_key == "seed" or normalized_key.endswith("seed")


def is_comfyui_seed_value(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, str):
        raw_value = value.strip()
        return raw_value.isdigit() or (
            raw_value.startswith("-") and raw_value[1:].isdigit()
        )
    return False


def is_comfyui_link(value: object) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def build_comfyui_node_index(workflow: dict) -> dict[str, dict]:
    node_index: dict[str, dict] = {}
    visited_objects: set[int] = set()

    def walk(obj) -> None:
        object_id = id(obj)
        if object_id in visited_objects:
            return
        visited_objects.add(object_id)

        if isinstance(obj, dict):
            if isinstance(obj.get("inputs"), dict):
                node_id = obj.get("id")
                if node_id is not None:
                    node_index[str(node_id)] = obj

            for key, value in obj.items():
                if isinstance(value, dict) and isinstance(value.get("inputs"), dict):
                    node_index[str(key)] = value
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(workflow)
    return node_index


def inject_random_seeds_into_workflow(
    workflow: dict,
    *,
    seed_factory: Callable[[], int] = generate_comfyui_seed,
) -> int:
    """
    Randomize ComfyUI seed inputs before submitting the workflow.

    ComfyUI API-format workflows only include current widget values, not the
    UI's control_after_generate setting. Randomizing numeric seed-like inputs
    here gives each Presenton regeneration a new output for the same prompt.
    """
    node_index = build_comfyui_node_index(workflow)
    randomized_inputs: set[tuple[int, str]] = set()
    visited_objects: set[int] = set()
    seed_update_count = 0

    def randomize_input(inputs: dict, key: object) -> bool:
        nonlocal seed_update_count

        key_text = str(key)
        marker = (id(inputs), key_text)
        if marker in randomized_inputs:
            return False

        value = inputs.get(key)
        if not is_comfyui_seed_value(value):
            return False

        new_seed = seed_factory()
        inputs[key] = str(new_seed) if isinstance(value, str) else new_seed
        randomized_inputs.add(marker)
        seed_update_count += 1
        return True

    def randomize_linked_seed_source(link: list | tuple) -> None:
        source_node = node_index.get(str(link[0]))
        if not isinstance(source_node, dict):
            return

        inputs = source_node.get("inputs")
        if not isinstance(inputs, dict):
            return

        updated = False
        for source_key in list(inputs.keys()):
            if is_comfyui_seed_key(source_key):
                updated = randomize_input(inputs, source_key) or updated

        if updated:
            return

        source_candidates = [
            key
            for key, value in inputs.items()
            if normalize_comfyui_key(key) in COMFYUI_SEED_SOURCE_VALUE_KEYS
            and is_comfyui_seed_value(value)
        ]
        if len(source_candidates) == 1:
            randomize_input(inputs, source_candidates[0])

    def walk(obj) -> None:
        object_id = id(obj)
        if object_id in visited_objects:
            return
        visited_objects.add(object_id)

        if isinstance(obj, dict):
            inputs = obj.get("inputs")
            if isinstance(inputs, dict):
                for input_key, input_value in list(inputs.items()):
                    if not is_comfyui_seed_key(input_key):
                        continue
                    if is_comfyui_link(input_value):
                        randomize_linked_seed_source(input_value)
                    else:
                        randomize_input(inputs, input_key)

            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for value in obj:
                walk(value)

    walk(workflow)
    return seed_update_count
