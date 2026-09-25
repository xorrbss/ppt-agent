# SVG 이미지 자산 파이프라인 설계

상태: 품질 우선 설계 v3 — 최초 생성 품질을 최우선 목표로 확정, 품질 우위와 변환 수치는 검증 전  
범위: SVG 생성의 PPTX·DOCX·PDF 및 직접 첨부 이미지 수집, LLM 입력, 미리보기, 저장, 재시도, PPTX/PDF 내보내기  
변경 근거: 사용자 결정에 따라 최초 생성의 잘림·겹침 감소와 내용·가독성 보존을 생성 성공률보다 우선한다. 초기 품질 관문, DOCX/PDF 단계별 확장, 중앙 이미지 보존 정책을 반영했다. 이 문서는 구현 완료 증거가 아니다.

## 1. 결정

최우선 목표는 동일 자료·스타일·모델 조건에서 HTML보다 최초 생성의 넘침·잘림·의도하지 않은 겹침을 줄이면서 내용·수치·이미지 가독성을 보존하는 것이다. 이미지 오류 없이 생성되거나 PPTX를 열 수 있다는 사실은 이 목표의 달성을 뜻하지 않는다. 품질 우위는 전제로 가정하지 않고 초기 제품 경로 비교에서 확인한다.

개발은 최소 제품 경로 연결 → 최초 생성 품질 비교 → 확인된 약점 개선 → 형식·복구 범위 확장 순서로 진행한다. 우위가 확인되지 않으면 기능 수를 늘려 완료로 보고하지 않고 입력·배치·글꼴·이미지 표현의 실패 원인을 먼저 개선한다. DOCX/PDF 지원은 전체 범위에 유지하되 복합 도표 추출까지 첫 품질 비교의 선행 조건으로 묶지 않는다.

SVG 이미지 처리는 원본과 목적별 파생본을 분리한다. 정규 SVG와 체크포인트에는 이미지 bytes 또는 base64 data URI를 저장하지 않는다. LLM은 불투명한 `source-slot-*`만 배치하며, 서버가 manifest에 고정된 자산만 검증·해결한다.

기존 1.5MB 제한은 사용자 입력 제한으로 사용하지 않는다. 원본은 보존하고, 모델·화면·내보내기 목적에 맞게 파생본을 만든 뒤 각 실행 경계의 최종 방어선만 적용한다. 파생본을 만들 수 있는 정상 이미지가 크다는 이유로 문서 전체를 실패시키지 않는다.

## 2. 해결할 현재 결함

현재 경로는 선택된 원본 이미지를 읽어 page manifest마다 base64로 복제한다. 이 값이 checkpoint input digest와 provider 입력, 확정 SVG까지 전파된다.

2026-09-16 실제 100페이지 생성에서는 참고 PPTX에서 추출한 이미지가 단일 이미지 1.5MB 제한을 넘었다. 로컬 이미지 검증 전에 첫 페이지가 `inflight=1`로 저장됐고, provider 호출은 0회였지만 자동 재시도는 `svg_provider_attempt_uncertain`으로 종료됐다. 원본 첨부와 reference snapshot은 남았으나 발행된 페이지는 없었다.

설계가 제거해야 할 문제:

- 한 이미지가 여러 페이지의 base64와 digest 입력에 중복된다.
- DB JSON, LLM 요청, 작업 메모리와 export bundle 크기가 원본 이미지 크기에 비례한다.
- 정상적인 고해상도 사진이 고정 byte 제한 때문에 문서 전체를 실패시킨다.
- 로컬 실패가 provider 불확실 호출로 오분류된다.
- 화면용 이미지와 PPTX용 이미지가 같은 bytes를 사용해 속도와 품질을 동시에 최적화할 수 없다.
- 어떤 이미지가 축소·제외·대체됐는지 문서 영수증에서 설명할 수 없다.

## 3. 권위와 불변 조건

1. 사용자 원본은 기존 Workspace Asset/Reference Snapshot과 Source Image registry가 권위다. 새 업로드 통로를 만들지 않는다.
2. 원본 bytes는 변경하지 않는다. EXIF 방향, 색공간, 포맷 변환은 파생본에만 적용한다.
3. 자산 선택·출처·변환 정책과 model-preview는 provider 호출 전에 문서 전체에 대해 결정하고 digest로 고정한다. 배치 크기에 의존하는 screen/export 파생본은 SVG 배치 확정 후 생성한다.
4. 모델은 경로, URL, data URI, 저장소 키를 보지 않는다. slot ID와 의미 설명, 별도 image content part만 본다.
5. 모델이 반환한 알 수 없는 slot은 거부한다. manifest에 없는 파일이나 네트워크 자원은 열지 않는다.
6. 정규 SVG는 `source-slot-*` 참조를 유지한다. base64가 포함된 독립 실행형 SVG는 renderer/export worker의 일시 파생 결과다.
7. 화면·PDF·PPTX는 같은 정규 SVG digest와 같은 source manifest revision을 사용한다. 목적별 placement manifest는 이 두 digest를 명시적으로 참조한다.
8. 사용자 요청에서 이미지 보존이 필수인 경우와 일반적인 이미지 재사용을 구분한다.
9. 로컬 preflight 실패는 provider 호출로 집계하지 않는다.
10. 공통 수집기에 DOCX/PDF 어댑터를 추가하되 이번 활성화 대상은 SVG다. HTML Authored의 기본 선택·필터·생성 결과는 보존한다.
11. 이미지 bytes 중복 제거와 원문 내 등장 위치 보존을 분리한다. 동일 이미지의 여러 occurrence를 하나의 등장 위치로 합치지 않는다.
12. 추출 실패·미지원 개체·모델 미사용을 이미지가 없는 정상 문서와 구분한다. 전체 페이지 렌더를 개별 이미지 추출 성공으로 집계하지 않는다.
13. 최초 응답, 결정적 배치 보정 후, LLM 교정 후 결과를 분리한다. 보정으로 최종 결함이 사라져도 최초 생성 결함을 없었던 것으로 집계하지 않는다.
14. 선택되지 않은 참고 이미지의 추출·변환 오류는 필수 입력 실패로 승격하지 않는다. 전체 보존 지시와 문서 자체의 보안·권한 실패는 이 예외보다 우선한다.

## 4. 전체 흐름

```text
Workspace Asset / Reference Snapshot
                │
                ▼
PPTX / DOCX / PDF / 직접 이미지 수집·출처·digest 확인
                │
                ▼
문서 전체 이미지 선택 및 source-slot 고정
                │
                ▼
원본 안전성·변환 가능성 검사 + model-preview preflight
                │
                ▼
source manifest 영속화 + checkpoint input digest 확정
                │
                ▼
기존 호출 ledger 예약 → dispatch intent → 응답 영수증
                │
                ▼
slot을 유지한 정규 SVG 검증·후보 checkpoint 저장
                │
        ┌───────┴────────┐
        ▼                ▼
screen resolver      PPTX/PDF worker
배치별 파생본·측정     배치별 export 파생본·변환
        │                │
        └───────┬────────┘
                ▼
목적별 placement manifest / 결과 digest / 품질 receipt
```

