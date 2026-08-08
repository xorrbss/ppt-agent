"""Safety proofs for advanced deterministic source-projection repairs."""

from models.slidespec_v1 import RepairPlan
from services.slidespec_repair import (
    build_deterministic_cascade,
    build_deterministic_cascade_report,
)


_HASH = "sha256:" + "a" * 64
_CONTENT = "sha256:" + "b" * 64


def _context(**features):
    return {
        "slideId": "slide-advanced",
        "baseSpecRevision": 9,
        "baseSpecHash": _HASH,
        "expectedContentDigest": _CONTENT,
        "diagnosticRefs": ["dom:advanced"],
        **features,
    }


def _envelope(*operations, **values):
    return {
        "locked": False,
        "allowedOperations": list(operations),
        **values,
    }


def _chrome(**values):
    return {"source": "chrome", "telemetryComplete": True, **values}


def _roundtrip(*properties):
    return {"supported": True, "verifiedProperties": list(properties)}


def test_advanced_projection_builds_only_from_explicit_safety_proofs():
    context = _context(
        connectorRoute={
            "target": "sem:connector",
            "currentRoute": {"points": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.9}]},
            "route": {"points": [
                {"x": 0.1, "y": 0.1},
                {"x": 0.5, "y": 0.2},
                {"x": 0.9, "y": 0.9},
            ]},
            "mutationEnvelope": _envelope("rerouteConnector"),
            "measurement": _chrome(
                endpointAnchors={
                    "startResolved": True,
                    "endResolved": True,
                    "maxErrorPx": 0.25,
                },
                obstacleFree=True,
                minimumClearancePx=2,
            ),
            "roundTrip": _roundtrip("connector-route"),
        },
        glyphFit={
            "target": "sem:copy",
            "currentScale": 1,
            "minimumScale": 0.85,
            "mutationEnvelope": _envelope(
                "setFontScale", minFontScale=0.85, maxFontShrink=0.15
            ),
            "measurement": _chrome(
                kind="glyph-run",
                fontsReady=True,
                fontSizePx=20,
                measuredWidthPx=120,
                measuredHeightPx=20,
                availableWidthPx=108,
                availableHeightPx=20,
            ),
            "roundTrip": _roundtrip("font-size"),
        },
        imageCrop={
            "target": "sem:photo",
            "currentFit": "contain",
            "currentFocalPoint": {"x": 0.5, "y": 0.5},
            "fit": "cover",
            "focalPoint": {"x": 0.5, "y": 0.5},
            "mutationEnvelope": _envelope("setImageFit", "setCropFocalPoint"),
            "measurement": _chrome(
                decoded=True,
                naturalWidthPx=1600,
                naturalHeightPx=900,
                boxWidthPx=400,
                boxHeightPx=400,
                protectedRegion={"x": 0.4, "y": 0.3, "width": 0.2, "height": 0.3},
            ),
            "roundTrip": _roundtrip("object-fit", "object-position"),
        },
        staticTranslation={
            "target": "sem:badge",
            "currentBounds": {"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            "delta": {"x": 0.02, "y": -0.01},
            "mutationEnvelope": _envelope(
                "translateWithinParent", maxTranslation=0.04
            ),
            "measurement": _chrome(
                position="static",
                layoutIsolation=True,
                transformComposable=True,
                flowImpactPx=0,
                maxSiblingDeltaPx=0,
                candidateBoundsInParent={
                    "x": 0.12,
                    "y": 0.09,
                    "width": 0.1,
                    "height": 0.1,
                },
            ),
            "roundTrip": _roundtrip("transform"),
        },
    )

    report = build_deterministic_cascade_report(context)

    assert report.rejections == ()
    assert [tuple(operation.op for operation in plan.operations) for plan in report.plans] == [
        ("rerouteConnector",),
        ("setFontScale",),
        ("setImageFit", "setCropFocalPoint"),
        ("translateWithinParent",),
    ]
    assert report.plans[1].operations[0].scale == 0.9


