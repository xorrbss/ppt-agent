# AI-authored template application: target-owned workspace contract

## Scope

This document fixes the contract for confirming an AI-authored template change
from an existing presentation. It applies to the FastAPI confirmation endpoint,
the Next.js presentation workspace, and the Electron window boundary.

The legacy retemplate endpoint remains available to non-workspace callers. It
queues a derived deck and is covered by
`servers/fastapi/tests/unit/test_authored_retemplate.py`. It is not the
chat-first confirmation contract described here.

## Public HTTP contract

The confirmation request is:

```http
POST /api/v1/ppt/workspace/presentations/{sourceId}/template-applications
Idempotency-Key: <1..255 characters>
Content-Type: application/json

{
  "source_revision": 7,
  "prompt": "Apply the selected authored style",
  "authored_style": "signal",
  "vision_qa": false
}
```

`Idempotency-Key` is a required header. It must not be duplicated in the JSON
body. A successful admission returns HTTP 202 with this versioned receipt:

```json
{
  "schema_version": 1,
  "source_presentation_id": "source-uuid",
  "source_revision": 7,
  "target_presentation_id": "target-uuid",
  "conversation_id": "target-conversation-uuid",
  "first_user_message_id": "target-message-uuid",
  "operation_id": "target-operation-uuid",
  "status": "queued",
  "operation_state": "queued",
  "duplicate": false,
  "timeline": {
    "schema_version": 1,
    "conversation_id": "target-conversation-uuid",
    "items": [
      {
        "schema_version": 1,
        "anchor_type": "message",
        "id": "target-message-uuid",
        "position": 1,
        "role": "user",
        "content": "Apply the selected authored style",
        "attachments": []
      },
      {
        "schema_version": 1,
        "anchor_type": "operation",
        "id": "target-operation-uuid",
        "operation_id": "target-operation-uuid",
        "position": 2,
        "operation_type": "template.apply",
        "title": "Apply authored template",
        "status": "queued",
        "version": 0,
        "operation_version": 0,
        "expected_revision": 0,
        "actor": { "type": "user", "label": "You" },
        "target": {
          "presentation_id": "target-uuid",
          "summary": "Target presentation",
          "context": {}
        },
        "activity_summary": "Queued.",
        "allowed_actions": [],
        "events": []
      }
    ],
    "has_more": false,
    "revision": 0
  }
}
```

The durable generation job and task identifiers are internal implementation
details. The UI follows the returned target timeline and operation receipt; it
does not poll a source-owned generation task.

## Ownership and invariants

| Data | Owner after admission | Contract |
| --- | --- | --- |
| Source presentation | Source | Read and revision-fenced; never mutated by confirmation or target generation |
| Target presentation | Target | Allocated immediately in `staging`, revision 0 |
| Conversation and first user message | Target | Prompt is the first target timeline message |
| `template.apply` operation | Target | `presentation_id` is the target; source ID/revision are metadata only |
| Generation job and task | Target | Job points to the same target and operation |
| Timeline, progress, errors, retry/cancel, completion | Target | Never projected into the source timeline |

The source and target IDs must differ. The source revision in the request is a
compare-and-swap fence: a stale revision fails with `revision_conflict` before
any target-owned row is committed.

## Atomic admission and idempotency

One database transaction creates the complete target graph: staging
presentation, conversation, first user message, queued `template.apply`
operation, initial event/outbox record, generation task, and generation job.
The 202 receipt is returned only after that transaction commits. Any failure
before commit leaves none of those rows durable.

Idempotency is scoped to the source presentation and the required header. A
retry with the same key and identical source revision/body returns the same
target, conversation, message, operation, and timeline with `duplicate: true`.
It creates no second target or job. Reusing the key with a different payload is
a 409 `idempotency_key_payload_conflict`.

The Next.js client also suppresses duplicate confirmation clicks by reusing the
same in-flight promise and key for the selected style/vision-QA signature. This
is an optimization, not a substitute for server idempotency. After a failed
attempt, an explicit retry may create a fresh promise but must reuse that key so
an ambiguous admission result resolves to the original target.

## State transitions

The target-owned lifecycle is:

| Admission/job state | Target presentation | Visible behavior |
| --- | --- | --- |
| `queued` | `staging`, revision 0 | 202 receipt and target route are immediately available |
| `running` | `staging` | Target timeline shows progress; retry/cancel remain target-scoped |
| `succeeded` | `published`, committed revision | The same target route renders the completed presentation |
| `failed` or `cancelled` | non-published target with terminal failure state | Target timeline shows the safe error and permitted actions |

Publication must complete the already allocated target. It must not allocate a
replacement ID. A staging target is readable by its authenticated owner so the
new workspace route does not transiently return 404.

## Browser popup and fallback contract

The user gesture synchronously attempts `window.open("about:blank", "_blank")`
so popup blockers can make a deterministic decision. After the 202 receipt, a
live popup is replaced with:

```text
/presentation?id={targetId}&workspace_staging=template
```

If the popup is blocked, throws, or is closed before the receipt arrives, the
client displays a warning and navigates the current tab to the same target URL.
If confirmation fails, any reserved blank popup is closed and the current
source page remains in place. No completion card or target link is appended to
the source timeline.

## Electron trust boundary

Electron treats an internal presentation popup as trusted only when all of the
following hold:

- the URL uses HTTP(S), has no user information, and matches the exact runtime
  app origin (scheme, host, and port);
- the normalized path is exactly `/presentation` or `/presentation/`;
- the `id` query parameter is non-blank and bounded;
- the child uses the app's isolated renderer preferences and preload policy.

Trusted presentation popups are deduplicated by origin plus presentation ID.
The presentation child may navigate only within trusted presentation routes.
The literal `about:blank` is the sole non-HTTP(S) exception: Electron may admit
it as an inert anonymous staging child owned by the opener. Chromium reports
`target="_blank"` as either `_blank` or an empty frame name, so those two values
are accepted while every reusable explicit name remains blocked. That child
may transition only once to a trusted presentation URL. It cannot open further
popups or navigate elsewhere.
Other HTTP(S) URLs keep the existing external-browser policy, and unsupported
schemes such as `javascript:`, `data:`, and `file:` are denied. A denied or
failed child creation remains observable as a null popup so the renderer's
same-tab fallback still runs.

## Regression evidence

The pre-change source-owned behavior was pinned by the authored retemplate
tests, `test_workspace_authored_template_operation_handler.py`, the
`PresentationHeader`/`WorkspaceChatPanel` Cypress tests, and Electron's original
external-open handler. The dedicated target-owned regression files are:

- `servers/fastapi/tests/regression/test_ai_authored_template_target_workspace_contract.py`
- `servers/nextjs/app/(presentation-generator)/presentation/components/AuthoredTemplateChanger.target-workspace.cy.tsx`
- `electron/tests/template-workspace-trust-boundary.test.cjs`

Together they pin the required header/body/receipt, atomic target graph,
idempotent replay and conflict behavior, source immutability, popup fallback,
and exact Electron same-origin presentation boundary.
