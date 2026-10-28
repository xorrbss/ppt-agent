"""Cause witnesses through the real SVG attempt loop; no provider or browser IO."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from time import perf_counter
from types import SimpleNamespace
import uuid

import pytest

from services.generation_core.authoring.author_slide_svg import SvgAuthorResult
from services.authored_checkpoint_errors import AuthoredStageError
from services.generation_core.drivers.svg import collect
from tests.unit.test_svg_generation_timing_receipt import _ctx, _request
from utils.provider_overload import ProviderOutcomeError


@pytest.fixture
def loop_harness(monkeypatch):
    saved, calls, checkpoints = {}, [], []
    failures, invalid = {}, set()
    provider_errors = []
    ctx, request = _ctx(), _request()
    ctx.count = 20
    ctx.roles, ctx.manifests = ['content'] * 20, [{} for _ in range(20)]

    async def checkpoint(_job, index, *, payload):
        saved[index] = deepcopy(payload)
        checkpoints.append((index, deepcopy(payload)))

    async def provider(*, index, before_dispatch, **kwargs):
        await before_dispatch()
        calls.append((index, deepcopy(saved[index]), kwargs))
        if provider_errors:
            raise provider_errors.pop(0)
        return SvgAuthorResult(f'<svg data-index="{index}"/>', 'raw-response',
            {'attempt_ordinal': kwargs['receipt_attempt_ordinal'], 'validation_error': None})

    def finalize(svg, *, index, **_kwargs):
        if index in invalid:
            invalid.remove(index)
            raise ValueError('svg_common_header_body_intrusion: private text')
        return svg

    async def render(svg, **_kwargs):
        from xml.etree import ElementTree as ET
        index = int(ET.fromstring(svg).get('data-index'))
        kinds = failures.pop(index, [])
        return SimpleNamespace(png=b'png', quality={'passed': not kinds, 'verified': True,
            'failures': [{'kind': kind, 'text': 'private source text'} for kind in kinds]})

    async def sleep(_seconds):
        return None

    monkeypatch.setattr('services.generation_core.authoring.author_slide_svg.author_slide_svg', provider)
    monkeypatch.setattr('services.svg_contract.finalize_svg', finalize)
    monkeypatch.setattr('services.svg_renderer.render_svg_with_metrics', render)
    monkeypatch.setattr('services.generation_core.drivers.svg.author.asyncio.sleep', sleep)
    hooks = SimpleNamespace(checkpoint=checkpoint, async_session_maker=None,
        write_png=lambda _: {'png_sha256': 'test', 'png_path': 'test'})
    state = collect._RunState()

    async def run(payload=None, index=0):
        return await collect._attempt_loop(ctx, request, uuid.UUID(int=1), index, 'slide',
            payload if payload is not None else {'attempts': []}, None, state, hooks)

    return SimpleNamespace(run=run, ctx=ctx, saved=saved, calls=calls, checkpoints=checkpoints,
        failures=failures, invalid=invalid, provider_errors=provider_errors, hooks=hooks)


def assert_causes(harness, expected):
    # The checkpoint already contains both witnesses when the provider starts.
    assert len(harness.calls) == len(expected)
    for (_, dispatched, _), code in zip(harness.calls, expected, strict=True):
        attempt = dispatched['attempts'][-1]
        assert attempt['attempt_cause']['code'] == code
        assert attempt['attempt_cause']['code'] != 'unclassified'
        assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
        assert set(attempt['attempt_cause']) <= {'code', 'evidence_digest'}
        assert 'private' not in str(attempt['attempt_cause'])
        if 'evidence_digest' in attempt['attempt_cause']:
            assert len(attempt['attempt_cause']['evidence_digest']) == 71
    for payload in harness.saved.values():
        for attempt in payload['attempts']:
            if attempt.get('kind') == 'provider':
                assert attempt['attempt_cause']['code']
                assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']


def test_twenty_page_deck_initial_and_static_correction(loop_harness, record_property):
    h = loop_harness
    h.failures[5] = ['required_text_missing']

    async def scenario():
        for index in range(20):
            await h.run(index=index)

    started = perf_counter()
    asyncio.run(scenario())
    record_property('deck_wall_seconds', round(perf_counter() - started, 6))
    record_property('provider_calls', len(h.calls))
    assert_causes(h, ['initial'] * 6 + ['static_quality:required_text_missing'] + ['initial'] * 14)
    assert h.saved[5]['model_attempt_count'] == 2


def test_header_validation_correction(loop_harness):
    h = loop_harness
    h.invalid.add(0)
    asyncio.run(h.run())
    assert_causes(h, ['initial', 'header_repair:svg_common_header_body_intrusion'])


@pytest.mark.parametrize('reason', ['public_provider_overload', 'public_provider_call_timeout'])
def test_transport_retry(loop_harness, reason):
    h = loop_harness
    h.provider_errors.append(ProviderOutcomeError(reason, overload=True, recovery_allowed=True))
    asyncio.run(h.run())
    assert_causes(h, ['initial', 'provider_retry:' + reason])
    assert h.saved[0]['model_attempt_count'] == 1
    assert len(h.saved[0]['provider_failures']) == 1


def test_visual_advisory_page_five_mirrors_live7(loop_harness):
    h = loop_harness

    async def scenario():
        payload, _ = await h.run(index=4)
        assert payload['quality']['failures'] == []
        # The state produced by recover_deck for LIVE-7: a valid first drawing
        # is redesigned for information_structure, with no static failures.
        payload.update(svg_content=None, last_valid_svg=payload['svg_content'],
            correction_mode='design_rewrite', quality_recovery={'status': 'repair_pending',
                'decision': {'repair_scope': 'design_rewrite',
                    'target_axes': ['information_structure'],
                    'issues': [{'axis': 'information_structure', 'detail': 'private evidence'}]}})
        await h.run(payload, index=4)

    asyncio.run(scenario())
    assert_causes(h, ['initial', 'visual_advisory:information_structure'])
    assert all(attempt['quality']['failures'] == [] for attempt in h.saved[4]['attempts'])


def test_timeout_repurchase_uses_existing_fence(loop_harness):
    h = loop_harness
    payload = {'attempts': [], 'recovery_revision': 'svg-provider-timeout-repurchase/v1',
        'retained_provider_attempt_fence': {'consumed_attempt_count': 1, 'previous_ordinal': 1}}
    asyncio.run(h.run(payload))
    assert_causes(h, ['timeout_repurchase'])
    assert h.saved[0]['model_attempt_count'] == 2


def test_second_attempt_without_cause_records_unclassified_and_continues(loop_harness, caplog):
    h = loop_harness
    payload = {'attempts': [{'kind': 'provider', 'receipt': {'attempt_ordinal': 1}}]}
    asyncio.run(h.run(payload))
    attempt = h.calls[0][1]['attempts'][-1]
    assert attempt['attempt_cause'] == {'code': 'unclassified'}
    assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
    assert h.saved[0]['attempts'][-1]['attempt_cause'] == attempt['attempt_cause']
    assert [r.getMessage() for r in caplog.records if r.name.endswith('svg.author')] == [
        'SVG attempt cause unclassified stage=repair page_index=0']


def test_static_retry_supersedes_stale_provider_failure(loop_harness):
    h = loop_harness
    h.provider_errors.append(ProviderOutcomeError('public_provider_overload',
        overload=True, recovery_allowed=True))
    h.failures[0] = ['text_overflow']
    asyncio.run(h.run())
    assert_causes(h, ['initial', 'provider_retry:public_provider_overload', 'static_quality:text_overflow'])


def test_measured_header_repair(loop_harness):
    h = loop_harness
    h.failures[0] = ['svg_common_header_body_intrusion']
    asyncio.run(h.run())
    assert_causes(h, ['initial', 'header_repair:svg_common_header_body_intrusion'])


def test_visual_target_kind_and_digest(loop_harness):
    h = loop_harness
    payload = {'attempts': [{'kind': 'provider', 'receipt': {'attempt_ordinal': 1}}],
        'quality_recovery': {'status': 'repair_pending', 'decision': {
            'repair_scope': 'design_rewrite', 'target_axes': [],
            'design_targets': [{'kind': 'relation_visibility', 'detail': 'private source text'}]}}}
    asyncio.run(h.run(payload))
    # Historical attempts are intentionally never relabeled.
    assert 'attempt_cause' not in h.saved[0]['attempts'][0]
    attempt = h.calls[0][1]['attempts'][-1]
    assert attempt['attempt_cause']['code'] == 'visual_advisory:relation_visibility'
    assert len(attempt['attempt_cause']['evidence_digest']) == 71
    assert attempt['attempt_cause'] == attempt['receipt']['attempt_cause']


def test_validated_candidate_third_attempt_ceiling(loop_harness, monkeypatch):
    h = loop_harness
    from services.generation_core.drivers.svg import quality_recovery

    async def authority(*_args):
        pass

    h.ctx.quality_recovery_input_binding = 'bound'
    monkeypatch.setattr(quality_recovery, 'ensure_recovery_authority', authority)
    monkeypatch.setattr(quality_recovery, 'reserved_recovery_candidate', lambda *_args: {})
    payload = {'attempts': [{'kind': 'provider', 'receipt': {'attempt_ordinal': i}}
                            for i in (1, 2)], 'quality_recovery_fallback': {'status': 'reserved'}}
    asyncio.run(h.run(payload))
    attempt = h.calls[0][1]['attempts'][-1]
    assert attempt['attempt_cause'] == {'code': 'static_quality:validated_svg_adaptation'}
    assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
    assert len(h.saved[0]['attempts']) == 3 and len(h.calls) == 1
    payload['svg_content'] = None
    with pytest.raises(AuthoredStageError) as exhausted:
        asyncio.run(h.run(payload))
    assert exhausted.value.code == 'svg_repair_budget_exhausted'
    assert len(h.calls) == 1


@pytest.mark.parametrize('invalid', [{}, {'code': ''}, {'code': 'initial'},
    {'code': 'visual_advisory:free text'}, {'code': 'provider_retry:overload', 'prompt': 'private'}])
def test_invalid_second_cause_records_unclassified_and_continues(loop_harness, invalid, caplog):
    h = loop_harness
    payload = {'attempts': [{'kind': 'provider', 'receipt': {'attempt_ordinal': 1}}],
               'next_attempt_cause': invalid}
    asyncio.run(h.run(payload))
    attempt = h.calls[0][1]['attempts'][-1]
    assert attempt['attempt_cause'] == {'code': 'unclassified'}
    assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
    assert [r.getMessage() for r in caplog.records if r.name.endswith('svg.author')] == [
        'SVG attempt cause unclassified stage=repair page_index=0']


def test_current_visual_transition_supersedes_old_static_quality(loop_harness):
    h = loop_harness
    payload = {'attempts': [{'kind': 'provider', 'receipt': {'attempt_ordinal': 1},
                'quality': {'failures': [{'kind': 'text_overflow'}]}}],
               'next_attempt_cause': {'code': 'visual_advisory:information_structure'}}
    asyncio.run(h.run(payload))
    attempt = h.calls[0][1]['attempts'][-1]
    assert attempt['attempt_cause'] == {'code': 'visual_advisory:information_structure'}
    assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
    assert 'next_attempt_cause' not in h.saved[0]


@pytest.mark.parametrize('branch', ['source', 'raster'])
def test_source_and_raster_share_dispatch_boundary(loop_harness, monkeypatch, branch):
    h = loop_harness
    h.ctx.source_reflow = branch == 'source'
    h.ctx.raster_reconstruction = branch == 'raster'
    payload = {'attempts': []}
    seen_previous = []
    if branch == 'source':
        h.ctx.snapshot = {'source_authoring_contract': {'version': 'svg-source-whole-page/v1'}}
        h.ctx.source_units = [{}]
        payload = {'attempts': [{'kind': 'provider', 'raw_response': 'previous-raw',
            'receipt': {'attempt_ordinal': 1}, 'quality': {'failures': [{'kind': 'text_overflow'}]}}],
            'repair_hint': 'existing-hint'}

        def correction(_unit, _slots, previous, hint, **_kwargs):
            seen_previous.append(previous)
            return hint

        monkeypatch.setattr('services.generation_core.authoring.author_source_svg_layout.source_binding_correction_hint', correction)
    else:
        h.ctx.snapshot = {'composition_mode': 'free-svg'}
        h.ctx.raster_analysis = SimpleNamespace(pages=[SimpleNamespace(render_mode='redesign')])

    async def provider(*_args, before_dispatch, **_kwargs):
        await before_dispatch()
        h.calls.append((0, deepcopy(h.saved[0]), {}))
        return SvgAuthorResult('<svg/>', 'new-raw', {'attempt_ordinal': 2 if branch == 'source' else 1})

    async def rendered(**kwargs):
        return kwargs['payload'], b'png'

    module = f'services.generation_core.authoring.author_{branch}_svg_layout.author_{branch}_svg_layout'
    monkeypatch.setattr(module, provider)
    monkeypatch.setattr(collect.render, 'attempt_render', rendered)
    asyncio.run(h.run(payload))
    attempt = h.calls[0][1]['attempts'][-1]
    expected = 'static_quality:text_overflow' if branch == 'source' else 'initial'
    assert attempt['attempt_cause'] == {'code': expected}
    assert attempt['receipt']['attempt_cause'] == attempt['attempt_cause']
    if branch == 'source':
        assert seen_previous == ['previous-raw']
