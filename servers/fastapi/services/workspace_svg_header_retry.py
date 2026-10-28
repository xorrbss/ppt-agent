"""Immutable lineage and bounded admission for explicit SVG header retries."""
from copy import deepcopy

from utils.hashing import canonical_json_digest

HEADER_RETRY_VERSION = "svg-common-header-settled-retry/v1"
_BUDGET_FIELDS = ("planned_request_limit", "retry_request_limit",
                  "estimated_cost_limit_microunits", "actual_cost_limit_microunits")


def header_operation_request_hash(operation):
    """Keep the original command and explicit retry fingerprints distinct."""
    from services.workspace_service import canonical_payload_hash

    payload = operation.request_payload
    public_keys = ("presentation_id", "command", "command_type", "context", "expected_revision", "asset_ids")
    if not isinstance(payload, dict) or any(key not in payload for key in public_keys):
        raise ValueError("svg_common_header_retry_lineage_conflict")
    if operation.retry_of_operation_id is not None:
        if payload.get("retry_of_operation_id") != str(operation.retry_of_operation_id):
            raise ValueError("svg_common_header_retry_lineage_conflict")
        return canonical_payload_hash({"retry_of_operation_id": str(operation.retry_of_operation_id),
            "presentation_revision": operation.expected_revision, "request_payload": payload})
    return canonical_payload_hash({key: payload.get(key) for key in public_keys})


def _checkpoint_witnesses(checkpoint, *, active=False):
    """Retain provider responses while local render/quality evidence evolves."""
    from services.svg_common_header_design_checkpoint import _candidate_key
    from services.generation_core.authoring.author_slide_svg import svg_author_semantic_slot
    from services.generation_core.drivers.svg.collect import _unknown_provider_checkpoint
    from services.svg_contract import source_digest

    if not active and _unknown_provider_checkpoint(checkpoint):
        raise ValueError("svg_common_header_retry_provider_history_present")
    document = checkpoint.get("document", {})
    witnesses = {}
    for key, step in document.get("steps", {}).items():
        if not isinstance(step, dict):
            raise ValueError("svg_common_header_retry_checkpoint_unproven")
        if active and step.get("status") == "started":
            continue
        response = step.get("response") or {}
        slot = step.get("semantic_slot")
        if (step.get("status") != "succeeded" or step.get("stage") not in {"design", "selection"}
                or type(step.get("ordinal")) is not int or step["ordinal"] not in {1, 2}
                or slot != f"svg-header-{step['stage']}-{step['ordinal']}"
                or response.get("semantic_slot") != slot):
            raise ValueError("svg_common_header_retry_provider_history_present")
        witnesses["header:" + key] = {"slot": slot, "stage": "header", "slide_index": -1,
            "is_retry": step["ordinal"] > 1,
            "provider": response.get("provider"), "model": response.get("model"), "evidence": deepcopy(step)}
    pages = (document.get("parent_publication") or {}).get("outline", {}).get("slides", [])
    for key, page in checkpoint.get("candidate_pages", {}).items():
        if (not isinstance(page, dict) or (not active and page.get("inflight") is not None) or page.get("provider_failures")
                or page.get("state") == "unknown" or page.get("status") in {"unknown", "started"}):
            raise ValueError("svg_common_header_retry_provider_history_present")
        for attempt in page.get("attempts", []):
            if attempt.get("kind") == "local":
                continue
            receipt = attempt.get("receipt") or {}
            identity = receipt.get("author_attempt_identity")
            if not isinstance(identity, dict):
                raise ValueError("svg_common_header_retry_checkpoint_unproven")
            ordinal, raw = receipt.get("attempt_ordinal"), attempt.get("raw_response")
            page_id = (identity or {}).get("page_id")
            indexes = [index for index, item in enumerate(pages) if item.get("page_id") == page_id]
            if (attempt.get("kind") != "provider" or not isinstance(identity, dict) or len(indexes) != 1
                    or type(ordinal) is not int or ordinal not in {1, 2} or not isinstance(raw, str)
                    or receipt.get("raw_digest") != source_digest(raw)
                    or not isinstance(receipt.get("style_digest"), str)
                    or not isinstance(page.get("input_digest"), str)
                    or page.get("header_author_attempt_identity") != identity
                    or key != _candidate_key(indexes[0], identity)):
                raise ValueError("svg_common_header_retry_checkpoint_unproven")
            slot = svg_author_semantic_slot(indexes[0], ordinal, identity)
            if f"body:{key}:{ordinal}" in witnesses:
                raise ValueError("svg_common_header_retry_checkpoint_unproven")
            witnesses[f"body:{key}:{ordinal}"] = {"slot": slot, "stage": "author" if ordinal == 1 else "repair",
                "is_retry": ordinal > 1,
                "slide_index": indexes[0], "provider": receipt.get("provider"), "model": receipt.get("requested_model"),
                "evidence": {"attempt": {"kind": "provider", "raw_response": raw, "receipt": deepcopy(receipt)},
                             "input_digest": page["input_digest"], "identity": identity}}
    return witnesses


