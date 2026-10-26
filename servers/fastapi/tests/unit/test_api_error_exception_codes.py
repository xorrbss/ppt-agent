"""Raw driver attributes never become public error codes."""

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError

from constants.provider_registry import ProviderResolutionError
from models.api_error_model import APIErrorModel
from services.assets.contracts import AssetGatewayError
from services.authored_vnext.deck_ir.director import DeckDirectorError
from services.authored_vnext.integration.worker import WorkerError
from services.document_conversion_service import DocumentConversionError
from services.workspace_service import WorkspaceServiceError
from workers.public_worker_store import PublicWorkerExecutionError


class CodedErrorFromTestModule(Exception):
    code = "test_module_exception"


@pytest.mark.parametrize("error", [
    SQLAlchemyError("driver failed", code="gkpj"),
    SQLAlchemyError("gkpj", code="gkpj"),
    ValueError("provider_failed"),
])
def test_raw_exception_codes_and_text_use_generic_fallback(error):
    error.code = "gkpj"
    assert APIErrorModel.from_exception(error).code == "http_error_500"


def test_raw_attributes_cannot_impersonate_an_application_code():
    error = RuntimeError("driver failed")
    error.code = "svg_source_quality_failed"
    error.retryable = False
    assert APIErrorModel.from_exception(error).code == "http_error_500"


@pytest.mark.parametrize("error", [
    ProviderResolutionError("provider_unavailable", "example"),
    PublicWorkerExecutionError("public_worker_failed"),
])
def test_coded_exceptions_in_other_application_packages_keep_their_codes(error):
    assert APIErrorModel.from_exception(error).code == error.code


def test_test_module_exception_counts_as_application_owned_inside_source_root():
    assert APIErrorModel.from_exception(CodedErrorFromTestModule()).code == "test_module_exception"


@pytest.mark.parametrize("error", [
    AssetGatewayError("asset_not_ready"),
    DeckDirectorError("deck_director_transient_failure"),
    WorkerError("WORKER_UNAVAILABLE"),
    DocumentConversionError("private subprocess output"),
    WorkspaceServiceError("revision_conflict"),
])
def test_application_exception_types_keep_their_declared_codes(error):
    assert APIErrorModel.from_exception(error).code == error.code


@pytest.mark.parametrize("code", [
    "svg_source_quality_failed",
    "public_provider_result_unknown",
    "generation_service_interrupted",
    "public_generation_transition_invalid:rendering:authoring",
    "WORKER_UNAVAILABLE",
])
def test_classified_http_boundary_keeps_stable_failure_codes(code):
    error = HTTPException(500, "Presentation generation failed")
    error.code = code
    assert APIErrorModel.from_exception(error).code == code
    assert APIErrorModel.from_exception(RuntimeError("driver failed"), code=code).code == code
