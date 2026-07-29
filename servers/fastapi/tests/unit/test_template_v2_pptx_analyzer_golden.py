import json
from pathlib import Path

from templates.v2.pptx.analyzer import analyze_ooxml_candidates


CORPUS_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "template_v2"
    / "pptx-analyzer-golden-v1.json"
)


def test_versioned_analyzer_golden_corpus():
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    assert corpus["corpus_version"] == 1
    assert corpus["cases"]
    for case in corpus["cases"]:
        analysis = analyze_ooxml_candidates(case["input"])
        assert analysis.contract_version == 1, case["name"]
        assert analysis.candidate.sha256 == case["expected"]["candidate_sha256"], (
            case["name"]
        )
        assert analysis.summary.model_dump(mode="json") == case["expected"]["summary"], (
            case["name"]
        )
