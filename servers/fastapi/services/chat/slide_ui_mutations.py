"""Transactional, owner-scoped mutations for native Template V2 slide UI."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from sqlmodel import select

from utils.owner_context import get_current_owner_id, get_current_owner_is_admin
from models.sql.presentation import PresentationModel
from models.sql.slide import SlideModel
from services.chat.pending_slides import reconcile_pending_slides, slide_at_index
from services.chat.slide_ui_content import update_element
from services.chat.slide_ui_geometry import (
    normalized_position,
    scale_component_elements,
)
from services.chat.slide_ui_query import (
    compact_slide_ui,
    resolve_element_path,
    slide_ui_revision,
)
from services.presentation_version_service import snapshot_slides
from templates.v2.persistence import canonicalize_slide_ui


class SlideUIAccessError(PermissionError):
    """The requested presentation/slide is not visible to the current owner."""


class SlideUIRevisionConflict(RuntimeError):
    """The caller edited an obsolete slide UI revision."""


class SlideUIMutationService:
    """Apply one canonical UI edit and its version snapshot in one transaction."""

    def __init__(self, sql_session: Any, presentation_id: uuid.UUID) -> None:
        self._sql_session = sql_session
        self._presentation_id = presentation_id

    async def get_slide_ui(
        self, *, index: int, include_full_json: bool = False
    ) -> dict[str, Any]:
        slide = await self._owned_slide(index)
        if slide is None:
            raise SlideUIAccessError("slide_not_found_or_forbidden")
        if slide.ui is None:
            raise ValueError("slide_has_no_native_ui")
        return {
            "index": slide.index,
            "slide_id": str(slide.id),
            **compact_slide_ui(slide.ui, include_full_json=include_full_json),
        }

    async def update_slide_ui(
        self,
        *,
        index: int,
        expected_revision: str,
        ui: dict[str, Any],
    ) -> dict[str, Any]:
        requested = deepcopy(ui)

        def mutation(current: dict[str, Any]) -> None:
            current.clear()
            current.update(requested)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:updateSlide",
            mutation=mutation,
        )

    async def update_element(
        self,
        *,
        index: int,
        path: str,
        expected_revision: str,
        content: dict[str, Any] | None = None,
        style: dict[str, Any] | None = None,
        position: dict[str, Any] | None = None,
        size: dict[str, Any] | None = None,
        rotation: float | None = None,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            element, _, _ = resolve_element_path(ui, path)
            update_element(
                element,
                content=content,
                style=style,
                position=position,
                size=size,
                rotation=rotation,
            )

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:updateElement",
            mutation=mutation,
        )

    async def add_element(
        self,
        *,
        index: int,
        component_id: str,
        element: dict[str, Any],
        expected_revision: str,
        insert_at: int | None = None,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            component = self._component(ui, component_id)
            elements = component.get("elements")
            if not isinstance(elements, list):
                raise TypeError("component_elements_missing")
            target = len(elements) if insert_at is None else insert_at
            if target < 0 or target > len(elements):
                raise ValueError("element_insert_index_out_of_range")
            elements.insert(target, deepcopy(element))

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:addElement",
            mutation=mutation,
        )

    async def delete_element(
        self, *, index: int, path: str, expected_revision: str
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            _, parent, key = resolve_element_path(ui, path)
            if isinstance(parent, list) and isinstance(key, int):
                parent.pop(key)
            elif isinstance(parent, dict) and key == "child":
                parent["child"] = None
            else:
                raise ValueError("element_parent_not_mutable")

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:deleteElement",
            mutation=mutation,
        )

    async def duplicate_element(
        self, *, index: int, path: str, expected_revision: str
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            element, parent, key = resolve_element_path(ui, path)
            if not isinstance(parent, list) or not isinstance(key, int):
                raise TypeError("only_list_elements_can_be_duplicated")
            duplicate = deepcopy(element)
            name = duplicate.get("name")
            if isinstance(name, str) and name:
                duplicate["name"] = f"{name}-copy"
            parent.insert(key + 1, duplicate)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:duplicateElement",
            mutation=mutation,
        )

    async def reorder_element(
        self,
        *,
        index: int,
        path: str,
        expected_revision: str,
        action: str,
        to_index: int | None = None,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            _, parent, key = resolve_element_path(ui, path)
            if not isinstance(parent, list) or not isinstance(key, int):
                raise TypeError("only_list_elements_can_be_reordered")
            if action == "bring-to-front":
                target = len(parent) - 1
            elif action == "send-to-back":
                target = 0
            elif action == "move-forward":
                target = min(len(parent) - 1, key + 1)
            elif action == "move-backward":
                target = max(0, key - 1)
            elif action == "move-to-index" and isinstance(to_index, int):
                target = to_index
            else:
                raise ValueError("element_reorder_action_not_supported")
            if target < 0 or target >= len(parent):
                raise ValueError("element_reorder_index_out_of_range")
            parent.insert(target, parent.pop(key))

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label=f"chat:{action}",
            mutation=mutation,
        )

    async def group_elements(
        self,
        *,
        index: int,
        paths: list[str],
        name: str,
        expected_revision: str,
    ) -> dict[str, Any]:
        if len(paths) < 2 or len(paths) > 100:
            raise ValueError("group_requires_between_2_and_100_elements")

        def mutation(ui: dict[str, Any]) -> None:
            resolved = [resolve_element_path(ui, path) for path in paths]
            parent = resolved[0][1]
            if not isinstance(parent, list) or any(item[1] is not parent for item in resolved):
                raise ValueError("group_elements_must_share_a_list_parent")
            indices = [item[2] for item in resolved]
            if any(not isinstance(value, int) for value in indices):
                raise ValueError("group_elements_must_be_list_children")
            integer_indices = [int(value) for value in indices]
            if len(set(integer_indices)) != len(integer_indices):
                raise ValueError("group_element_paths_must_be_unique")
            first = min(integer_indices)
            children = [deepcopy(parent[value]) for value in sorted(integer_indices)]
            for value in sorted(integer_indices, reverse=True):
                parent.pop(value)
            parent.insert(first, {"type": "group", "name": name, "children": children})

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:groupElements",
            mutation=mutation,
        )

    async def ungroup_element(
        self, *, index: int, path: str, expected_revision: str
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            element, parent, key = resolve_element_path(ui, path)
            children = element.get("children")
            if element.get("type") != "group" or not isinstance(children, list):
                raise ValueError("selected_element_is_not_a_group")
            if not isinstance(parent, list) or not isinstance(key, int):
                raise TypeError("group_parent_not_mutable")
            parent[key : key + 1] = deepcopy(children)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:ungroupElement",
            mutation=mutation,
        )

    async def add_component(
        self,
        *,
        index: int,
        component: dict[str, Any],
        expected_revision: str,
        insert_at: int | None = None,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            components = self._components(ui)
            target = len(components) if insert_at is None else insert_at
            if target < 0 or target > len(components):
                raise ValueError("component_insert_index_out_of_range")
            components.insert(target, deepcopy(component))

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:addComponent",
            mutation=mutation,
        )

    async def update_component(
        self,
        *,
        index: int,
        component_id: str,
        expected_revision: str,
        description: str | None = None,
        position: dict[str, Any] | None = None,
        size: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            component = self._component(ui, component_id)
            if description is not None:
                component["description"] = description
            if position is not None:
                component["position"] = normalized_position(position)
            if size is not None:
                scale_component_elements(component, size)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:updateComponent",
            mutation=mutation,
        )

    async def delete_component(
        self, *, index: int, component_id: str, expected_revision: str
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            components = self._components(ui)
            target = self._component_index(components, component_id)
            components.pop(target)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:deleteComponent",
            mutation=mutation,
        )

    async def duplicate_component(
        self,
        *,
        index: int,
        component_id: str,
        expected_revision: str,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            components = self._components(ui)
            target = self._component_index(components, component_id)
            duplicate = deepcopy(components[target])
            base_id = str(duplicate.get("id") or "component")
            known = {str(component.get("id")) for component in components}
            suffix = 2
            candidate = f"{base_id}-copy"
            while candidate in known:
                candidate = f"{base_id}-copy-{suffix}"
                suffix += 1
            duplicate["id"] = candidate
            duplicate["position"] = {
                "x": float(duplicate["position"]["x"]) + 12,
                "y": float(duplicate["position"]["y"]) + 12,
            }
            components.insert(target + 1, duplicate)

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:duplicateComponent",
            mutation=mutation,
        )

    async def reorder_component(
        self,
        *,
        index: int,
        component_id: str,
        expected_revision: str,
        to_index: int,
    ) -> dict[str, Any]:
        def mutation(ui: dict[str, Any]) -> None:
            components = self._components(ui)
            source = self._component_index(components, component_id)
            if to_index < 0 or to_index >= len(components):
                raise ValueError("component_reorder_index_out_of_range")
            components.insert(to_index, components.pop(source))

        return await self._mutate(
            index=index,
            expected_revision=expected_revision,
            label="chat:reorderComponent",
            mutation=mutation,
        )

    async def _mutate(
        self,
        *,
        index: int,
        expected_revision: str,
        label: str,
        mutation: Callable[[dict[str, Any]], None],
    ) -> dict[str, Any]:
        presentation = await self._owned_presentation()
        slide = await self._owned_slide(index)
        if presentation is None or slide is None:
            raise SlideUIAccessError("slide_not_found_or_forbidden")
        if slide.ui is None:
            raise ValueError("slide_has_no_native_ui")
        current = canonicalize_slide_ui(slide.ui)
        if current is None:
            raise ValueError("slide_has_no_native_ui")
        current_revision = slide_ui_revision(current)
        if expected_revision != current_revision:
            raise SlideUIRevisionConflict(
                f"slide_ui_revision_conflict:{current_revision}"
            )

        candidate = deepcopy(current)
        mutation(candidate)
        canonical = canonicalize_slide_ui(candidate)
        if canonical is None:
            raise ValueError("slide_ui_required")
        next_revision = slide_ui_revision(canonical)
        if next_revision == current_revision:
            return {
                "updated": False,
                "index": slide.index,
                "slide_id": str(slide.id),
                "revision": current_revision,
                "message": "No UI changes were produced.",
            }

        slides = await self._slides()
        try:
            await snapshot_slides(
                self._sql_session,
                self._presentation_id,
                [item.model_dump(mode="json") for item in slides],
                label=label,
                force=True,
            )
            slide.ui = canonical
            slide.html_content = None
            self._sql_session.add(slide)
            await self._sql_session.commit()
            refresh = getattr(self._sql_session, "refresh", None)
            if refresh is not None:
                await refresh(slide)
        except Exception:
            rollback = getattr(self._sql_session, "rollback", None)
            if rollback is not None:
                await rollback()
            raise

        return {
            "updated": True,
            "index": slide.index,
            "slide_id": str(slide.id),
            "revision": next_revision,
        }

    async def _owned_presentation(self) -> PresentationModel | None:
        statement = select(PresentationModel).where(
            PresentationModel.id == self._presentation_id
        )
        owner_id = get_current_owner_id()
        if not get_current_owner_is_admin():
            if owner_id is None:
                statement = statement.where(PresentationModel.owner_id.is_(None))
            else:
                statement = statement.where(PresentationModel.owner_id == owner_id)
        return await self._sql_session.scalar(statement)

    async def _owned_slide(self, index: int) -> SlideModel | None:
        # A WHERE on `index` would address the database's pre-edit position
        # while a buffered turn holds unpublished index shifts.
        return slide_at_index(await self._slides(), index)

    async def _slides(self) -> list[SlideModel]:
        statement = (
            select(SlideModel)
            .where(SlideModel.presentation == self._presentation_id)
            .order_by(SlideModel.index)
        )
        owner_id = get_current_owner_id()
        if not get_current_owner_is_admin():
            statement = statement.where(
                SlideModel.owner_id.is_(None)
                if owner_id is None
                else SlideModel.owner_id == owner_id
            )
        return reconcile_pending_slides(
            self._sql_session,
            self._presentation_id,
            await self._sql_session.scalars(statement),
        )

    @staticmethod
    def _components(ui: dict[str, Any]) -> list[dict[str, Any]]:
        components = ui.get("components")
        if not isinstance(components, list):
            raise TypeError("slide_components_missing")
        return components

    @classmethod
    def _component(cls, ui: dict[str, Any], component_id: str) -> dict[str, Any]:
        components = cls._components(ui)
        return components[cls._component_index(components, component_id)]

    @staticmethod
    def _component_index(
        components: list[dict[str, Any]], component_id: str
    ) -> int:
        for index, component in enumerate(components):
            if isinstance(component, dict) and component.get("id") == component_id:
                return index
        raise ValueError(f"component_not_found:{component_id}")
