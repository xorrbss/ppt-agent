"""Build and score the Composer blind rating pack (design bar, 7b).

Assembles a ready-to-use blind rating package for the three named tiers in
``docs/generation-quality-root-cause-and-composer-architecture-20260904.md``
section 7b:

- floor: the 09-02 source-native output (``preserve-43.pdf``, 43 pages)
- parity: the source deck itself (deck-43 PPTX, 43 pages)
- candidate: the Composer's own offline-eval output for the same 43-page
  source (``artifacts/composer-offline-eval/r12-43`` by default)
- target: the Hyundai SI reference deck, rated separately as a craft
  calibration set (10 pages, different content -- never page-aligned
  against the other three)

Floor/parity/candidate are anonymised to A/B/C (page order and page-to-page
alignment preserved) with the real mapping written to a separate
``key.json`` that must not be handed to raters. The target deck is
anonymised to D and rated on its own 10-page sheet.

Run from ``servers/fastapi``::

    .venv\\Scripts\\python.exe scripts/composer_blind_rating_pack.py build
    .venv\\Scripts\\python.exe scripts/composer_blind_rating_pack.py score \\
        --main-sheet <filled_main.csv> --target-sheet <filled_target.csv>

``build`` does not start the FastAPI app, database, or any worker; it only
rasterizes PDFs (pypdfium2) and shells out to a headless LibreOffice once
for the parity PPTX. ``score`` is pure CSV/JSON processing.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

FASTAPI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = FASTAPI_ROOT.parents[1]
if str(FASTAPI_ROOT) not in sys.path:
    sys.path.insert(0, str(FASTAPI_ROOT))

from scripts.composer_blind_rating_pack_prefilter import run_style_prefilter  # noqa: E402
from scripts.composer_blind_rating_pack_render import (  # noqa: E402
    TARGET_SIZE,
    convert_pptx_to_pdf,
    copy_png_set,
    render_pdf_to_pngs,
)
from scripts.composer_blind_rating_pack_score import (  # noqa: E402
    evaluate,
    format_verdict,
    read_sheet,
    write_sheet,
)

DEFAULT_OUT = REPO_ROOT / "artifacts" / "composer-blind-rating-20260904"
DEFAULT_FLOOR_PDF = REPO_ROOT / "output" / "candidate-54892244-dual-lane" / "preserve-43.pdf"
DEFAULT_PARITY_PPTX = (
    REPO_ROOT
    / "app_data"
    / "workspace_assets"
    / "d74ac154-738d-4c58-bade-daf2f8d703d3"
    / "e36768ca-7569-4989-828c-37135a4dcdb5"
    / "32b098831a84774dfc35820fed6e21e715259e2fc4e72e586a63c3fc650f1188.pptx"
)
DEFAULT_CANDIDATE_DIR = REPO_ROOT / "artifacts" / "composer-offline-eval" / "r12-43"
DEFAULT_TARGET_DIR = REPO_ROOT / "output" / "hyundai-reference-90plus-20260901" / "reference-first10"
DEFAULT_STYLE_SCRIPT = REPO_ROOT / "output" / "hyundai-reference-90plus-20260901" / "evaluate_visual_system.py"

# Fixed so a rebuild reproduces the same A/B/C mapping; not a secret, the
# pack is only "blind" because key.json is withheld from raters, not because
# the mapping is unguessable.
SHUFFLE_SEED = "composer-blind-rating-20260904"

README_KO = """\
# Composer 블라인드 평가 패키지

이 패키지는 세 개 덱을 슬라이드 단위로 블라인드 평가하기 위한 자료입니다.
`pack/A`, `pack/B`, `pack/C` 폴더는 같은 43쪽 소스 문서를 서로 다른 방식으로
만든 결과물이며, 페이지 번호는 세 덱 모두 동일한 소스 페이지를 가리키도록
정렬되어 있습니다 (예: 모든 덱의 `slide-07.png`는 같은 원본 7쪽).

`pack/D` 폴더는 **다른 내용의 10쪽짜리 별도 덱**입니다. A/B/C와 페이지 대
페이지로 비교하지 마세요 - 이 덱은 "레이아웃 완성도(craft)"만 별도로 채점하는
기준(calibration) 세트입니다.

평가자는 **어떤 폴더가 어떤 결과물인지 알아내려고 시도하지 마세요.**
(`key.json` 파일은 평가자에게 제공되지 않습니다. 열어보지 마세요.)

## 채점 방법