async def header_retry_source(session, *, source, expected_revision):
    """Prove unstarted or settled provider history without reopening it.

    Unknown or unproven responses require a separate recovery contract.
    Saved header responses retain their original ledger/owner/fence provenance.
    """
    from sqlalchemy import select
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
    from models.sql.provider_runtime_reservation import ProviderRuntimeBudget, ProviderRuntimeReservation
    from models.sql.provider_usage_receipt import ProviderUsageReceipt
    from models.sql.workspace import WorkspaceOperation, get_workspace_owner_scope
    from services.public_operation_admission import current_principal_context
    from utils.datetime_utils import as_utc
    from utils.hashing import sha256_hex
    from services.public_operation_admission_models import _logical_call_ordinal
    from services.svg_common_header_design_plan import validate_header_design_plan

    principal = current_principal_context()
    pins, seen, bounds, policy, histories = [], set(), None, None, []
    consumed = (0, 0, 0, 0)
    runtime_bounds = (None, None)
    current = source
    original_intent = {key: source.request_payload.get(key) for key in
        ("presentation_id", "command", "command_type", "context", "expected_revision", "asset_ids", "svg_header_revision")}
    plan = validate_header_design_plan((original_intent.get("svg_header_revision") or {}).get("plan"))
    if plan is None:
        raise ValueError("svg_common_header_retry_checkpoint_unproven")
    for _ in range(16):
        current = await session.scalar(select(WorkspaceOperation).where(
            WorkspaceOperation.id == current.id).with_for_update().execution_options(populate_existing=True))
        if (current is None or current.id in seen or current.owner_id != principal.owner_id
                or current.owner_scope != get_workspace_owner_scope() or current.owner_scope != source.owner_scope
                or current.presentation_id != source.presentation_id or current.conversation_id != source.conversation_id
                or current.kind != "design.apply" or current.expected_revision != expected_revision
                or current.state not in {"failed", "cancelled", "dead_letter"} or current.completed_at is None
                or current.attempt_number < 1 or current.attempt_number > current.max_attempts
                or current.attempt_token is not None or current.lease_expires_at is not None
                or current.contract_version != "public-operation/v2"
                or current.execution_profile_version != "public-generation-export/v2"
                or current.request_payload.get("principal_tenant_id") != principal.tenant_id
                or (current.request_payload.get("context") or {}).get("target") != "common_header"
                or {key: current.request_payload.get(key) for key in original_intent} != original_intent
                or header_operation_request_hash(current) != current.request_hash):
            raise ValueError("svg_common_header_retry_lineage_conflict")
        seen.add(current.id)
        admission = await session.scalar(select(PublicOperationAdmission).where(
            PublicOperationAdmission.operation_id == current.id).with_for_update())
        if (admission is None or admission.owner_id != principal.owner_id or admission.tenant_id != principal.tenant_id
                or admission.operation_kind != "generation" or admission.terminal_at is None
                or admission.state not in {"failed", "cancelled", "budget_exhausted", "deadline_exceeded"}
                or admission.fence < 1 or admission.fence != current.fencing_token
                or admission.deadline_at is None or current.deadline_at is None
                or as_utc(admission.deadline_at) != as_utc(current.deadline_at) or not admission.policy_digest):
            raise ValueError("svg_common_header_retry_admission_conflict")
        identity = (admission.policy_version, admission.policy_digest, admission.resource_units, admission.slide_count)
        if policy is not None and identity != policy:
            raise ValueError("svg_common_header_retry_admission_conflict")
        policy = identity
        if any(getattr(admission, name) != 0 for name in ("estimated_reserved_microunits",
                "actual_reserved_microunits", "actual_cost_unavailable_count")):
            raise ValueError("svg_common_header_retry_provider_history_present")
        used = tuple(getattr(admission, name) for name in ("initial_request_count", "retry_request_count",
                    "estimated_consumed_microunits", "actual_consumed_microunits"))
        limits = tuple(getattr(admission, name) for name in _BUDGET_FIELDS)
        if any(type(value) is not int or value < 0 for value in (*limits, *used)):
            raise ValueError("svg_common_header_retry_admission_conflict")
        consumed = tuple(a + b for a, b in zip(consumed, used, strict=False))
        remaining = tuple(limit - amount for limit, amount in zip(limits, consumed, strict=False))
        if min(remaining) < 0:
            raise ValueError("svg_common_header_retry_budget_exhausted")
        bounds = remaining if bounds is None else tuple(min(a, b) for a, b in zip(bounds, remaining, strict=False))
        checkpoint = current.svg_header_design_checkpoint
        if checkpoint is not None:
            if (not isinstance(checkpoint, dict) or checkpoint.get("version") != "svg-common-header-design-checkpoint/v2"
                    or checkpoint.get("identity") != {"owner_id": str(current.id), "owner_type": "operation",
                                                         "request_digest": current.request_hash}
                    or not isinstance(checkpoint.get("document"), dict)
                    or not isinstance(checkpoint["document"].get("steps", {}), dict)
                    or not isinstance(checkpoint.get("candidate_pages"), dict)
                    or checkpoint["document"].get("status") not in {None, "preparing", "selected"}):
                raise ValueError("svg_common_header_retry_provider_history_present")
            if any(not isinstance(step, dict) or step.get("status") != "succeeded"
                   for step in checkpoint["document"].get("steps", {}).values()):
                raise ValueError("svg_common_header_retry_provider_history_present")
        # Check independent ledgers too: a crash may occur before the public
        # reservation or checkpoint catches up with the durable runtime.
        calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
            PublicProviderLogicalCall.admission_id == admission.id).order_by(PublicProviderLogicalCall.id).with_for_update()))
        if (any(call.stage not in {"header", "author", "repair"} or call.state != "reported"
                or call.outcome != "succeeded" or call.reconciled_at is None
                or type(call.actual_cost_microunits) is not int or call.actual_cost_microunits < 0
                or not call.provider_id or call.provider_id in {"unknown", "unavailable"}
                or not call.model_id or call.model_id in {"unknown", "unavailable"} for call in calls)
                or used != (sum(call.retry_class != "retry" for call in calls),
                            sum(call.retry_class == "retry" for call in calls),
                            sum(call.estimated_cost_microunits for call in calls),
                            sum(call.actual_cost_microunits for call in calls))
                or await session.scalar(select(ProviderUsageReceipt).where(
                    ProviderUsageReceipt.job_id == current.id).limit(1)) is not None):
            raise ValueError("svg_common_header_retry_provider_history_present")
        witnesses = _checkpoint_witnesses(checkpoint or {})
        matched_calls, matched_keys, runtime_calls = set(), set(), {}
        for witness_key, witness in witnesses.items():
            slot, stage, index = witness["slot"], witness["stage"], witness["slide_index"]
            # Inherited steps may join an ancestor. Own physical calls must
            # match the exact operation/stage/slot hash, including resume.
            for call in calls:
                for attempt in range(1, current.attempt_number + 1):
                    key = slot + (f".resume-{attempt}" if attempt > 1 else "")
                    ordinal = _logical_call_ordinal(operation_id=current.id, stage=stage, slide_index=index,
                        logical_call_ordinal=None, logical_call_key=key)
                    expected_hash = sha256_hex(f"public-provider-call/v2:{current.id}:{stage}:{index}:{ordinal}:"
                        f"{sha256_hex(key.encode('utf-8'))}".encode("utf-8"))
                    if (call.stage == stage and call.slide_index == index and call.logical_call_ordinal == ordinal
                            and (call.retry_class == "retry") == witness["is_retry"]
                            and call.idempotency_key_hash == expected_hash
                            and call.provider_id == witness["provider"] and call.model_id == witness["model"]):
                        matched_calls.add(call.id)
                        matched_keys.add(witness_key)
                        from services.provider_usage_instrumentation import ProviderUsageScope
                        scope = ProviderUsageScope(job_id=current.id, operation="authored_generation", stage=stage,
                            attempt_number=attempt, slide_index=None if index == -1 else index, public_only=True)
                        request_id = scope.next_runtime_request_id(slot)
                        runtime_key = sha256_hex(f"provider-reservation-v1:{current.id}:{request_id}".encode())
                        runtime_calls[runtime_key] = call
        if matched_calls != {call.id for call in calls} or len(matched_keys) != len(calls):
            raise ValueError("svg_common_header_retry_provider_history_present")
        budgets = list(await session.scalars(select(ProviderRuntimeBudget).where(
            ProviderRuntimeBudget.workspace_operation_id == current.id).order_by(ProviderRuntimeBudget.id).with_for_update()))
        reservation_history = []
        for budget in budgets:
            reservations = list(await session.scalars(select(ProviderRuntimeReservation).where(
                ProviderRuntimeReservation.budget_id == budget.id).order_by(ProviderRuntimeReservation.id).with_for_update()))
            reservation_history.extend(item.model_dump(mode="json") for item in reservations)
            if (budget.owner_id != principal.owner_id or budget.tenant_id != principal.tenant_id
                    or budget.estimated_reserved != 0 or budget.request_count != len(reservations)
                    or budget.request_count != len(calls)
                    or any(item.state != "reconciled" or item.outcome != "success"
                           or type(item.actual_cost) is not int or item.actual_cost < 0 for item in reservations)
                    or {item.reservation_key_hash for item in reservations} != set(runtime_calls)
                    or any(item.estimated_cost != runtime_calls[item.reservation_key_hash].estimated_cost_microunits
                           or item.actual_cost != runtime_calls[item.reservation_key_hash].actual_cost_microunits
                           for item in reservations if item.reservation_key_hash in runtime_calls)
                    or budget.estimated_consumed != sum(item.estimated_cost for item in reservations)
                    or budget.actual_consumed != sum(item.actual_cost for item in reservations)
                    or budget.estimated_consumed != used[2] or budget.actual_consumed != used[3]):
                raise ValueError("svg_common_header_retry_provider_history_present")
            for index, cap in enumerate((budget.estimated_limit, budget.actual_limit)):
                if cap is not None:
                    left = cap - consumed[index + 2]
                    if left < 0:
                        raise ValueError("svg_common_header_retry_budget_exhausted")
                    runtime_bounds = tuple(min(value, left) if position == index and value is not None
                        else left if position == index else value for position, value in enumerate(runtime_bounds))
        pin = {"operation_id": str(current.id), "owner_id": str(current.owner_id), "tenant_id": admission.tenant_id,
            "request_hash": current.request_hash, "operation_version": current.version,
            "checkpoint_identity": deepcopy((checkpoint or {}).get("identity")),
            "fence": current.fencing_token, "checkpoint_digest": canonical_json_digest(checkpoint),
            "admission_digest": canonical_json_digest(admission.model_dump(mode="json")),
            "logical_calls_digest": canonical_json_digest([call.model_dump(mode="json") for call in calls]),
            "runtime_digest": canonical_json_digest([row.model_dump(mode="json") for row in budgets]),
            "reservations_digest": canonical_json_digest(reservation_history)}
        pins.append(pin)
        histories.append((current, witnesses, matched_keys))
        if current.retry_of_operation_id is None:
            if set(witnesses) != matched_keys:
                raise ValueError("svg_common_header_retry_provider_history_present")
            root_steps = witnesses
            for ancestor, ancestor_steps, own_keys in reversed(histories[:-1]):
                replay = ancestor.request_payload.get("svg_header_retry") or {}
                replay_index = next(i for i, item in enumerate(pins) if item["operation_id"] == str(ancestor.id))
                if replay.get("ancestors") != pins[replay_index + 1:]:
                    raise ValueError("svg_common_header_retry_lineage_conflict")
                if (any(ancestor_steps.get(key) != value for key, value in root_steps.items())
                        or set(ancestor_steps) - set(root_steps) != own_keys):
                    raise ValueError("svg_common_header_retry_lineage_conflict")
                parent = histories[replay_index + 1][0]
                await _require_inherited_bodies(session, ancestor, parent.svg_header_design_checkpoint or {},
                    ancestor.svg_header_design_checkpoint or {}, replay["ancestors"][0])
                root_steps = ancestor_steps
            for stage, cap in (("header", plan["header_call_limit"]),
                               ("author", policy[3] + 1), ("repair", policy[3] + 1)):
                if sum(witness["stage"] == stage for witness in root_steps.values()) > cap:
                    raise ValueError("svg_common_header_retry_budget_exhausted")
            return {"version": HEADER_RETRY_VERSION, "source_operation_id": str(source.id),
                "ancestors": pins, "limits": dict(zip(_BUDGET_FIELDS, bounds, strict=False)),
                "policy_version": policy[0], "policy_digest": policy[1], "resource_units": policy[2],
                "slide_count": policy[3], "consumed": dict(zip(_BUDGET_FIELDS, consumed, strict=False)),
                "runtime_cost_limits": {"estimated": runtime_bounds[0], "actual": runtime_bounds[1]}}
        if current.request_payload.get("svg_header_retry", {}).get("version") != HEADER_RETRY_VERSION:
            raise ValueError("svg_common_header_retry_lineage_conflict")
        current = await session.get(WorkspaceOperation, current.retry_of_operation_id)
        if current is None:
            raise ValueError("svg_common_header_retry_lineage_conflict")
    raise ValueError("svg_common_header_retry_lineage_conflict")