전체 문서 preflight는 원본을 안전하게 읽고 필요한 모델 입력을 만들 수 있는지 확인하는 단계다. 아직 존재하지 않는 SVG 배치에 맞춘 최종 파생본을 요구하지 않는다. screen 검증 완료 후 페이지를 발행하며, export 결과는 별도 operation에서 확정한다. 후속 변환 실패 시 저장된 SVG부터 재개하고 자산 문제를 이유로 LLM을 다시 호출하지 않는다.

## 5. 자산 종류

| 종류 | 용도 | 저장 원칙 |
|---|---|---|
| source | 업로드 원본과 문서에서 추출한 원본 개체 | 업로드 bytes 불변; embedded bytes/stream 및 출처 연결 보존 |
| source-appearance | crop·회전·mask가 적용된 원본의 보이는 모습, 도표 영역 또는 스캔 페이지 | 추출 원본과 구별하는 재현 가능한 파생본; renderer와 영역 기록 |
| model-preview | LLM이 이미지 의미와 구도를 이해하기 위한 입력 | 작고 안정적인 sRGB 파생본, content-addressed 재사용 |
| screen-preview | Chromium 측정과 화면/썸네일용 | 실제 배치 크기와 device scale에 맞춘 파생본 |
| export | native PPTX 및 고품질 PDF용 | 실제 배치 크기에 맞춘 고품질 파생본, 원본보다 확대하지 않음 |
| resolved-svg | slot이 data URI로 치환된 worker 내부 결과 | 영속 원본으로 저장하지 않으며 작업 종료 시 폐기 |

`model-preview`, `screen-preview`, `export`가 같은 bytes 조건을 만족하면 digest가 같은 한 파생본을 공유할 수 있다. 역할 이름이 같다는 이유로 별도 파일을 강제로 만들지 않는다.

### 5.1 기존 경로와 확장 경계

첫 연결 범위는 기존 PPTX/직접 이미지와 DOCX 내장 raster, PDF 개별 raster다. 스캔 페이지는 기존 페이지 렌더·OCR 경로로 출처를 보존한다. DOCX SmartArt/복합 개체, PDF 벡터·혼합 도표의 자동 영역 식별은 후속 확장으로 유지한다. 후속 항목을 첫 비교에서 검증했다고 표시하거나 PDF 전체 지원으로 묶어 공개하지 않는다.

- `services/authored_source_images.py`의 AssetGateway/reference snapshot 권한 확인과 `services/source_images/registry.py`의 등록을 재사용한다. `extract.py`의 PPTX 추출과 나란히 DOCX/PDF 어댑터를 둔다.
- 현재 registry의 문서 이미지 수집 분기는 PPT 계열 중심이다. `lightweight_document_service.py`의 DOCX 경로는 텍스트 추출이므로 그림의 위치·관계 추출을 추가해야 한다.
- `documents_loader.py`의 PDF 페이지 렌더, `source_locked/pdf.py`의 페이지 번호·원본 digest, `document_conversion_service.py`의 Office 변환을 재사용 대상으로 삼는다. 이들에 개별 이미지 추출 기능이 이미 있다고 가정하지 않는다.
- 수집 결과는 `assets`, `occurrences`, `diagnostics`, `coverage`를 반환한다. 상태는 `complete`, `partial`, `failed`, `no_images`로 구분한다. 미지원·손상 개체가 있으면 단순 빈 목록으로 성공 처리하지 않는다.
- 출처 모델의 `source_slide_index`는 PPTX 호환 필드로 유지하고 공통 `sourceLocator`를 추가한다. 파일 형식별 어댑터가 같은 선정·변환·manifest 경로로 합류한다.

### 5.2 DOCX 이미지 추출

1. ZIP/XML 안전 검사를 거친 후 본문, 표 안의 문단, 텍스트 상자, 머리글·바닥글, 각주·미주의 실제 참조를 관계 파일과 연결한다. 참조되지 않은 `word/media` 파일을 내용 이미지로 오인하지 않는다.
2. DrawingML의 inline/anchor 이미지와 VML 그림 참조를 읽는다. AlternateContent의 지원되는 한 분기만 선택하여 같은 그림을 중복 집계하지 않는다. 외부 연결 이미지는 내려받지 않고 `external_reference_unresolved`로 기록한다.
3. 원본 media bytes, part/relationship ID, 문단 또는 표·셀 위치, 문서 순서, 가까운 제목·캡션·대체 텍스트를 보존한다. 관계 ID는 part 범위에서 해석한다. 캡션 연관이 모호하면 추정임을 표시한다.
4. 동일 media가 여러 번 등장하면 bytes는 재사용하되 occurrence별 crop·회전·표시 크기와 문맥을 유지한다. 내부 단위와 좌표계, 좌우/상하 반전도 기록한다.
5. DOCX의 페이지 번호는 XML만으로 확정하지 않는다. 기본 출처는 part/문단/표 위치이며 `pageIndex`는 null이다. 페이지가 필요하면 격리 Office→PDF 변환 결과와 renderer/font version을 연결하고, 위치 매칭 신뢰도를 기록한다.
6. 차트·SmartArt·그룹 도형 및 안전하게 직접 디코딩하지 못하는 그림은 원본 package를 유지한다. 지원되는 격리 렌더가 있으면 확인된 개체 영역을 `source-appearance`로 등록한다. 개체 위치를 확정할 수 없으면 전체 페이지를 해당 개체인 것처럼 등록하지 않고 `unsupported_object` 또는 `region_unresolved`로 남긴다.

머리글 로고·작은 도형·반복 이미지는 수집 후 분류한다. 기존 200px/반복 횟수/전체 페이지 면적 필터로 먼저 폐기하지 않는다. 사용자 보존 요청과 required occurrence가 필터보다 우선한다. `.docx`가 필수 지원 범위이며 `.doc`/`.docm` 확장은 별도 지원 판정 없이 자동 포함하지 않는다. 매크로 실행은 하지 않는다.

### 5.3 PDF 이미지·도표 추출