def test_owned_text_separation_probe_emits_exact_bounded_font_scale():
    context = _context(
        glyphFit={
            "target": "lay:h3",
            "currentScale": 1.0,
            "candidateScale": 0.9,
            "minimumScale": 0.9,
            "mutationEnvelope": _envelope(
                "setFontScale", minFontScale=0.9, maxFontShrink=0.1
            ),
            "measurement": _chrome(
                kind="owned-text-separation",
                fontsReady=True,
                fontSizePx=16,
                measuredWidthPx=149.234375,
                measuredHeightPx=24,
                availableWidthPx=135.390625,
                availableHeightPx=24,
                targetTextBoundsPx={
                    "x": 1027,
                    "y": 430,
                    "width": 149.234375,
                    "height": 24,
                },
                occluderTextBoundsPx={
                    "x": 1162.390625,
                    "y": 436,
                    "width": 41.609375,
                    "height": 15,
                },
                candidateSeparatesTextPaint=True,
            ),
            "roundTrip": _roundtrip("font-size"),
        }
    )

    report = build_deterministic_cascade_report(context)

    assert report.rejections == ()
    assert len(report.plans) == 1
    assert report.plans[0].operations[0].op == "setFontScale"
    assert report.plans[0].operations[0].scale == 0.9


def test_owned_text_separation_probe_rejects_scale_that_leaves_overlap():
    context = _context(
        glyphFit={
            "target": "lay:h3",
            "currentScale": 1.0,
            "candidateScale": 0.95,
            "minimumScale": 0.9,
            "mutationEnvelope": _envelope(
                "setFontScale", minFontScale=0.9, maxFontShrink=0.1
            ),
            "measurement": _chrome(
                kind="owned-text-separation",
                fontsReady=True,
                measuredWidthPx=149.234375,
                measuredHeightPx=24,
                availableWidthPx=135.390625,
                availableHeightPx=24,
                targetTextBoundsPx={
                    "x": 1027,
                    "y": 430,
                    "width": 149.234375,
                    "height": 24,
                },
                occluderTextBoundsPx={
                    "x": 1162.390625,
                    "y": 436,
                    "width": 41.609375,
                    "height": 15,
                },
                candidateSeparatesTextPaint=True,
            ),
            "roundTrip": _roundtrip("font-size"),
        }
    )

    report = build_deterministic_cascade_report(context)

    assert report.plans == ()
    assert report.rejections[0].reason_code == (
        "repair.glyph.separation_probe_unproven"
    )


def test_advanced_projection_is_deterministic_idempotent_and_schema_roundtrips():
    context = _context(glyphFit={
        "target": "sem:copy",
        "currentScale": 1,
        "minimumScale": 0.85,
        "mutationEnvelope": _envelope(
            "setFontScale", minFontScale=0.85, maxFontShrink=0.15
        ),
        "measurement": _chrome(
            kind="range-bounds",
            fontsReady=True,
            fontSizePx=16,
            measuredWidthPx=200,
            measuredHeightPx=32,
            availableWidthPx=180,
            availableHeightPx=32,
        ),
        "roundTrip": _roundtrip("font-size"),
    })

    first = build_deterministic_cascade(context)
    second = build_deterministic_cascade(context)
    encoded = first[0].model_dump(mode="json", by_alias=True)
    decoded = RepairPlan.model_validate(encoded)

    assert first == second
    assert decoded == first[0]
    assert decoded.model_dump(mode="json", by_alias=True) == encoded