async def _require_inherited_bodies(session, operation, original, checkpoint, source_pin):
    from sqlalchemy import select
    from models.sql.public_admission import PublicOperationAdmission, PublicProviderLogicalCall
    from services.public_operation_admission_models import _logical_call_ordinal
    from services.svg_contract import source_digest
    from utils.hashing import sha256_hex

    original_witnesses = _checkpoint_witnesses(original)
    current_witnesses = _checkpoint_witnesses(checkpoint, active=operation.state == "running")
    for key, source_page in original.get("candidate_pages", {}).items():
        page = checkpoint.get("candidate_pages", {}).get(key)
        if not isinstance(page, dict) or page.get("input_digest") != source_page.get("input_digest"):
            raise ValueError("svg_common_header_retry_checkpoint_conflict")
        # Local reuse receipts are provenance too, even when no SDK call was
        # purchased for this candidate. Rendering may update only local QA.
        def attempts(value):
            return [{name: item.get(name) for name in ("kind", "receipt", "raw_response")}
                    for item in value.get("attempts", [])]
        prior_attempts, next_attempts = attempts(source_page), attempts(page)
        if next_attempts[:len(prior_attempts)] != prior_attempts:
            raise ValueError("svg_common_header_retry_checkpoint_conflict")
        body = source_page.get("author_body_svg")
        if page.get("author_body_svg") != body:
            # A newly completed bounded repair may replace the active body;
            # a copied receipt or a different candidate cannot authorize it.
            additions = [(name, witness) for name, witness in current_witnesses.items()
                         if name.startswith(f"body:{key}:") and name not in original_witnesses]
            if not additions:
                raise ValueError("svg_common_header_retry_checkpoint_conflict")
            _, witness = max(additions, key=lambda item: item[1]["evidence"]["attempt"]["receipt"]["attempt_ordinal"])
            receipt = witness["evidence"]["attempt"]["receipt"]
            if (not isinstance(page.get("author_body_svg"), str)
                    or source_digest(page["author_body_svg"]) != receipt.get("source_digest")):
                raise ValueError("svg_common_header_retry_checkpoint_unproven")
            admission = await session.scalar(select(PublicOperationAdmission).where(
                PublicOperationAdmission.operation_id == operation.id))
            calls = list(await session.scalars(select(PublicProviderLogicalCall).where(
                PublicProviderLogicalCall.admission_id == admission.id))) if admission is not None else []
            matched = False
            for attempt_number in range(1, operation.attempt_number + 1):
                slot = witness["slot"] + (f".resume-{attempt_number}" if attempt_number > 1 else "")
                ordinal = _logical_call_ordinal(operation_id=operation.id, stage=witness["stage"],
                    slide_index=witness["slide_index"], logical_call_ordinal=None, logical_call_key=slot)
                digest = sha256_hex(f"public-provider-call/v2:{operation.id}:{witness['stage']}:"
                    f"{witness['slide_index']}:{ordinal}:{sha256_hex(slot.encode())}".encode())
                matched |= any(call.idempotency_key_hash == digest and call.logical_call_ordinal == ordinal
                    and call.stage == witness["stage"] and call.slide_index == witness["slide_index"]
                    and call.provider_id == witness["provider"] and call.model_id == witness["model"]
                    and call.state == "reported" and call.outcome == "succeeded" and call.reconciled_at is not None
                    and type(call.actual_cost_microunits) is int and call.actual_cost_microunits >= 0 for call in calls)
            if not matched:
                raise ValueError("svg_common_header_retry_checkpoint_unproven")
        expected_parent = source_page.get("retry_render_parent")
        if (source_page.get("quality") or {}).get("passed") is not True or source_page.get("terminal"):
            expected_parent = {"page_key": key, "checkpoint_digest": source_pin["checkpoint_digest"],
                "quality_digest": canonical_json_digest(source_page.get("quality")),
                "svg_digest": canonical_json_digest(source_page.get("svg_content"))}
        if page.get("retry_render_parent") != expected_parent:
            raise ValueError("svg_common_header_retry_checkpoint_conflict")
        svg = page.get("svg_content")
        allowed = [source_page.get("svg_content")]
        if expected_parent is not None or page.get("author_body_svg") != body:
            allowed.append(page.get("author_body_svg"))
        if svg not in allowed:
            quality = page.get("quality") or {}
            if svg is None and page.get("repair_hint"):
                continue
            if (not isinstance(svg, str) or quality.get("source_digest") != source_digest(svg)
                    or quality.get("measurement_complete") is not True):
                raise ValueError("svg_common_header_retry_checkpoint_conflict")