1. 권한과 PDF 파싱·페이지·디코딩 예산을 확인한다. 페이지별로 실제 표시되는 image occurrence를 조사하고 XObject, Form 내부 이미지, inline image를 처리 범위에 포함한다. 미사용 리소스나 mask만을 독립 그림으로 등록하지 않는다.
2. 원본 document digest, pageIndex(0 기반), object locator 또는 inline occurrence locator, 표시 bbox, clip, transform, 페이지 rotation/CropBox/MediaBox를 기록한다. 사용자 화면의 페이지 번호는 1 기반으로 표시한다.
3. 직접 복원이 가능한 embedded raster는 bytes 또는 원본 stream과 decode recipe를 보존한다. 색공간·soft mask·image mask가 관련되면 원본 PDF에서 보이는 결과를 재현한 `source-appearance`를 연결한다. 같은 object라도 crop/transform이 다르면 occurrence를 분리한다.
4. 벡터 도표나 텍스트·선·이미지로 조합된 그림은 개별 이미지 파일이 없을 수 있다. 기존 PDF 렌더 경로를 이용해 확인된 영역을 렌더하고 `extractionKind=region_render`로 등록한다. 텍스트·표를 장식 이미지로 임의 재분류하지 않으며 영역 분리가 불확실하면 검수 대상으로 남긴다.
5. 스캔 PDF는 페이지 이미지/페이지 렌더를 `extractionKind=scanned_page`로 보존하고 기존 OCR 문맥을 연결한다. 확실한 하위 그림 영역만 별도 occurrence로 만든다. 하위 영역을 얻지 못하면 페이지 자산을 유지하고 `page_only` coverage를 표시한다. 페이지 전체를 이미지 없음 또는 일반 backplate로 자동 제외하지 않는다.
6. 직접 추출, 영역 렌더, 스캔 페이지가 같은 시각 영역을 나타내면 preferred occurrence와 대체 관계를 기록한다. 생성 후보에 중복 투입하지 않되 원본 출처는 유지한다.

PDF 객체 추출 backend는 기존 의존성의 stream/좌표 지원을 먼저 확인한다. 기존 페이지 렌더 재사용은 확정 방향이며, 정확한 객체·mask 복원에 필요한 adapter/library 선택은 구현 단계의 capability 확인 사항이다. 지원하지 못하는 형식을 성공으로 표시하지 않는다. 암호가 필요한 PDF는 기존 업로드·복호화 흐름으로 해결되기 전 `source_locked` 상태이며 provider를 호출하지 않는다.

### 5.4 출처와 보존 단위

`sourceLocator`는 `sourceDocumentAssetId`, `sourceDocumentSha256`, `sourceFormat`, `partName/objectLocator`, `blockPath`, `pageIndex`, `bbox`, `coordinateSpace`, `transform`, `caption`, `contextConfidence`를 갖는다. 형식에 해당하지 않거나 확인할 수 없는 값은 null로 남긴다. 추출 ID는 문서 digest + locator + occurrence 순서에서 안정적으로 만든다.

`extractionKind`는 `embedded_raster`, `region_render`, `scanned_page`, `direct_image`로 구분한다. `sourceObjectDigest`, `appearanceDigest`, `extractionProfileVersion`으로 원본 객체와 보이는 결과를 연결한다. PDF 원본 stream hash와 디코딩된 이미지 hash가 같다고 가정하지 않는다. 의미 분석·OCR 결과는 이미지 bytes와 분리하고 분석 모델·버전을 남긴다.

수집은 등록 가능한 모든 occurrence와 제외 근거를 남기고, LLM에는 페이지 문맥과 예산으로 선정한 일부만 보낸다. required 여부는 asset 전체가 아니라 원문 occurrence와 대상 페이지 매핑에 적용한다. 전체 이미지 보존 요청에서 coverage가 partial/failed이면 미추출 개체를 required 목록 밖에 두어 통과시키지 않는다. 보존 범위를 확인할 수 있을 때까지 preflight 실패로 남긴다.

## 6. 목적별 변환 정책

공통 전처리:

- renderer/provider에 전달할 raster는 디코더가 확인한 PNG/JPEG이며 직접 PNG/JPEG/WebP 원본을 수용한다. DOCX/PDF의 다른 embedded 포맷·stream은 원본을 보존하고 격리 추출/렌더 어댑터에서 지원 여부를 판정한다.
- EXIF 방향을 픽셀에 적용하고 메타데이터를 제거한다.
- sRGB로 변환한다. alpha가 의미 있으면 유지한다.
- 이미지 폭·높이·픽셀 수·디코딩 시간은 업로드/worker 안전 한도에서 검사한다.
- raster 원본보다 큰 이미지로 확대하지 않는다. 픽셀 원본이 없는 벡터 영역은 출력 크기에 맞춰 예산 내에서 렌더한다.
- 같은 source appearance digest와 변환 recipe는 content-addressed 결과를 재사용한다. crop·mask·회전이 다른 occurrence를 같은 결과로 잘못 재사용하지 않는다.

포맷 선택:

- 투명도, 로고, UI 캡처, 선 중심 도형은 PNG를 우선한다.
- 사진은 JPEG를 우선하고 시각 품질 목표를 만족하는 범위에서 품질을 조정한다.
- WebP 원본은 원본으로 보존하되 SVG/PPTX용 파생본은 검증된 PNG/JPEG로 만든다.
- 변환 결과의 실제 포맷, 크기와 digest를 다시 읽어 검증한다.

크기 결정:

- model-preview는 provider가 지원하는 입력 크기와 문서별 이미지 예산 안에서 긴 변을 제한한다.
- screen-preview는 SVG가 요구한 배치 box × 검증된 device scale을 목표로 한다.
- export는 PPTX/PDF에서의 배치 box와 출력 밀도를 기준으로 하며 raster 원본 해상도를 상한으로 한다. CSS px→inch 환산은 문서의 실제 canvas/export 크기로 계산한다. 벡터 영역 렌더는 원본 픽셀 상한 대신 renderer 해상도·자원 예산을 적용한다.
- 작은 배치에 큰 원본을 그대로 넣지 않는다. 큰 배치에 필요한 픽셀이 원본에 없으면 확대하지 않고 receipt에 해상도 부족을 기록한다.

byte 제한은 변환의 입력 조건이 아니라 실행 경계의 최종 방어선이다. 사진은 profile의 최소 해상도·품질까지 단계적으로 조절한다. 화면 캡처·차트·문자 포함 이미지는 사진처럼 손실 압축하지 않고 PNG와 글자 가독성을 우선한다. `protected_regions`의 텍스트·로고·핵심 영역을 자르지 않는다. 영역이 불명확하면 전체를 보호한다. 새 crop은 기본 금지이며 원문에 이미 적용된 crop 재현은 원본 appearance 복원으로 구분한다.

최소 기준에서도 맞지 않으면 해당 slot의 `allowExcludeOnFailure`에 따라서만 제외할 수 있다. 원본은 유지하고 제외·저해상도·검수 필요 상태를 표시한다. 모델 입력용 축소본을 고품질 export의 원본으로 재사용하지 않는다.