`sheets/rating_sheet_main.csv` 에 A/B/C 각 슬라이드를, `sheets/rating_sheet
_target_craft.csv` 에 D 덱의 각 슬라이드를 아래 5개 항목으로 1~5점 채점하여
빈 칸을 채워 주세요. 한 시트에 평가자 3명분의 행이 이미 만들어져 있으니
본인의 `rater_id` (rater-1 / rater-2 / rater-3) 행만 채우면 됩니다.

## 평가 항목 (5축, 1~5점)

1. **legibility (가독성)** - 글자 크기, 대비, 여백이 충분해 즉시 읽히는가.
   1=읽기 힘들다 / 3=대체로 읽힌다 / 5=한눈에 읽힌다.
2. **information_hierarchy (정보 위계)** - 제목/본문/보조정보의 중요도가
   시각적으로 구분되는가. 1=모든 요소가 동등해 보인다 / 3=주요 요소는
   구분된다 / 5=한눈에 무엇이 중요한지 안다.
3. **layout_craft (레이아웃 완성도)** - 정렬, 간격, 균형이 의도적으로
   설계된 느낌인가. 1=요소가 아무렇게나 놓여 있다 / 3=정돈되어 있다 /
   5=전문 디자이너가 만든 느낌이다.
4. **deck_consistency (덱 전체 일관성)** - 이 슬라이드가 같은 덱의 다른
   슬라이드와 동일한 스타일 시스템(색, 폰트, 여백 규칙)을 따르는가.
   1=따로 노는 느낌 / 3=대체로 일관적 / 5=하나의 시스템으로 보인다.
5. **reads_as_designed (설계된 느낌 vs 채워 넣은 느낌)** - 내용에 맞춰
   레이아웃이 설계되었는가, 아니면 템플릿에 텍스트만 채워 넣은 느낌인가.
   1=텍스트 덤프처럼 보인다 / 3=무난하다 / 5=이 내용을 위해 설계된 것
   처럼 보인다.

빈 칸으로 남기지 말고, 판단이 어려운 경우에도 최선의 추정치를 적어 주세요.
`notes` 칸은 자유 기술(선택)입니다.

## 참고: 자동 사전 필터 (게이트 아님)

