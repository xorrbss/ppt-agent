import asyncio
import hashlib
import hmac
import json

import aiohttp
from sqlmodel import select
from enums.webhook_event import WebhookEvent
from models.sql.webhook_subscription import WebhookSubscription
from services.database import get_async_session


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

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    subscription.url,
                    data=body,
                    headers=headers,
                ) as _:
                    pass

        except Exception as e:
            print(f"Error sending request to webhook {subscription.id}: {e}")
            pass