다음은 구현 시 비교 실험을 시작할 **후보 profile**이며 provider의 공식 한도나 이미 검증된 제품 기본값이 아니다. 기존 provider capability/worker 정책이 더 엄격하면 이를 따른다. 초기 활성화 전에 실제 지원 한도와 품질 결과를 확인해 `svg-image-derivative/v1`의 구체적 값·recipe digest를 고정한다.

| 항목 | 초기 비교 후보 | 제한에 닿았을 때 |
|---|---|---|
| model-preview | 사진 긴 변 1536px, 문자·도표 2048px; 원본보다 확대 금지 | 페이지별 필요 이미지부터 제공; 핵심 글자 정보가 읽히지 않으면 검수/입력 재계획 |
| 사진 JPEG | 품질 90부터 85까지; 모델용 긴 변 축소 하한 min(원본 긴 변, 1024px) | 더 낮춰 성공 처리하지 않고 정책별 제외 또는 preflight 실패 |
| screen | 배치 CSS 크기 × 2; 확대 화면은 별도 해상도 요청 가능 | 기존 파생본 재사용 또는 원본에서 재생성, 저해상도 상태 표시 |
| export | 사진 200ppi, 문자·도표 300ppi 비교 시작 | raster 원본 한도 준수, 원본 부족은 가독성 검수 대상 |
| 요청 예산 | 내용 이미지 최대 4개부터 실험; 스타일 포함 총 이미지 bytes 8MiB 이내 후보 | 실제 provider의 개수·해상도·전송 직렬화 제한까지 함께 적용 |
| 변환 자원 | 이미지당 64MP, 작업자 메모리 512MiB, 동시 변환 2개, 변환 30초 후보 | 작업자 전체 예약 예산이 허용하는 수만 실행; 부족하면 대기/명시적 실패 |

style reference도 실제 요청 개수·bytes에 포함한다. 최종 wire payload의 base64 팽창과 전체 SVG/worker 한도는 별도 계산한다. 여러 정상 이미지가 합쳐져 기존 2MB SVG 제한을 넘는 문제를 단일 이미지 변환으로 해결됐다고 간주하지 않는다. 정규 SVG는 XML 구조 예산을, resolved SVG는 이미지 합계·메모리 예산을 따로 검증한다.

문자 이미지의 합격 여부는 원본 appearance와 최종 표시 크기의 가독성 비교로 판정한다. OCR 문자 수만으로 통과시키지 않으며 수치·범례·캡션 누락은 허용하지 않는다. 분류가 불명확한 이미지는 문자 이미지 정책을 적용한다.

## 7. manifest 계약

호환 버전은 slot 기반 정규 SVG `svg-static/v2`, 원본 목록 `svg-asset-manifest/v1`, 배치 결과 `svg-placement-manifest/v1`로 나눈다. 아래는 원본 목록의 축약 예이며 이 절의 occurrence·권한·검증 필드를 함께 구현해야 한다.

```json
{
  "schemaVersion": "svg-asset-manifest/v1",
  "profileVersion": "svg-image-derivative/v1",
  "presentationId": "...",
  "sourceRevision": 0,
  "slots": [
    {
      "slotId": "source-slot-...",
      "sourceAssetId": "...",
      "sourceSha256": "...",
      "sourceWidth": 2400,
      "sourceHeight": 1600,
      "semanticDescription": "...",
      "occurrenceId": "...",
      "sourceLocator": {"sourceFormat": "pdf", "pageIndex": 4, "coordinateSpace": "page-normalized"},
      "extractionKind": "embedded_raster",
      "sourceAppearanceSha256": "...",
      "preservationClass": "supporting",
      "required": false,
      "allowOmit": true,
      "allowExcludeOnFailure": false,
      "selectionReason": "content-match",
      "modelDerivative": {"ref": "...", "sha256": "...", "width": 1280, "height": 853, "mediaType": "image/jpeg"},
      "screenPolicy": {"fit": "contain", "allowCrop": false},
      "exportPolicy": {"fit": "contain", "allowCrop": false}
    }
  ],
  "digest": "sha256:..."
}
```

사용자 API, source manifest와 page checkpoint에는 불투명 ref, digest, 크기·정책·출처만 기록하며 private path, storage key, data URL은 노출하지 않는다. 내부 artifact DB와 인증된 worker transport의 기존 opaque storage key 계약은 유지한다. page checkpoint의 이미지 부분에는 제공/사용 slot ID와 source/placement manifest ref·digest만 저장한다.

source manifest는 provider 전 고정된 assets, occurrences, 페이지별 offered slots, 보존 정책, model derivatives와 추출 diagnostics를 포함한다. immutable source manifest를 후속 배치 결과로 덮어쓰지 않는다. placement manifest는 정규 SVG digest, source manifest digest, slide revision, purpose, slot occurrence, box/fit/crop/transform, derivative ref·digest, renderer/profile version을 포함한다. digest는 digest 필드 자체를 제외한 정규화 직렬화로 계산한다.

파생본 metadata는 기존 `ImageAsset`에 연결되는 가산적 저장소로 확장한다. 기본 논리 identity는 `(tenant, owner, source_appearance_sha256, recipe_digest)`이며 recipe에 profile/decoder/encoder version, 실제 목표 픽셀, colorspace, alpha, crop/rotation, 품질을 포함한다. purpose는 선택 정책과 receipt에 남기되 같은 최종 recipe의 bytes를 중복 생성하지 않는다. DB unique 제약과 임시 파일→검증→게시 순서로 하나만 게시한다.

가산적 migration은 derivative metadata와 document/slide revision의 manifest 참조를 추가하고 기존 SVG 필드를 제거하지 않는다. parent manifest를 먼저 영속화하고 FK를 연결한다. candidate checkpoint와 최종 publication은 구분하며, 최종 SVG/source manifest/placement manifest/preview ref는 revision·lease fence를 확인한 하나의 publication transaction으로 갱신한다. stale renderer가 새 revision에 결과를 쓰지 못하게 한다.

동일 bytes 재사용과 작업별 접근 권한은 분리한다. export 시 현재 operation에 귀속된 artifact ref를 발급하고 기존 저장 bytes를 재사용한다. 다른 owner/tenant의 digest를 알고 있다는 이유로 접근을 허용하지 않는다. `image-derivative` 등 필요한 artifact kind/MIME과 export bundle schema version을 명시적으로 추가하고 기존 closed-schema validator도 함께 확장한다.

## 8. LLM 계약

provider prompt에는 slot별로 다음만 전달한다.

- slot ID
- 의미 설명과 출처 페이지
- 원본 비율
- 필수/선택 여부
- 허용 fit/crop 정책

