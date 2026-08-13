"""Published-master pin validation for generation operations."""

from models.authored_vnext.deck import MasterDigestPin
from models.authored_vnext.master.operations import MasterRevisionResponse
from services.authored_vnext.master.digests import canonical_digest

from .errors import PublishedMasterRequiredError, StaleMasterError
from .repository import GenerationRepository


def validate_master_pin(
    repository: GenerationRepository,
    pin: MasterDigestPin,
) -> MasterRevisionResponse:
    master = repository.get_master(pin.master_id, pin.master_revision)
    head = repository.get_master_head(pin.master_id)
    if master.status != "published":
        raise PublishedMasterRequiredError()
    if head.revision != pin.master_revision or head.digest != pin.master_digest:
        raise StaleMasterError("published master head changed")
    contract = master.contract
    layout_digest = canonical_digest(
        contract.layouts.model_dump(mode="json", by_alias=True)
    )
    if (
        master.digest != pin.master_digest
        or layout_digest != pin.layout_registry_digest
        or contract.font_manifest.digest != pin.font_manifest_digest
    ):
        raise StaleMasterError("published master checkpoint mismatch")
    return master


__all__ = ["validate_master_pin"]
