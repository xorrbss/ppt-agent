"""Runtime aliases for the generated strict OOXML package wire contract."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys


_CONTRACT_PATH = (
    Path(__file__).resolve().parents[8]
    / "contracts"
    / "authored_vnext"
    / "ooxml"
    / "v1"
    / "generated"
    / "ooxml_package.py"
)
_MODULE_NAME = "authored_vnext_ooxml_package_generated"
_spec = spec_from_file_location(_MODULE_NAME, _CONTRACT_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("authored_vnext_ooxml_contract_unavailable")
_generated = module_from_spec(_spec)
sys.modules[_MODULE_NAME] = _generated
_spec.loader.exec_module(_generated)

EditableForkRequest = _generated.EditableForkRequest
ImportPackageReceipt = _generated.ImportPackageReceipt
PackageOperationCommand = _generated.PackageOperationCommand
PackageOperationReceipt = _generated.PackageOperationReceipt
PackageReceiptReadBack = _generated.PackageReceiptReadBack
PackageRollbackRequest = _generated.PackageRollbackRequest
PackageVersionReceipt = _generated.PackageVersionReceipt

__all__ = [
    "EditableForkRequest",
    "ImportPackageReceipt",
    "PackageOperationCommand",
    "PackageOperationReceipt",
    "PackageReceiptReadBack",
    "PackageRollbackRequest",
    "PackageVersionReceipt",
]
