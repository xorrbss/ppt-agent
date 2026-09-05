import asyncio
import hashlib
import hmac
import json
import logging

import aiohttp
from sqlmodel import select
from enums.webhook_event import WebhookEvent
from models.sql.webhook_subscription import WebhookSubscription
from services.database import async_session_maker, get_async_session
from services.outbound_url_guard import (
    EgressPolicyError,
    OutboundTarget,
    pinned_client_session,
    reject_redirect,
    validate_outbound_url_async,
)
from services.provider_egress_policy import AddressResolver
from utils.datetime_utils import get_current_utc_datetime

LOGGER = logging.getLogger(__name__)

# Retry a failed delivery (connection error OR non-2xx) with exponential backoff.
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 0.5
_DELIVERY_TIMEOUT_SECONDS = 30
# Policy denials are deterministic; only a DNS outage is worth retrying.
_RETRYABLE_POLICY_CODES = frozenset({"egress_dns_unavailable"})

# Seams for tests: an offline resolver and a session factory that never
# opens a socket. Production code never reassigns them.
_resolver: AddressResolver | None = None
_build_session = pinned_client_session
# Seam for tests: the DB session factory used to record delivery outcomes.
_session_maker = async_session_maker


class WebhookService:

    @classmethod
    async def send_webhook(cls, event: WebhookEvent, data: dict):
        async for sql_session in get_async_session():
            webhook_subscriptions = await sql_session.scalars(
                select(WebhookSubscription).where(
                    WebhookSubscription.event == event.value
                )
            )
            webhook_subscriptions = list(webhook_subscriptions)
            if not webhook_subscriptions:
                return

            async_tasks = []
            for webhook_subscription in webhook_subscriptions:
                async_tasks.append(
                    cls.send_request_to_webhook(webhook_subscription, data)
                )

            await asyncio.gather(*async_tasks)

            break

    @classmethod
    async def send_request_to_webhook(
        cls, subscription: WebhookSubscription, data: dict
    ):

        # Sign the exact bytes we send. HMAC-SHA256 lets the receiver verify
        # authenticity/integrity without the shared secret ever crossing the
        # wire (the old code posted the raw secret in an Authorization header,
        # so any interception leaked it).
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
        }
        if subscription.secret:
            signature = hmac.new(
                subscription.secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers["X-Presenton-Signature"] = f"sha256={signature}"

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                # Re-validate at send time: the stored URL may have been
                # re-pointed at a private address since subscription (DNS
                # rebinding), so every attempt resolves and pins afresh.
                target = await validate_outbound_url_async(
                    subscription.url, resolver=_resolver
                )
                await cls._post_once(target, body, headers)
                # Clear any earlier recorded rejection now that delivery
                # recovered (operator fixed the target, allowlisted it, etc).
                await cls._record_delivery_outcome(subscription.id, code=None)
                return
            except EgressPolicyError as exc:
                error_detail = exc.code
                if exc.code not in _RETRYABLE_POLICY_CODES:
                    LOGGER.error(
                        "webhook %s delivery denied by outbound policy: %s",
                        subscription.id,
                        exc.code,
                    )
                    # A terminal rejection is stored on the row rather than
                    # only logged, so a subscription pointed at a target the
                    # guard now refuses does not fail silently forever.
                    await cls._record_delivery_outcome(subscription.id, code=exc.code)
                    return
            except Exception as exc:  # noqa: BLE001
                error_detail = str(exc)

            if attempt < _MAX_ATTEMPTS:
                backoff = _BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                LOGGER.warning(
                    "webhook %s attempt %d/%d failed (%s); retrying in %.1fs",
                    subscription.id,
                    attempt,
                    _MAX_ATTEMPTS,
                    error_detail,
                    backoff,
                )
                await asyncio.sleep(backoff)
            else:
                LOGGER.error(
                    "webhook %s failed after %d attempts: %s",
                    subscription.id,
                    _MAX_ATTEMPTS,
                    error_detail,
                )

    @staticmethod
    async def _record_delivery_outcome(subscription_id: str, *, code: str | None) -> None:
        """Persist the terminal outcome of a delivery attempt on the row.

        ``code`` is the rejecting ``EgressPolicyError`` code, or ``None`` to
        clear a previously recorded rejection after a delivery succeeds. Best
        effort: a failure to persist must not raise out of the delivery path.
        """
        try:
            async with _session_maker() as sql_session:
                subscription = await sql_session.get(WebhookSubscription, subscription_id)
                if subscription is None:
                    return
                subscription.last_delivery_rejected_code = code
                subscription.last_delivery_rejected_at = (
                    get_current_utc_datetime() if code else None
                )
                sql_session.add(subscription)
                await sql_session.commit()
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "webhook %s: failed to persist delivery outcome", subscription_id
            )

    @staticmethod
    async def _post_once(
        target: OutboundTarget, body: bytes, headers: dict[str, str]
    ) -> None:
        async with _build_session(target) as session:
            async with session.post(
                target.url,
                data=body,
                headers=headers,
                allow_redirects=False,
                timeout=aiohttp.ClientTimeout(total=_DELIVERY_TIMEOUT_SECONDS),
            ) as response:
                # A redirect could point back at a private address after the
                # pinned connection, so it is a policy denial, not a retry.
                reject_redirect(response.status)
                if 200 <= response.status < 300:
                    return
                raise _DeliveryFailed(f"HTTP {response.status}")


class _DeliveryFailed(Exception):
    """Non-2xx response; retried like a connection error."""