`style_prefilter_advisory.json` 에는 후보 덱(candidate)이 타깃 덱과 팔레트/
밀도 면에서 얼마나 비슷한지에 대한 자동 수치가 들어 있습니다. 이는
라운드 간 팔레트·밀도 이탈을 잡기 위한 사전 참고 자료일 뿐이며, **합격/불합격을
결정하는 게이트가 아닙니다.** 평가자는 이 파일을 볼 필요가 없습니다.
"""


@dataclass(frozen=True)
class DeckBuild:
    role: str
    label: str
    source: str
    png_paths: list[Path]


def _shuffle_letters(roles: list[str]) -> dict[str, str]:
    rng = random.Random(SHUFFLE_SEED)
    shuffled = list(roles)
    rng.shuffle(shuffled)
    letters = ["A", "B", "C"][: len(shuffled)]
    return dict(zip(letters, shuffled, strict=True))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Render, anonymise and assemble the pack")
    build.add_argument("--out", type=Path, default=DEFAULT_OUT)
    build.add_argument("--floor-pdf", type=Path, default=DEFAULT_FLOOR_PDF)
    build.add_argument("--parity-pptx", type=Path, default=DEFAULT_PARITY_PPTX)
    build.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    build.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET_DIR)
    build.add_argument("--style-script", type=Path, default=DEFAULT_STYLE_SCRIPT)

    score = sub.add_parser("score", help="Compute medians and the phase-1 verdict from filled sheets")
    score.add_argument("--out", type=Path, default=DEFAULT_OUT)
    score.add_argument("--main-sheet", type=Path, default=None)
    score.add_argument("--target-sheet", type=Path, default=None)

    return parser.parse_args(argv)


def build_pack(args: argparse.Namespace) -> None:
    out_root: Path = args.out
    pack_dir = out_root / "pack"
    sheets_dir = out_root / "sheets"
    build_dir = out_root / "_build"
    for directory in (out_root, pack_dir, sheets_dir, build_dir):
        directory.mkdir(parents=True, exist_ok=True)

    role_letter = _shuffle_letters(["floor", "parity", "candidate"])
    letter_role = {role: letter for letter, role in role_letter.items()}

    floor_pngs = render_pdf_to_pngs(args.floor_pdf, pack_dir / letter_role["floor"], expected_pages=43)

    parity_pdf = convert_pptx_to_pdf(args.parity_pptx, build_dir / "parity")
    parity_pngs = render_pdf_to_pngs(parity_pdf, pack_dir / letter_role["parity"], expected_pages=43)

    candidate_source = sorted(args.candidate_dir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if len(candidate_source) != 43:
        raise SystemExit(f"expected 43 candidate PNGs in {args.candidate_dir}, found {len(candidate_source)}")
    candidate_pngs = copy_png_set(candidate_source, pack_dir / letter_role["candidate"])

    target_source = sorted(args.target_dir.glob("slide-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
    if len(target_source) != 10:
        raise SystemExit(f"expected 10 target PNGs in {args.target_dir}, found {len(target_source)}")
    target_pngs = copy_png_set(target_source, pack_dir / "D")

    decks = {
        "floor": DeckBuild("floor", "09-02 source-native output (preserve-43)", str(args.floor_pdf), floor_pngs),
        "parity": DeckBuild("parity", "source deck itself (deck-43)", str(args.parity_pptx), parity_pngs),
        "candidate": DeckBuild(
            "candidate",
            f"Composer offline-eval output ({args.candidate_dir.name})",
            str(args.candidate_dir),
            candidate_pngs,
        ),
    }

    generated_at = datetime.now(timezone.utc).isoformat()
    assignment = {
        letter: {
            "role": role,
            "label": decks[role].label,
            "source": decks[role].source,
            "pageCount": len(decks[role].png_paths),
        }
        for letter, role in role_letter.items()
    }
    assignment["D"] = {
        "role": "target_craft_calibration",
        "label": "Hyundai SI reference deck (craft calibration, not page-aligned)",
        "source": str(args.target_dir),
        "pageCount": len(target_pngs),
    }
    key = {
        "generatedAt": generated_at,
        "shuffleSeed": SHUFFLE_SEED,
        "warning": "Do NOT share this file with raters.",
        "assignment": assignment,
    }
    (out_root / "key.json").write_text(json.dumps(key, indent=2, ensure_ascii=False), encoding="utf-8")

    write_sheet(sheets_dir / "rating_sheet_main.csv", decks=("A", "B", "C"), slide_count=43)
    write_sheet(sheets_dir / "rating_sheet_target_craft.csv", decks=("D",), slide_count=10)

    (out_root / "README.md").write_text(README_KO, encoding="utf-8")

    prefilter = run_style_prefilter(pack_dir / letter_role["candidate"], args.target_dir, args.style_script)
    (out_root / "style_prefilter_advisory.json").write_text(
        json.dumps(prefilter, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    manifest = {
        "generatedAt": generated_at,
        "designBarDoc": "docs/generation-quality-root-cause-and-composer-architecture-20260904.md#7b",
        "renderTargetSize": list(TARGET_SIZE),
        "distinction": (
            "pack/A, pack/B, pack/C are page-aligned (same 43-page source, "
            "shuffled anonymously). pack/D is a separate 10-page craft "
            "calibration deck with different content and is never compared "
            "page-by-page with A/B/C."
        ),
        "inputs": {
            "floorPdf": str(args.floor_pdf),
            "parityPptx": str(args.parity_pptx),
            "candidateEvalDir": str(args.candidate_dir),
            "targetDir": str(args.target_dir),
            "styleScript": str(args.style_script),
        },
        "pageCounts": {letter: info["pageCount"] for letter, info in assignment.items()},
        "stylePrefilterAdvisory": "style_prefilter_advisory.json",
        "keyFile": "key.json (withhold from raters)",
        "sheets": ["sheets/rating_sheet_main.csv", "sheets/rating_sheet_target_craft.csv"],
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Pack built at {out_root}")
    print(f"A/B/C page counts: {[assignment[letter]['pageCount'] for letter in ('A', 'B', 'C')]}")
    print(f"D (target craft calibration) page count: {assignment['D']['pageCount']}")
    print(f"Style pre-filter score (advisory, not a gate): {prefilter['score']}")


def score_pack(args: argparse.Namespace) -> None:
    out_root: Path = args.out
    key = json.loads((out_root / "key.json").read_text(encoding="utf-8"))
    main_sheet = args.main_sheet or (out_root / "sheets" / "rating_sheet_main.csv")
    target_sheet = args.target_sheet or (out_root / "sheets" / "rating_sheet_target_craft.csv")

    main_rows = read_sheet(main_sheet)
    target_rows = read_sheet(target_sheet)
    verdict = evaluate(key, main_rows, target_rows)

    print(format_verdict(verdict))
    (out_root / "verdict.json").write_text(json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nWritten: {out_root / 'verdict.json'}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.command == "build":
        build_pack(args)
    elif args.command == "score":
        score_pack(args)
    else:  # pragma: no cover - argparse enforces valid subcommands
        raise SystemExit(f"unknown command: {args.command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