def test_advanced_projection_fails_closed_with_structured_reasons():
    report = build_deterministic_cascade_report(_context(
        connectorRoute={
            "target": "sem:connector",
            "route": {"points": [{"x": 0, "y": 0}, {"x": 1, "y": 1}]},
            "mutationEnvelope": _envelope("rerouteConnector"),
            "measurement": _chrome(
                endpointAnchors={
                    "startResolved": True,
                    "endResolved": False,
                    "maxErrorPx": 0,
                },
                obstacleFree=True,
                minimumClearancePx=3,
            ),
            "roundTrip": _roundtrip("connector-route"),
        },
        glyphFit={
            "target": "sem:copy",
            "currentScale": 1,
            "minimumScale": 0.85,
            "mutationEnvelope": _envelope("setFontScale", maxFontShrink=0.15),
            "measurement": _chrome(
                kind="glyph-run",
                fontsReady=True,
                measuredWidthPx=200,
                measuredHeightPx=20,
                availableWidthPx=120,
                availableHeightPx=20,
            ),
            "roundTrip": _roundtrip("font-size"),
        },
        imageCrop={
            "target": "sem:photo",
            "fit": "cover",
            "focalPoint": {"x": 0.5, "y": 0.5},
            "mutationEnvelope": _envelope("setImageFit", "setCropFocalPoint"),
            "measurement": _chrome(
                decoded=True,
                naturalWidthPx=1600,
                naturalHeightPx=900,
                boxWidthPx=400,
                boxHeightPx=400,
                protectedRegion={"x": 0, "y": 0.2, "width": 0.2, "height": 0.2},
            ),
            "roundTrip": _roundtrip("object-fit", "object-position"),
        },
        staticTranslation={
            "target": "sem:badge",
            "delta": {"x": 0.01, "y": 0},
            "mutationEnvelope": _envelope("translateWithinParent"),
            "measurement": _chrome(
                position="static",
                layoutIsolation=False,
                transformComposable=True,
                flowImpactPx=0,
                maxSiblingDeltaPx=0,
                candidateBoundsInParent={"x": 0.1, "y": 0.1, "width": 0.1, "height": 0.1},
            ),
            "roundTrip": _roundtrip("transform"),
        },
    ))

    assert report.plans == ()
    assert {(item.feature, item.reason_code) for item in report.rejections} == {
        ("connector_reroute", "repair.connector.anchor_unproven"),
        ("glyph_shrink_to_fit", "repair.glyph.required_shrink_outside_envelope"),
        ("image_crop_focal_point", "repair.image.protected_region_visibility_unproven"),
        ("isolated_static_translation", "repair.static_translation.flow_isolation_unproven"),
    }


def test_missing_roundtrip_property_or_mutation_envelope_never_produces_plan():
    report = build_deterministic_cascade_report(_context(
        glyphFit={
            "target": "sem:copy",
            "currentScale": 1,
            "measurement": _chrome(
                kind="glyph", fontsReady=True, measuredWidthPx=110,
                measuredHeightPx=20, availableWidthPx=100, availableHeightPx=20,
            ),
            "roundTrip": _roundtrip("line-height"),
        },
    ))

    assert report.plans == ()
    assert report.rejections[0].reason_code == "repair.mutation_envelope.missing_or_locked"


def test_glyph_fit_property_stays_inside_envelope_across_measured_overflows():
    for measured_width in range(101, 118):
        context = _context(glyphFit={
            "target": "sem:copy",
            "currentScale": 1,
            "minimumScale": 0.85,
            "mutationEnvelope": _envelope(
                "setFontScale", minFontScale=0.85, maxFontShrink=0.15
            ),
            "measurement": _chrome(
                kind="glyph-run",
                fontsReady=True,
                measuredWidthPx=measured_width,
                measuredHeightPx=20,
                availableWidthPx=100,
                availableHeightPx=20,
            ),
            "roundTrip": _roundtrip("font-size"),
        })

        first = build_deterministic_cascade_report(context)
        second = build_deterministic_cascade_report(context)

        assert first == second
        if measured_width <= 117:  # required scale is at least the 0.85 floor
            scale = first.plans[0].operations[0].scale
            assert 0.85 <= scale < 1
            assert scale == round(100 / measured_width, 6)


def test_malformed_advanced_geometry_is_rejected_before_plan_validation():
    report = build_deterministic_cascade_report(_context(
        connectorRoute={
            "target": "sem:connector",
            "route": {"points": [{"x": -0.1, "y": 0}, {"x": 1, "y": 1}]},
            "mutationEnvelope": _envelope("rerouteConnector"),
            "measurement": _chrome(
                endpointAnchors={
                    "startResolved": True, "endResolved": True, "maxErrorPx": 0
                },
                obstacleFree=True,
                minimumClearancePx=2,
            ),
            "roundTrip": _roundtrip("connector-route"),
        },
        staticTranslation={
            "target": "sem:badge",
            "currentBounds": {"x": 0.95, "y": 0.1, "width": 0.1, "height": 0.1},
            "delta": {"x": 0.01, "y": 0},
            "mutationEnvelope": _envelope("translateWithinParent"),
            "measurement": _chrome(
                position="static", layoutIsolation=True, transformComposable=True,
                flowImpactPx=0, maxSiblingDeltaPx=0,
                candidateBoundsInParent={"x": 0.5, "y": 0.1, "width": 0.1, "height": 0.1},
            ),
            "roundTrip": _roundtrip("transform"),
        },
    ))

    assert report.plans == ()
    assert {item.reason_code for item in report.rejections} == {
        "repair.connector.route_invalid",
        "repair.static_translation.precondition_unproven",
    }