async def require_header_retry_lineage(session, operation, admission=None):
    if operation.retry_of_operation_id is None:
        if "svg_header_retry" in operation.request_payload:
            raise ValueError("svg_common_header_retry_lineage_conflict")
        return None
    from models.sql.workspace import WorkspaceOperation

    source = await session.get(WorkspaceOperation, operation.retry_of_operation_id)
    if (source is None or source.owner_id != operation.owner_id or source.owner_scope != operation.owner_scope
            or source.presentation_id != operation.presentation_id or source.conversation_id != operation.conversation_id):
        raise ValueError("svg_common_header_retry_lineage_conflict")
    verified = await header_retry_source(session, source=source, expected_revision=operation.expected_revision)
    intent_keys = ("presentation_id", "command", "command_type", "context", "expected_revision", "asset_ids", "svg_header_revision")
    if (verified != operation.request_payload.get("svg_header_retry")
            or any(operation.request_payload.get(key) != source.request_payload.get(key) for key in intent_keys)
            or header_operation_request_hash(operation) != operation.request_hash):
        raise ValueError("svg_common_header_retry_lineage_conflict")
    checkpoint = operation.svg_header_design_checkpoint or {}
    original = source.svg_header_design_checkpoint or {}
    inherited = _checkpoint_witnesses(original)
    replayed = _checkpoint_witnesses(checkpoint, active=operation.state == "running")
    if (checkpoint.get("replay_source") != verified["ancestors"][0]
            or any(replayed.get(key) != witness for key, witness in inherited.items())):
        raise ValueError("svg_common_header_retry_checkpoint_conflict")
    await _require_inherited_bodies(session, operation, original, checkpoint, verified["ancestors"][0])
    if admission is not None and (admission.policy_version != verified["policy_version"]
            or admission.policy_digest != verified["policy_digest"] or admission.resource_units != verified["resource_units"]
            or admission.slide_count != verified["slide_count"]
            or any(getattr(admission, name) > limit for name, limit in verified["limits"].items())):
        raise ValueError("svg_common_header_retry_admission_conflict")
    return verified


