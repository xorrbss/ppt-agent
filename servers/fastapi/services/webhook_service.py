import asyncio
import hashlib
import hmac
import json
import logging

import aiohttp
from sqlmodel import select
from enums.webhook_event import WebhookEvent
from models.sql.webhook_subscription import WebhookSubscription
from services.database import get_async_session

LOGGER = logging.getLogger(__name__)

# Retry a failed delivery (connection error OR non-2xx) with exponential backoff.
_MAX_ATTEMPTS = 3
_BASE_BACKOFF_SECONDS = 0.5


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
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        subscription.url,
                        data=body,
                        headers=headers,
                    ) as response:
                        if 200 <= response.status < 300:
                            return
                        error_detail = f"HTTP {response.status}"
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