def test_static_translation_uses_chrome_containment_interval_not_fixed_ratio_cap():
    context = _context(staticTranslation={
        "target": "sem:footer",
        "currentBounds": {"x": 0.04, "y": 0.99, "width": 0.92, "height": 0.01},
        "delta": {"x": 0, "y": -0.12},
        "mutationEnvelope": _envelope(
            "translateWithinParent", maxTranslation=0.08
        ),
        "measurement": _chrome(
            position="static",
            layoutIsolation=True,
            transformComposable=True,
            stackingStable=True,
            flowImpactPx=0,
            maxSiblingDeltaPx=0,
            targetBoundsPx={"x": 48, "y": 714, "width": 1184, "height": 20},
            parentBoundsPx={"x": 0, "y": 0, "width": 1280, "height": 720},
            safeTranslationIntervalPx={
                "minX": -48,
                "maxX": 48,
                "minY": -714,
                "maxY": -14,
            },
            candidateBoundsInParent={
                "x": 0.0375,
                "y": 0.8722222222,
                "width": 0.925,
                "height": 0.0277777778,
            },
        ),
        "roundTrip": _roundtrip("transform"),
    })

    report = build_deterministic_cascade_report(context)

    assert report.rejections == ()
    assert report.plans[0].operations[0].op == "translateWithinParent"


def test_static_translation_can_leave_visible_overflow_parent_inside_chrome_interval():
    context = _context(staticTranslation={
        "target": "sem:footer-copy",
        "currentBounds": {"x": 0.04, "y": 0.98, "width": 0.2, "height": 0.018},
        "delta": {"x": 0, "y": -0.02},
        "mutationEnvelope": _envelope("translateWithinParent", maxTranslation=0.08),
        "measurement": _chrome(
            position="static",
            layoutIsolation=True,
            transformComposable=True,
            stackingStable=True,
            flowImpactPx=0,
            maxSiblingDeltaPx=0,
            targetBoundsPx={"x": 48, "y": 721, "width": 142, "height": 13},
            parentBoundsPx={"x": 48, "y": 714, "width": 1184, "height": 20},
            safeTranslationIntervalPx={
                "minX": -48,
                "maxX": 1090,
                "minY": -721,
                "maxY": -14,
            },
            candidateBoundsInParent={
                "x": 0,
                "y": -0.37,
                "width": 0.12,
                "height": 0.65,
            },
        ),
        "roundTrip": _roundtrip("transform"),
    })

    report = build_deterministic_cascade_report(context)

    assert report.rejections == ()
    assert report.plans[0].operations[0].op == "translateWithinParent"


def test_static_translation_and_glyph_non_applicability_are_precisely_classified():
    report = build_deterministic_cascade_report(_context(
        glyphFit={
            "target": "sem:footer-label",
            "currentScale": 1,
            "minimumScale": 0.85,
            "mutationEnvelope": _envelope("setFontScale", maxFontShrink=0.15),
            "measurement": _chrome(
                kind="range-bounds",
                fontsReady=True,
                measuredWidthPx=80,
                measuredHeightPx=13,
                availableWidthPx=80,
                availableHeightPx=13,
            ),
            "roundTrip": _roundtrip("font-size"),
        },
        staticTranslation={
            "target": "sem:wrapper",
            "currentBounds": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            "delta": {"x": 0, "y": 0},
            "mutationEnvelope": _envelope("translateWithinParent"),
            "measurement": _chrome(
                position="static",
                layoutIsolation=True,
                transformComposable=True,
                stackingStable=True,
                flowImpactPx=0,
                maxSiblingDeltaPx=0,
                candidateBoundsInParent={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
            ),
            "roundTrip": _roundtrip("transform"),
        },
    ))

    assert report.plans == ()
    assert {item.reason_code for item in report.rejections} == {
        "repair.glyph.size_reduction_not_applicable",
        "repair.static_translation.zero_delta",
    }