def seed_header_retry_checkpoint(operation, source):
    """Create new ownership while retaining the complete producing identity."""
    original = source.svg_header_design_checkpoint or {}
    operation.svg_header_design_checkpoint = {
        "version": "svg-common-header-design-checkpoint/v2",
        "identity": {"owner_id": str(operation.id), "owner_type": "operation", "request_digest": operation.request_hash},
        "document": deepcopy(original.get("document", {})), "candidate_pages": deepcopy(original.get("candidate_pages", {})),
        "replay_source": deepcopy(operation.request_payload["svg_header_retry"]["ancestors"][0]),
    }
    for key, page in operation.svg_header_design_checkpoint["candidate_pages"].items():
        if (page.get("quality") or {}).get("passed") is True and not page.get("terminal"):
            continue
        # An explicit child may remeasure a settled body under repaired local
        # rendering code. Keep the ancestor's failed metrics and complete
        # checkpoint untouched; the new render never resets provider attempts.
        page["retry_render_parent"] = {"page_key": key, "checkpoint_digest":
            operation.svg_header_design_checkpoint["replay_source"]["checkpoint_digest"],
            "quality_digest": canonical_json_digest(page.get("quality")),
            "svg_digest": canonical_json_digest(page.get("svg_content"))}
        body = page.get("author_body_svg")
        if isinstance(body, str) and body:
            page["svg_content"] = body
            composition = (page.get("source_preservation") or {}).get("common_header_composition")
            if composition:
                page["source_preservation"] = deepcopy(composition["body_receipt"])
        for field in ("quality", "png_path", "png_sha256", "terminal", "source_digest", "system_furniture"):
            page.pop(field, None)