model-preview bytes는 **각 provider 요청에서 필요한 slot에 한해** image content part로 전달한다. JSON prompt, 체크포인트와 SVG에는 복제하지 않는다. 여러 페이지·교정 요청 사이의 provider 캐시나 토큰 절감은 보장하지 않는다. 요청별 실제 이미지 수·bytes·사용량을 기록한다. SVG용 style reference snapshot도 immutable ref로 전환하여 이미지 base64 중복 저장을 제거하되 HTML용 snapshot 계약은 유지한다.

모델은 다음처럼 slot만 반환한다.

```xml
<image href="source-slot-..." x="..." y="..." width="..." height="..." preserveAspectRatio="xMidYMid meet"/>
```

서버는 SVG 파싱 후 slot의 존재, required 또는 allowOmit=false occurrence 사용, 배치 box, 비율 정책을 검사한다. 정규 SVG에는 slot을 그대로 남긴다. provider가 data URI, URL, 경로 또는 미등록 slot을 반환하면 한 번의 교정 대상이며 그대로 저장하지 않는다.

보존 정책은 중앙 판정기 하나에서 아래 표로 결정한다. `required`, `allowOmit`, `allowExcludeOnFailure`는 독립적으로 수정하는 옵션이 아니라 판정 결과의 파생 필드다. 수집·선정·preflight·SVG 검증·UI는 같은 policy version과 결과를 사용하며 서로 다른 기본값을 두지 않는다. 모순된 조합은 manifest 저장 전에 거부한다.

| 정책 | required / allowOmit / allowExcludeOnFailure | 추출·변환 실패 | 모델 미사용 |
|---|---|---|---|
| mandatory: 사용자 지정 또는 원문 보존 필수 occurrence | true / false / false | provider 예약 전 필수 입력 실패 | 한 번 교정 후에도 누락이면 해당 페이지 실패 |
| selected_supporting: 선정된 핵심·지원·중요도 불명확 이미지 | false / true / false | 조용히 제외하지 않음; 입력 계획을 해결하기 전 preflight 미완료 | 기록 유지; 본문 참조·critical·불명확이면 review_required 및 품질 관문 미통과 |
| selected_decorative: 선정된 장식 이미지 | false / true / true | 제외 사유를 남기고 진행 가능 | 미사용 사실 기록 |
| not_selected: 생성 입력에 선정되지 않은 참고 이미지 | false / true / false | 수집 진단만 남기고 생성 차단 안 함; model-preview 생성 불필요 | 모델에 제공하지 않았으므로 not_selected로 기록 |

판정 우선순위는 사용자 전체/개별 보존 지시 및 source-preservation manifest → 선정 여부 → 의미 분류다. mandatory는 not_selected가 될 수 없다. 사용자의 지정 없이 시스템 추정을 사용자 필수 지시로 승격하지 않는다. 선정 이후의 오류를 숨기려고 supporting을 decorative/not_selected로 바꾸지 않는다. 입력 계획 변경은 새 선택 이유·manifest revision을 남기며, 이미 호출한 페이지의 입력을 소급 변경하지 않는다.

`preservationClass`는 critical/supporting/decorative다. 시스템의 중요도 추정은 confidence와 근거를 기록하며 사용자 지시로 가장하지 않는다. 기본적으로 변환 실패에 따른 자동 제외는 확인된 decorative만 허용한다. supporting은 생략할 수 있어도 실패 시 조용히 버리지 않는다. 본문이 참조하는 차트·구성도 또는 중요도 불명확 이미지가 빠지면 `review_required`이며 원본 후보와 미사용 사실을 UI에 남긴다. 미사용 이유를 모델이 제공하지 않으면 `not_used_by_model`로 기록하고 이유를 지어내지 않는다.

## 9. preflight와 체크포인트 순서

한 문서의 provider 호출 전에 다음을 완료한다.

1. reference snapshot과 live asset binding 재확인
2. 형식별 안전한 추출·coverage/diagnostics 수집; 손상·미지원 occurrence는 정상 no_images와 구분
3. 중앙 정책으로 원문 occurrence의 보존 조건과 문서 전체 페이지별 offered slot 고정; 미선정 참고 이미지의 진단은 문서 필수 입력 실패와 구분
4. mandatory 및 선정 slot의 원본 digest/decoder 검증, model-preview 생성 및 provider 입력 예산 검증; screen/export 포맷 변환 가능성만 확인하고 최종 배치 크기는 요구하지 않음
5. source manifest 영속화와 digest 확정
6. 모든 페이지 checkpoint에 content/style/canvas/manifest digest 기록
7. preflight 완료 상태 commit

그 후 페이지별 실행 순서는 다음과 같다.

1. checkpoint와 manifest digest 재확인
2. provider rate/budget admission
3. 기존 provider admission/usage ledger에 semantic slot, ordinal, request digest, lease epoch를 연결하고 `reserved` 기록
4. lease/epoch를 재확인하고 `dispatch_intent`를 commit한 뒤에만 외부 요청 시작; 전송 확인 정보를 얻으면 추가 기록
5. 응답 원문과 확보된 사용량 영수증을 먼저 기록하고 `completed`로 전환; 사용량이 미확정이어도 응답이 있으면 재호출하지 않음
6. SVG slot·내용·배치 검증, 정규 SVG 후보와 source manifest ref checkpoint 저장
7. screen 파생본과 placement manifest 해결, Chromium 측정 및 preview 생성
8. revision/fence 확인 후 정규 SVG, manifest, preview digest와 품질 receipt 원자적 publication

`inflight` boolean 하나로 상태를 표현하지 않는다. 새 provider 상태 저장소를 병렬로 만들지 않고 기존 ledger의 상태에 page checkpoint가 연결되게 한다. DB 상태 전환과 네트워크 전송의 원자성 또는 exactly-once를 주장하지 않는다.

| 마지막 상태 | 재개 처리 |
|---|---|
| preflight_failed | provider 비용 없이 수정 후 재실행 가능 |
| reserved | dispatch intent가 commit되지 않았고 이전 실행자가 fence로 배제됨을 확인한 경우에만 같은 ordinal 재예약 |
| dispatch_intent / dispatched | 원문 응답이 없으면 전송 전/중/후 중단을 구별할 수 없으므로 uncertain. provider가 해당 요청의 idempotency/reconciliation을 보장하는 경우에만 같은 키로 조정 |
| completed | 저장된 원문으로 후속 검증 재개; 누락된 사용량은 별도 조정하고 재구매하지 않음 |
| rendered | provider 호출 없이 저장된 PNG/SVG 재사용 |

dispatch intent 이전의 로컬 이미지 검증 실패는 반드시 `preflight_failed`다. usage receipt가 없다는 사실만으로 호출 0회를 추론하지 않는다. preflight는 결정적 추출·변환과 기존 로컬 OCR/분석 결과 재사용으로 수행하고 새로운 유료 의미 분석은 포함하지 않는다. 별도 모델 분석을 추가하려면 그 호출도 독립 ledger와 비용 단계로 노출해야 한다.

