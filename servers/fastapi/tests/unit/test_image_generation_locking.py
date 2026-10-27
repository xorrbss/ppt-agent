import asyncio
from contextlib import asynccontextmanager

import pytest

from models.image_prompt import ImagePrompt
from services.image_generation_service import ImageGenerationService
from utils.get_env import is_parallel_image_generation_enabled


def test_image_request_identity_binds_page_and_prompt_and_replays_stably(monkeypatch):
    from services import image_generation_service as image_mod

    monkeypatch.setenv("ENABLE_PARALLEL_IMAGE_GENERATION", "true")
    slots = []

    @asynccontextmanager
    async def admission(*, semantic_slot, **_kwargs):
        slots.append(semantic_slot)
        await asyncio.sleep(0)
        yield

    async def provider(_prompt, _output_directory):
        async with image_mod._metered_image_provider_call(
            provider="fake", model="test", semantic_slot="image.primary.codex",
        ):
            return "https://example.com/image.png"

    monkeypatch.setattr(image_mod, "provider_call_admission", admission)
    service = object.__new__(ImageGenerationService)
    service.output_directory = "/tmp"
    service.is_image_generation_disabled = False
    service.is_stock_provider_selected = lambda: False
    service.image_gen_func = provider

    async def generate(page, prompt):
        with image_mod.image_request_scope(f"scene-{page}"):
            return await service.generate_image(ImagePrompt(prompt=prompt))

    async def scenario():
        await asyncio.gather(generate(0, "same scene"), generate(1, "same scene"))
        await generate(0, "same scene")
        await generate(0, "changed scene")

    asyncio.run(scenario())
    assert slots[0] != slots[1] and slots[0] == slots[2] and slots[0] != slots[3]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),
        ("true", True),
        ("1", True),
        ("false", False),
        ("0", False),
    ],
)
def test_parallel_image_generation_env(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("ENABLE_PARALLEL_IMAGE_GENERATION", raising=False)
    else:
        monkeypatch.setenv("ENABLE_PARALLEL_IMAGE_GENERATION", raw)

    assert is_parallel_image_generation_enabled() is expected


@pytest.mark.parametrize(
    ("parallel_env", "expected_max_active"),
    [("true", 2), ("false", 1)],
)
def test_image_generation_lock_is_shared_across_service_instances(
    monkeypatch, parallel_env: str, expected_max_active: int
):
    monkeypatch.setenv("ENABLE_PARALLEL_IMAGE_GENERATION", parallel_env)
    # F2 replaced the process-wide lock with a semaphore sized by
    # IMAGE_GENERATION_CONCURRENCY (default 3). Pin it to 1 here so the
    # "not parallel" branch still means strict serialization, matching this
    # test's original lock-based expectation; concurrency > 1 behaviour is
    # covered by test_image_generation_concurrency.py.
    monkeypatch.setenv("IMAGE_GENERATION_CONCURRENCY", "1")
    active_requests = 0
    max_active_requests = 0

    async def provider(_prompt: str, _output_directory: str) -> str:
        nonlocal active_requests, max_active_requests
        active_requests += 1
        max_active_requests = max(max_active_requests, active_requests)
        await asyncio.sleep(0.01)
        active_requests -= 1
        return "https://example.com/generated.png"

    def service() -> ImageGenerationService:
        instance = object.__new__(ImageGenerationService)
        instance.output_directory = "/tmp"
        instance.is_image_generation_disabled = False
        instance.is_stock_provider_selected = lambda: False
        instance.image_gen_func = provider
        return instance

    async def generate_from_independent_services():
        await asyncio.gather(
            service().generate_image(ImagePrompt(prompt="presentation image")),
            service().generate_image(ImagePrompt(prompt="assistant image")),
        )

    asyncio.run(generate_from_independent_services())

    assert max_active_requests == expected_max_active


def test_stock_provider_keeps_single_argument_calling_convention(monkeypatch):
    monkeypatch.setenv("ENABLE_PARALLEL_IMAGE_GENERATION", "false")
    received_prompts = []

    async def provider(prompt: str) -> str:
        received_prompts.append(prompt)
        return "https://example.com/stock.png"

    service = object.__new__(ImageGenerationService)
    service.output_directory = "/tmp"
    service.is_image_generation_disabled = False
    service.is_stock_provider_selected = lambda: True
    service.image_gen_func = provider

    result = asyncio.run(
        service.generate_image(
            ImagePrompt(prompt="city skyline", theme_prompt="editorial illustration")
        )
    )

    assert result == "https://example.com/stock.png"
    assert received_prompts == ["city skyline"]


def test_image_provider_calls_carry_a_bounded_deadline(monkeypatch):
    """One hung picture may not hold a deck past its generation deadline.

    Image calls run behind a process-wide lock and the OpenAI SDK's own
    default deadline is ten minutes, so an unbounded call stalls every other
    picture too. A measured worker-lane run lost 27 minutes of authoring wall
    this way while only 7 provider calls were made.
    """

    from utils.get_env import get_image_generation_timeout_seconds

    monkeypatch.delenv("IMAGE_GENERATION_TIMEOUT_SECONDS", raising=False)
    assert get_image_generation_timeout_seconds() == 180.0

    monkeypatch.setenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "45")
    assert get_image_generation_timeout_seconds() == 45.0

    # The bounds keep an operator from reintroducing the ten-minute stall or
    # setting a deadline no real image call could meet.
    monkeypatch.setenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "5000")
    assert get_image_generation_timeout_seconds() == 600.0
    monkeypatch.setenv("IMAGE_GENERATION_TIMEOUT_SECONDS", "0")
    assert get_image_generation_timeout_seconds() == 5.0


def test_every_openai_image_client_is_constructed_with_the_deadline():
    """The deadline is only real if every image client carries it."""

    import inspect

    from services import image_generation_service
    from services.authored_icon_pack import provider as icon_provider

    for module in (image_generation_service, icon_provider):
        source = inspect.getsource(module)
        constructions = source.count("AsyncOpenAI(")
        deadlines = source.count("timeout=get_image_generation_timeout_seconds(")
        assert constructions == deadlines, (
            f"{module.__name__} constructs {constructions} image client(s) "
            f"but only {deadlines} carry the image deadline"
        )
