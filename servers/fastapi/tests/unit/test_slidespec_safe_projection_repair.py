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