dispatch intent 직후 실제 송신 전 종료는 보수적으로 uncertain이 될 수 있다. 이 구간을 안전 재시도 가능하다고 표시하지 않는다. 기존 실행자의 늦은 송신도 가능한 경계에서는 provider idempotency 또는 기존 admission의 단일 실행 보장이 확인되기 전 재구매하지 않는다. 취소·타임아웃·lease 만료가 기존 호출 ordinal을 새 호출로 자동 바꾸지 못하게 한다.

## 10. 미리보기와 내보내기

웹의 기존 SVG 미리보기와 Electron/썸네일의 기존 PNG 경로를 보존한다. 웹은 권한이 확인된 서버 resolver가 만든 임시 resolved SVG를 기존 sandbox/CSP 경계에서 표시한다. slot 원본이나 private path를 브라우저가 직접 해결하지 않는다. 캐시 키에 owner/tenant, SVG/source/placement digest, 해상도와 renderer version을 포함하고 전달 시 권한을 재검사한다. 확대에 필요한 raster 해상도는 원본 범위에서 다시 요청할 수 있다.

PPTX/PDF export bundle은 다음을 묶는다.

- 정규 SVG artifact ref와 digest
- 문서 revision, slide order와 canvas
- source manifest와 export placement manifest ref·digest
- 각 slot의 export derivative ref와 digest
- converter/profile version

export operation은 소유권·원본 binding을 확인한 뒤 재사용할 파생본에 현재 operation의 artifact ref를 발급한다. worker는 전달받은 ref가 같은 owner/tenant/현재 operation과 manifest digest에 묶였는지 검증한다. 원래 파생본을 만든 operation과 export operation이 같을 필요는 없다. 내부 opaque storage key로 허용된 transport cache만 읽고, 변환기에는 외부 URL이나 DB 접근 권한을 주지 않는다.

PPTX는 export 파생본을 이미지 개체로 포함하며 SVG 텍스트·도형 편집성을 유지한다. PDF는 같은 export 파생본과 정규 SVG를 사용한다. screen과 export bytes가 달라도 원본 appearance, placement와 변환 정책의 연결을 receipt로 증명하고 실제 시각적 일치는 별도 readback으로 판정한다. DOCX/PDF에서 영역 렌더한 도표는 이미지이므로 내부 글자·선까지 native 편집 가능하다고 표시하지 않는다.

## 11. 실패 처리

| 실패 | 처리 |
|---|---|
| 손상·위장 포맷·디코딩 폭탄 | 자산 거부, required면 생성 admission 실패 |
| 추출 불완전·미지원 개체 | occurrence별 diagnostics/coverage 기록; required 보존 불가면 preflight 실패, 나머지는 보존 정책에 따라 검수 또는 실패 |
| 생성 입력에 선정되지 않은 참고 이미지의 추출·변환 오류 | diagnostics만 남기고 해당 이미지를 변환하지 않음; 전체 보존 지시나 문서 자체의 보안·권한 오류가 아니면 생성 차단 금지 |
| allowExcludeOnFailure 이미지의 preflight 변환 실패 | 제외 후 지속, 이유와 미보존 이미지 UI 표시 |
| required 또는 선정된 자동 제외 불허 이미지의 preflight 변환 실패 | provider 예약 전 실패; 원인 수정 후 안전 재시도. 미선정 참고 이미지는 이 분기에 포함하지 않음. 손상 원본은 원본 교체 없이는 자동 재시도하지 않음 |
| provider 이미지 개수/byte 예산 초과 | 생략 허용 slot만 관련성 순으로 줄이고 기록; required가 남은 예산에도 맞지 않으면 preflight 실패 |
| 모델이 생략 허용 slot을 미사용 | offered/used 차이 기록; 본문 참조·critical·중요도 불명확이면 review_required |
| 모델이 required slot을 미사용 | 한 번 교정 후 해당 페이지 실패 |
| 배치 확정 후 screen/export 파생 실패 | SVG 후보 checkpoint를 보존하고 변환 단계부터 복구; provider 재호출 금지 |
| export 파생본 누락·digest 불일치 | 원본 binding 확인 후 결정적으로 재생성 가능하면 복구; 아니면 export만 실패, 생성 원본은 보존 |
| 화면 파생본 누락 | 정규 SVG와 원본으로 재생성; 완료 전 해당 preview를 완료로 표시하지 않음 |

페이지에 선택할 이미지가 많을 때는 개수와 provider 입력 예산을 함께 적용한다. 필수 occurrence를 먼저 배정하고 생략 허용 후보는 relevance·출처 위치·중복 여부로 안정적으로 정렬한다. `limit=None`처럼 페이지마다 무제한 제공하지 않는다. 추출·수집 coverage 부족과 생성 단계의 의도적 생략을 같은 제외 사유로 합치지 않는다.

## 12. 보안·자원 한도

- 원본 업로드 한도와 악성 파일 검사는 기존 AssetGateway 권위를 따른다.
- 이미지 decoder는 worker 경계에서 픽셀 수, 메모리, CPU, 시간 제한을 적용한다.
- canonical SVG의 image href는 manifest에 등록된 정확한 `source-slot-*` 토큰만 허용한다. 이 토큰을 파일 상대 경로로 해석하지 않으며 `http:`, `https:`, `file:`, `data:`와 그 밖의 상대 경로는 모두 거부한다.
- resolver는 manifest slot만 받아 immutable ref의 owner·digest·media type을 재검증한다.
- EXIF와 불필요한 metadata를 파생본에서 제거한다.
- provider/브라우저에는 자격 증명, local path, storage key를 포함하지 않는다. 인증된 worker의 기존 ArtifactRef에는 검증된 opaque storage key를 허용하며 host 절대 경로·임의 경로·provider 자격 증명은 금지한다.
- DOCX ZIP 총 해제 크기/entry 수와 XML entity, PDF page/object/stream 확장량 및 렌더 시간을 제한한다. 외부 relationship과 PDF 외부 자원을 자동 로드하지 않는다. 기존 업로드·worker 한도를 낮추거나 우회하지 않는다.
- 문서 전체 preflight는 refs와 metadata 중심으로 진행하고 bytes는 제한된 worker에서 stream 처리한다. 100페이지 분량의 decoded bitmap/base64를 동시에 쌓지 않는다. document 단위 누적 예산과 전역 worker 예산을 모두 예약하며 취소 시 미게시 임시 파일만 정리한다.
- GC는 현재 문서뿐 아니라 이력 revision, 복제/재작성, 활성 checkpoint, 실행 중 export bundle이 참조하는 manifest와 원본까지 추적한다. operation 하나가 종료됐다는 이유로 공유 bytes를 삭제하지 않는다. 기존 retention/삭제 권한을 따르고 보존 기간을 임의 연장하지 않는다.