def retry_runtime_cost_limits(manifest, estimated, actual):
    """Clamp current environment caps by all settled ancestor consumption."""
    result = []
    for name, current, consumed_key in (("estimated", estimated, "estimated_cost_limit_microunits"),
                                        ("actual", actual, "actual_cost_limit_microunits")):
        inherited = manifest["runtime_cost_limits"][name]
        remaining = None if current is None else max(0, current - manifest["consumed"][consumed_key])
        result.append(inherited if remaining is None else remaining if inherited is None else min(remaining, inherited))
    return tuple(result)


def require_retry_page_context(owner, ctx, request, outline, index, identity, saved):
    """Join replayed bodies to the exact reconstructed page/style inputs."""
    if owner.retry_of_operation_id is None or not saved:
        return
    from services.svg_common_header_generation import _page_input_digest

    if saved.get("input_digest") != _page_input_digest(ctx, request, outline, request.language, index):
        raise ValueError("svg_common_header_retry_checkpoint_unproven")
    for attempt in saved.get("attempts", []):
        if attempt.get("kind") == "local":
            continue
        receipt = attempt.get("receipt") or {}
        if receipt.get("style_digest") != ctx.snapshot.get("digest") or receipt.get("author_attempt_identity") != identity:
            raise ValueError("svg_common_header_retry_checkpoint_unproven")
