import json
from unittest.mock import Mock
import uuid

import pytest

from services.public_operation_correlation import (
    build_public_operation_correlation,
    log_public_operation_correlation,
)
from services.workspace_metrics import WORKSPACE_METRICS


def test_public_operation_correlation_joins_logs_without_metric_identity_labels():
    operation_id = uuid.UUID("00000000-0000-0000-0000-000000000041")
    logger = Mock()
    WORKSPACE_METRICS.reset()

    event = log_public_operation_correlation(
        operation_id=operation_id,
        component="render",
        action="completed",
        outcome="succeeded",
        logger=logger,
    )

    assert event == {
        "schema_version": 1,
        "pipeline": "public_operation_correlation",
        "operation_id": str(operation_id),
        "component": "render",
        "action": "completed",
        "outcome": "succeeded",
    }
    logger.info.assert_called_once_with(
        "public_operation_correlation %s",
        json.dumps(event, sort_keys=True, separators=(",", ":")),
    )
    metrics = WORKSPACE_METRICS.prometheus_text()
    assert (
        'presenton_public_operation_correlation_total{component="render",action="completed",outcome="succeeded"} 1'
        in metrics
    )
    assert str(operation_id) not in metrics
    WORKSPACE_METRICS.reset()


@pytest.mark.parametrize(
    "operation_id",
    ["prompt contains secret", "../../private", "not-a-uuid"],
)
def test_public_operation_correlation_rejects_untrusted_operation_id(operation_id):
    with pytest.raises(ValueError, match="public_operation_id_invalid"):
        build_public_operation_correlation(
            operation_id=operation_id,
            component="api",
            action="accepted",
            outcome="succeeded",
        )
