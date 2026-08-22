import json
from pathlib import Path

from canonical_json import canonical_json, canonical_sha256


FIXTURE_DIR = Path(__file__).parent


def test_canonical_fixture_matches_expected_digest():
    value = json.loads((FIXTURE_DIR / "canonical-fixture.json").read_text(encoding="utf-8"))
    expected = json.loads((FIXTURE_DIR / "canonical-fixture.expected.json").read_text(encoding="utf-8"))
    assert canonical_json(value) == expected["canonicalJson"]
    assert canonical_sha256(value) == expected["sha256"]