하드 byte 상한은 남긴다. 다만 정상 원본을 직접 그 상한에 대입하지 않고, 목적별 변환 결과와 worker bundle에만 적용한다. 상한에 도달한 사실은 `optimized`, `downscaled`, `excluded`, `rejected`로 구분해 기록한다.

## 13. 관측과 품질 receipt

문서 단위:

- 형식별 발견/등록 asset 수와 occurrence 수, 선택/사용/제외 수 및 추출 coverage
- 원본 총 bytes와 목적별 파생본 총 bytes
- 중복 제거율과 cache hit 수
- preflight 시간과 실패 분류
- provider에 전달한 이미지 수와 총 bytes

slot 단위:

- source/derivative digest와 크기
- 변환 profile 및 최종 포맷
- 축소 비율, 품질 조정 여부
- 선택 이유와 required 여부
- 원본 위치·extractionKind, 최종 사용 페이지와 placement box, crop/mask/회전 및 추출 신뢰도
- required/allowOmit/allowExcludeOnFailure와 미사용·제외 사유
- 화면/PPTX/PDF 해결 결과

이 receipt는 품질 점수와 분리한다. 이미지 최적화 성공이 내용·배치 품질 통과를 의미하지 않는다. 이미지 제외가 허용된 경우에도 문서 UI에서 확인할 수 있어야 한다.

UI는 추출→선정→모델 입력 준비→페이지 생성→미리보기→내보내기의 진행 상태를 구분한다. 이미지 상세에는 출처 문서/문단 또는 페이지, 원본 모습, 사용 페이지, 제외·저해상도·검수 이유를 표시한다. 정상 추출 수와 스캔 페이지/영역 렌더 수를 따로 집계한다.

## 14. 구현 순서

0. **비교 조건과 최소 계약**: 대표 원본·아웃라인·이미지 후보·모델·스타일·폰트·canvas를 고정하고 최초 생성/보정 후 측정 경계를 정한다. 중앙 이미지 정책, 가산적 저장과 이중 읽기 경계를 정의한다.
1. **최소 제품 경로 연결**: 기존 PPTX/직접 이미지와 DOCX 내장 그림·PDF 개별 이미지를 이용해 추출→모델 입력→SVG→화면→PPTX를 내부 flag 아래 연결한다. 필요한 preflight·권한·provider ledger·resolver는 이 단계에 포함한다. 기존 코드를 확장하고 독립 시험용 생성 경로를 남기지 않는다.
2. **최초 생성 품질 관문**: §15.2의 동일 조건 HTML/SVG 비교를 실시한다. 잘림·겹침 감소와 내용·가독성 비퇴행이 확인되지 않으면 생성 입력·배치 규칙·글꼴·이미지 표현을 먼저 개선한다. 신규 추출 형식 확대를 품질 달성의 대체물로 삼지 않는다.
3. **품질을 유지하며 추출 범위 확장**: DOCX 표/anchor/머리글 등 실제 그림 범위를 완성하고 PDF mask/회전/스캔 처리를 확장한다. 이어 SmartArt·벡터/혼합 도표의 영역 식별을 연결한다. 지원 범위별로 같은 품질 관문을 다시 적용하며 미지원은 명시한다.
4. **전체 문서·복구·호환 완성**: 100페이지 자산 재사용, 중단/취소/fence, PDF export, footer/순서 변경·재작성·복제·이력·GC를 완성한다. `svg_page_remake.py`의 data URI 복원 의존성을 해소하고 모든 reader/resolver를 확인한다.
5. **사용자 노출과 제품 인수**: 품질 관문과 해당 지원 범위의 기능·호환 인수를 모두 충족한 뒤 새 writer를 일반 활성화한다. 최초 품질, 최종 품질, 생성/내보내기 상태를 구분해서 표시한다.

초기 품질 관문은 전체 모드 개발의 중간 결정 지점이다. 표본 페이지 수를 제품 생성 상한으로 만들지 않는다. 내부 비교에서도 새 형식이 기존 문서로 유출되지 않게 capability를 제한하며, 기존 문서의 reader/수정 기능을 손상시키지 않는다. 이미 노출된 SVG 기능을 이 설계 편집만으로 끄거나 변경하지 않는다.

legacy data URI SVG는 읽기뿐 아니라 기존 수정·내보내기도 지원한다. 새 revision 작성 시 decoder 검증을 거쳐 원본/occurrence manifest로 승격하고 기존 revision은 유지한다. 변환할 수 없으면 기존 문서를 손상시키거나 기능을 조용히 비활성화하지 않고 실패 원인을 표시한다. rollback은 새 writer를 끄는 방식이며, 이미 v2로 저장한 문서를 읽지 못하는 서버 버전으로 무조건 되돌리지 않는다.

단순히 1.5MB 상수를 올리는 변경은 허용하지 않는다. 모든 reader와 resolver가 준비되기 전 slot writer를 켜거나, 제품 인수 전에 대형 첨부·DOCX/PDF를 안정 지원한다고 표시하지 않는다.

## 15. 인수 기준

- 1.5MB보다 큰 정상 PNG/JPEG/WebP가 포함된 PPTX·DOCX 및 큰 이미지가 포함된 PDF에서 model-preview preflight 후 생성이 진행된다. 배치 후 screen/export 파생본을 만들고 큰 원본에 기존 단일 이미지 제한을 직접 적용하지 않는다.
- 동일 이미지를 100페이지 후보로 사용해도 DB/checkpoint에 base64가 100회 복제되지 않는다.
- provider가 보는 이미지 bytes와 최종 SVG/PPTX 이미지의 source digest 연결을 확인할 수 있다.
- 원본·model-preview·screen-preview·export 파생본의 digest와 크기가 receipt에 남는다.
- 자동 제외가 허용된 장식 이미지 하나의 변환 실패는 다른 페이지 생성을 중단시키지 않으며 제외 사실이 남는다. 핵심·불명확 이미지는 자동 제외로 성공시키지 않는다.
- required 이미지의 preflight 실패는 해당 생성 단계 provider 호출 0회, `preflight_failed`로 종료된다. 원인을 수정한 뒤 같은 입력 또는 새 revision 정책에 맞춰 안전하게 재개한다.
- dispatch intent 전의 fenced 중단은 같은 ordinal로 재개한다. intent 저장 직후/송신 중/응답 저장 전 종료를 각각 확인하고, 응답 없는 불확실 호출은 보장된 idempotency/reconciliation 없이 자동 재구매하지 않는다.
- 프로세스 재시작 후 완료된 파생본과 페이지를 재사용한다.
- 최종 화면, PowerPoint 렌더와 PDF에서 placement가 일치한다.
- PowerPoint에서 텍스트·도형 편집성을 유지하고 이미지가 지정된 box와 비율 정책을 지킨다.
- 원본 파일, reference snapshot, owner/tenant binding과 artifact digest가 재시도·내보내기까지 보존된다.

