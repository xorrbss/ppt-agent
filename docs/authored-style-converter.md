# Authored 스타일 변환기

`scripts/convert-authored-style.py`는 외부 NotebookLM 프롬프트 스타일 YAML을
PPT-agent의 Authored 스타일 스키마로 변환한다. 변환 결과에 네트워크가 필요하지
않으며 저장소가 이미 사용하는 PyYAML만 사용한다.

변환 규칙은 공개 저장소
[`YamilAyma/notebooklm-prompt-styles`](https://github.com/YamilAyma/notebooklm-prompt-styles)의
커밋 `84b108d2228372370aace26d69583b74a8b7cde5`에 있는 100개 스타일과
[`CONTRIBUTING.md`](https://github.com/YamilAyma/notebooklm-prompt-styles/blob/84b108d2228372370aace26d69583b74a8b7cde5/CONTRIBUTING.md#step-1-create-the-style-yaml)를
기준으로 작성했다. 외부 저장소는 조사 자료일 뿐이며 런타임에 내려받지 않는다.

## 사용법

저장소 루트에서 실행한다.

```bash
# 단일 파일을 ID 기반 파일명으로 출력 디렉터리에 변환
uv run --project servers/fastapi python scripts/convert-authored-style.py \
  path/to/academic_edge.yaml --output build/authored-styles

# 단일 파일을 지정한 .yaml 파일로 변환
uv run --project servers/fastapi python scripts/convert-authored-style.py \
  path/to/academic_edge.yaml --output build/academic-edge.yaml

# 디렉터리의 .yaml/.yml 파일을 비재귀 배치 변환
uv run --project servers/fastapi python scripts/convert-authored-style.py \
  path/to/styles --output build/authored-styles

# 파일을 쓰지 않고 모든 파싱·충돌·round-trip 검증 수행
uv run --project servers/fastapi python scripts/convert-authored-style.py \
  path/to/styles --output build/authored-styles --dry-run

# 이미 존재하고 대소문자까지 정확히 같은 대상만 명시적으로 교체
uv run --project servers/fastapi python scripts/convert-authored-style.py \
  path/to/styles --output build/authored-styles --overwrite
```

배치 출력은 디렉터리여야 한다. 단일 파일 출력은 `.yaml`만 허용한다. 성공 시 입력
경로 순으로 변환 계획을 출력하고, 오류 시 traceback 없이 종료 코드 `2`를 반환한다.

## 필드 변환

| Authored 필드 | 변환 규칙 |
| --- | --- |
| `id` | 명시적 `id` 또는 입력 파일 stem을 ASCII 소문자 hyphen ID로 정규화한다. 경로 구분자, `..`, Windows 예약명, 80자 초과 ID는 거부한다. |
| `name` | `name_ko`, `name` 순으로 사용한다. 한글이 없으면 정규화한 ID의 제목에 `스타일`을 붙인다. |
| `description` | `description_ko`가 있으면 사용하고, 없으면 색상·타이포그래피·레이아웃을 설명하는 결정적인 한국어 문장을 만든다. 원본 영문 `description`은 `brief`의 `MOOD`에 보존한다. |
| `category` | 현재 허용값과 알려진 외부 별칭을 매핑한다. 값이 없으면 ID·설명·theme 키워드로 `research`, `technology`, `business`, `editorial`, `creative`를 순서대로 추론하고 나머지는 `general`로 둔다. |
| `tags`, `use_cases` | 유효한 외부 목록을 정렬·중복 제거한다. 없으면 category별 한국어 기본값을 사용한다. |
| `preview` | palette 문자열에서 3자리/6자리 hex를 추출해 대문자 `#RRGGBB`로 만든다. background/primary/accent 계열 키를 우선하고 부족한 색은 고정 기본 palette로 채운다. variant는 `notebooklm-{category}`이다. |
| `brief` | 원본 theme, typography, color palette, key visual elements, image prompts, slide templates를 현재 카탈로그의 9개 섹션으로 재작성한다. 원본 레이아웃이 적어도 cover/content/data/timeline/closing archetype을 보충한다. |

`brief` 섹션 순서는 `MOOD`, `PALETTE`, `TYPOGRAPHY`, `LAYOUT SYSTEM`,
`SIGNATURE ELEMENTS`, `DATA VISUALIZATION`, `IMAGE DIRECTION`,
`SLIDE ARCHETYPES`, `AVOID`로 고정된다. YAML 키와 배치 입력은 결정적으로 정렬되므로
같은 입력은 같은 bytes를 만든다.

## 안전 정책

- 입력은 UTF-8 단일 YAML 문서이며 최대 1 MiB이다. PyYAML safe loader를 사용하고
  duplicate key, alias, 100단계를 넘는 중첩을 거부한다.
- 변환기는 모든 입력을 메모리에서 변환하고 ID·대소문자 무시 파일명·대상 충돌을
  사전검사한 뒤에만 기록한다. 산출물은 임시 파일에서 같은 디렉터리로 원자 교체한다.
- 생성한 전체 배치를 임시 디렉터리에 기록해 현재 `load_authored_styles()`로 다시
  읽고 모든 공개 필드와 private `brief`가 일치하는지 확인한다.
- 기본값은 덮어쓰기를 금지한다. 따라서 공식 30종 카탈로그를 대상으로 실행해도
  같은 파일이 있으면 실패하며 기존 bytes를 변경하지 않는다. `--overwrite`는 사용자가
  지정한 정확히 같은 철자의 기존 대상만 교체하므로, 공식 카탈로그에는 사용하지 않는다.
- dry-run도 파싱, 정규화, 충돌 검사, 현 로더 round-trip을 모두 수행하지만 출력
  디렉터리조차 만들지 않는다.