### 15.1 형식별 필수 사례

| 입력 | 확인할 계약 |
|---|---|
| PPTX: 큰 사진·반복 로고·전체 화면 캡처 | 기존 수집 호환, bytes 재사용과 occurrence 보존, required 필터 우선 |
| DOCX: 본문/표/anchor 그림·캡션·머리글 | 실제 관계 참조, 문단/표 출처, 페이지 번호를 임의 생성하지 않음 |
| DOCX: 같은 media의 서로 다른 crop·회전, AlternateContent | occurrence별 appearance 유지, 대체 분기 중복 없음 |
| DOCX: 차트/SmartArt·외부 연결·미지원 media | 영역 렌더/미지원/외부 참조 구분, 조용한 누락 금지 |
| PDF: embedded/inline/Form 이미지·soft mask·회전 페이지 | 보이는 위치·색·투명도 복원, 객체/stream과 appearance digest 연결 |
| PDF: 벡터/혼합 도표·스캔 페이지 | region_render/scanned_page 구분, OCR 문맥, 원본 영역 중복 투입 방지 |
| 손상 DOCX·암호 PDF·자원 예산 초과 | no_images와 오류 구분, required면 provider 예약 전 실패 |
| 같은 bytes를 다른 작업·복제 문서에서 재사용 | owner/tenant 검사와 현재 operation ref 발급; 이력/활성 export의 GC 보호 |
| legacy SVG의 열기·재작성·순서 변경·복원·내보내기 | v1/v2 이중 읽기, revision 보존, stale publication 거부 |

추출 수만 세지 않고 원본별 expected occurrence와 실제 수집·선정·최종 사용을 대조한다. PDF 도표가 이미지 개체로 추출되지 않은 경우에도 coverage가 성공으로 둔갑하지 않아야 한다.

### 15.2 HTML 대비 품질 인수

이 관문은 구현 순서 2에서 처음 적용하며 모든 형식·운영 기능 완성을 기다리지 않는다. 후속 형식 확장과 최종 제품 인수에서도 유지한다. 기능 완료와 최초 생성 품질 판정은 별개이며, 전자를 달성했다고 후자를 완료로 표시할 수 없다.

기존 Authored OSB·현대백화점 스타일과 동일 입력/아웃라인/모델 preset/내용 이미지 후보/내용 요구를 사용한다. DOCX의 화면 캡처·한글 캡션, PDF의 복잡한 도표·스캔, PPTX의 큰 사진·밀집 자료를 포함한다. 초기 작성과 교정 후 결과를 따로 기록하며 형식별 모델 호출·비용·시간도 비교한다. 비교용 이미지 수집 결과는 동일하게 주입하되 HTML 제품의 기본 경로를 바꾸지 않는다.

- 내용·수치·required occurrence 누락은 양쪽 모두 허용하지 않는다. critical 이미지 미사용과 새로운 가독성 결함이 있으면 SVG 개선으로 판정하지 않는다.
- 최초 생성은 최초 provider 응답에 안전한 파싱·slot 해결·공통 고정 요소 적용만 한 결과로 정의한다. 측정 전에 텍스트 축소·줄바꿈 추가·좌표 이동·LLM 교정을 하지 않는다. 원문 응답과 최초 측정 artifact를 보존하고 결정적 보정 후/LLM 교정 후 지표는 별도 기록한다. 유효하지 않은 응답·렌더 실패도 전체 시도 수에서 빼지 않는다.
- 비교에는 동일한 내용 요구·실제 폰트·canvas·이미지 appearance와 동일한 호출/교정 예산을 적용한다. 포맷별 표현 지시 차이는 기록한다. 작은 글씨, 본문 삭제, 이미지를 통째로 페이지화하는 방법으로 겹침만 줄인 결과는 개선으로 인정하지 않는다.
- 최초 생성의 넘침·잘림·의도하지 않은 겹침 발생 페이지 비율과 심각도를 비교한다. SVG가 이 항목에서 개선되고 나머지 내용/가독성/스타일 보존이 악화되지 않아야 품질 우위를 주장할 수 있다. 양쪽 모두 0건이면 우위 미확인이다.
- 화면의 실제 폰트·이미지 로드 후 측정과 PowerPoint/PDF readback을 함께 확인한다. 검증 생략은 미검증이며 성공이 아니다. native 텍스트·도형의 실제 편집 가능 여부와 추출 도표의 raster 특성을 따로 보고한다.
- 반복 표본의 문서별 결과·최악 페이지를 남겨 일부 좋은 페이지로 전체 품질을 대표하지 않는다. 최초 비교는 형식/스타일별 2회 이상을 후보로 하되 표본 확대는 사용자 요청과 프로젝트 검증 정책을 따른다.

판정은 `improved`, `regressed`, `inconclusive`로 남긴다. 대표 입력에서 넘침·잘림·의도하지 않은 겹침의 발생 페이지 비율이 줄고 심각도가 악화되지 않으며 내용·수치·필수 이미지·가독성·스타일이 비퇴행일 때만 관문 통과로 판정한다. 반복 결과의 방향이 엇갈리거나 양쪽 모두 결함 0건이면 inconclusive이며 우위를 선언하지 않는다. 그 경우 실제 HTML 결함 입력과 SVG 약점 사례를 추가해 원인을 확인하고, 결과를 보고 다음 작업 범위를 정한다. 비용·생성 성공률 개선으로 품질 실패를 상쇄하지 않는다.

### 15.3 검증 실행 범위와 남은 확정 사항

이 문서의 사례는 향후 제품 인수 계획이며 이번 설계 편집에서 실행하지 않는다. 구현 단계마다 변경 경계에 맞는 가장 좁은 검증 하나를 선택한다. 전체 E2E·성능 측정·실제 provider 비교는 해당 실행이 승인된 작업 범위에서 진행한다.

1.5MB 초과 이미지와 100페이지 선택은 합성 fixture로 재현하되 이것만으로 실제 실패 문서가 복구됐다고 보고하지 않는다. 실패 문서 `c31c3497-8a36-4b82-a21b-d683cba7f6e2`의 제품 검증 시 기존 권한이 있는 snapshot을 참조하며 원본을 저장소 fixture로 복사하지 않는다. 새 생성·복구 revision의 실제 결과와 provider 영수증을 확인한다.

구현 전 capability 확인과 초기 활성화 인수에서 확정할 항목은 PDF 객체/mask 추출 backend, DOCX 복합 개체의 영역 매핑 지원 범위, §6 profile 수치, 기존 provider별 idempotency/reconciliation 지원이다. 미확정 항목은 candidate로 유지하고 라이브러리 지원·품질 우위·성공 비용을 추정으로 확정하지 않는다.
