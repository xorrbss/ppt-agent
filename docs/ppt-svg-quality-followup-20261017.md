# SVG 품질·문단 보존·계보 검증 후속 작업

2026-10-17 초기 후속 구현 커밋은 `0925eeeb3`이며 이후 보완과 실제 결과는 아래에 기록한다. 기존 12쪽 완료 근거는 [앞선 구현 보고](ppt-llm-header-implementation-20261017.md)에 유지한다. 이번 변경으로 과거 산출물이나 실패·UNKNOWN·blocked 기록을 수정하거나 성공으로 재분류하지 않는다.

최종 실제 결과는 A4 문서 revision 2의 정상 게시·웹 다운로드·Office 4쪽·native 원문 보존·source QA·현재 artifact fidelity까지 통과다. 기발급 export receipt의 `releaseReadiness=blocked`는 유지하며 formal READY나 release 완료를 의미하지 않는다. 아래 초기 실패와 진행 상태는 해당 시점의 기록이며 최종 binding과 근거는 마지막 절에 구분한다.

## 문제와 변경 범위

- 기존 12쪽의 7쪽에는 조건 설명의 높은 밀도와 약한 경고 강조가 남았다. 자유 SVG 작성 지침에 의미를 제한하는 전제·금지·예외를 구분해 상대적으로 강조하고 충분한 본문 공간을 배정하도록 추가했다. 모든 조건을 굵게 만들거나 작은 하단에 몰아넣지 않으며, 원문에 없는 경고·금지를 만들지 않는다. 페이지·템플릿 특례는 없다.
- 기존 12쪽의 8쪽 표에서 줄바꿈이 사라진 직접 원인은 검증용 입력 생성기의 공백 정규화였다. 이를 제품 export의 문단 손실 결함으로 기록하지 않는다. 이번 제품 보강은 새 Markdown 원문의 명시적 표 셀 문단을 전달·검증하는 별도 범위다.
- `native_requirements.py`는 속성 없는 `<br>`·`<br/>`를 셀 LF로 전달한다. 원문 문단의 비공백 문자 위치와 빈 문단 경계를 보존하고, 문단 안의 추가 시각 줄바꿈은 허용한다. 속성 포함 태그나 HTML escape를 새로 해석하지 않는다. 머리글·구분선·본문이 있는 정상적인 한 열 Markdown 표도 지원한다.
- `native_declarations.py`와 native 품질 판정은 원문 문단 경계를 그림과 native JSON에 유지하도록 요구한다. 공백 정규화한 문자 일치만으로 문단 손실을 통과시키지 않는다.
- `svg_partial_recovery.py`는 한 검증 호출의 동일 session·transaction·owner·tenant 안에서 성공한 historical episode 증명을 공유한다. 현재 코드 검증이 필요한 호출은 memo로 생략하지 않는다. 기존 archived 행은 각각 정확히 비교하고, 원본 operation 소유 ledger 집합의 추가·변경·삭제는 거부한다. 같은 job에 정상 preview accounting이 검증 전에 추가되는 것은 허용하되, 검증 시작 시점의 현재 전체 ledger 집합을 종료 직전 fresh 조회와 비교해 검증 중 변화를 거부한다. 순환, 출처·operation reference·payload seal과 producer checkpoint 집합 변경도 거부하며 durable 증명이나 다른 호출의 cache로 승격하지 않는다.

자유 SVG 본문을 고정 카드·슬롯·레이아웃 목록으로 바꾸지 않았으며, 원문 내용·수치·페이지 수를 줄여 통과시키는 방식도 사용하지 않는다.

## 좁은 검증

root가 실행한 최종 검증 결과는 다음과 같다. 전체 suite·빌드·전역 보안 검사 결과가 아니다.

| 변경층 | 기존 검증 항목 | 결과 |
|---|---|---|
| 작성 지침 | `test_author_slide_svg_asset_offer.py::test_writing_policy_routes_copy_corrections_without_unlocking_verbatim_sources` | 3 parameter 통과, 5.08초 |
| native 원문·문단·한 열 표 | 기존 해당 node의 parameter 검증 | 22개 통과, 5.06초 |
| recovery memo·거부 경계·preview accounting 호환 | 기존 해당 node | 최종 1개 통과, 9.82초 |

독립 읽기 전용 코드 검토에서 최종 memo scope와 한 열 표·break parser 변경의 차단 문제는 발견하지 못했다. 이는 실제 생성·게시·PowerPoint 결과를 대신하지 않는다.

## 실제 PostgreSQL 비교 측정

출처 operation `25b1cba5-1650-4e29-a387-c1d3e1e0357d`의 historical episode 검증 경로(`require_current_code=False`)를 같은 PostgreSQL `REPEATABLE READ READ ONLY` snapshot에서 최종 ledger 호환성 수정 후 비교했다. 두 결과 모두 accepted이며 manifest digest는 `82c6d484d60787ce0d068f322676697cd9a6b0fc2afb34b483f59e98f7cf9c2f`로 정확히 같다.

| 항목 | 변경 전 | 변경 후 |
|---|---:|---:|
| 실행 시간 | 94.845925초 | 52.550141초 |
| verify_episode 호출 | 32 | 16 |
| 전체 episode 검증 | 32 | 6 |
| DB read | 414 | 218 |

최종 근거는 [ancestry-comparison-02-final-ledger-compat.json](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/ancestry-comparison-02-final-ledger-compat.json)에 보존하며, 앞선 [ancestry-comparison-01.json](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/ancestry-comparison-01.json)도 그대로 남긴다. 실제 provider 호출이나 DB 변경 없이 측정한 한 출처·한 깊이의 historical 검증 시간이다. 전체 생성 시간이나 전체 recovery-plan 처리 시간, p50/p95는 측정하지 않았다. 각 archive의 ancestor 참조 목록을 읽고 대조하는 비용이 남아 계보 깊이 N에 따라 N²로 늘어날 가능성이 있으므로, 모든 깊이의 선형 성능이나 전역 속도 개선을 주장하지 않는다.

## 첫 A4 실제 검증과 후속 보완

기존 비PPT A4 입력의 4쪽·내용·수치·조건·담당·일정을 유지했다. 4쪽에 이미 있던 확인 문장 3개만 한 열 표의 단일 셀에 `<br>`·`<br/>` 문단으로 배치했다. 기존 원본 SHA 불변과 역변환한 문장의 정확한 일치, 요청 스키마를 offline으로 확인했다. parser는 네 페이지에서 표를 각각 하나씩 인식하며 4쪽 해당 셀의 3문단을 확인했다. 이 입력 증거는 실제 artifact·Office 증거가 아니다.

새 요청은 정상 API에서 HTTP 202로 접수됐다. source operation은 `07292304-d9dd-4d33-af53-608485398ac0`, 새 문서는 `5d7e99f1-6d8b-5751-ba2f-48c135dd40b3`다. 중복 POST나 자동 재시도 없이 한 번 제출했다. 기존 서버 정책을 유지하며, 이전 12쪽 recovery 전용 $1 추가 예산을 이번 fresh 생성의 한도로 기록하지 않는다. 실제 적용 한도·사용량·호출 수는 완료 근거에서 별도로 확인한다.

첫 source operation `07292304-d9dd-4d33-af53-608485398ac0`의 생성·게시, export `263c9b7b`의 완료, 브라우저 다운로드와 실제 Office 4쪽 확인을 완료했다. [native·문단 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/followup-a4-paragraph4-native-paragraph-proof.json)는 native 표 4개·fallback 0개, 46개 셀의 원문 일치, 수치 24개 coverage와 4쪽 해당 셀의 원문 3문단 경계 보존을 확인했다. 이는 문단 내용·경계의 보존 근거이며 문단 간 시각 간격의 일치와 구분한다.

별도 최종 QA `a9c47d4f`는 operation 자체는 완료됐지만 2·3쪽의 `empty_band`와 4쪽의 `unbalanced`를 보고했다. 현재 artifact fidelity는 `failed`다. 독립 육안 평가는 새 A4 93점·기존 A4 94점이며, 주관적인 명목 1점 차이로 개선 효과를 입증하지 않는다. export·문단 보존 성공이나 독립 점수로 최종 QA 실패를 덮어쓰지 않는다.

원인 확인에서 2·3쪽의 빈 띠는 source SVG에도 존재했다. 4쪽은 source의 문단 baseline이 717·809·911px로 서로 다른 간격인데, PPTX의 세 문단 `pPr`에는 해당 간격이 없고 같은 행 높이·중앙 정렬로 배치됐다. 원문 문단 경계는 보존됐지만 native 변환의 문단 간격 정보는 손실됐다. source 균형을 유도하는 범용 작성 지침을 추가한 뒤 기존 좁은 검증 3개가 4.56초에 통과했다. 이후 native 문단 geometry 보강과 실제 후속 검증은 아래에 별도로 기록한다.

후속 입력 `followup-a4-paragraph4-balanced-v2-generation-request.json`은 첫 입력의 `request` 전체를 정확히 복사했다. 원문·지침·수치·4쪽 수는 바꾸지 않았으며 새 idempotency key와 첫 operation을 가리키는 로컬 replacement lineage를 준비했다. 요청 스키마·deep equality·첫 envelope SHA 불변을 offline으로 확인했다. 후속 구현·진단과 실제 접수 상태는 아래에 별도로 기록한다. 기존 모든 증거 파일과 첫 QA 실패를 보존한다.

증거 경로는 `C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017`이다.

기존 12쪽의 Office 92점은 앞선 특정 산출물의 평가다. 이번 코드 변경의 새 시각 점수가 아니며, 기존 7쪽이 자동 수정됐다는 뜻도 아니다. 후속 A4의 실제 결과는 별도 근거로 구분한다. 기존 receipt의 blocked와 formal READY 미평가 상태도 유지한다.

## 정책 확인과 문단 간격 보강

첫 요청의 공개 정책은 `interactive-v1`이지만 실제 snapshot에는 내부 `svg-quality-first@2`가 적용됐다. source QA는 full scope에서 네 페이지의 개별 PNG를 검토했고 모두 `density_balance=passed`, 문제·개선 대상 0으로 판정했다. 추가 aesthetic repair는 failed axis나 명시적인 개선 대상이 있어야 실행되므로 첫 검토 한 번으로 종료됐다. `visionQa=false` 때문에 source QA를 생략한 것이 아니다.

최종 QA는 별도 Office artifact PNG와 다른 검토 문맥에서 `empty_band`·`unbalanced`를 보고했다. 2·3쪽은 source에도 빈 띠가 있어 검토 판정의 차이가 확인되며, 4쪽에는 별도의 native 변환 문단 간격 손실도 있었다. 공개 정책은 `interactive-v1`·`release-v1`만 지원하고 `quality-first`는 내부 정책명이다. `release-v1` 또는 `visionQa=true`로 변경해 첫 실패를 우회하지 않으며, 후속 요청 본문도 동일하게 유지한다.

후속 구현 커밋 `38fb6a32b`는 자유 SVG 본문 전체의 공간 균형 지침과 native 표의 선택적 `line_spacing`·`space_before`를 보강했다. source의 px geometry를 문단 간격으로 전달하며 기존 rich style·글꼴·내용·병합·행 높이·명시적 padding의 우선순위는 보존한다. vendor manifest는 변경한 파일의 hash 한 개만 갱신했다. 기존 좁은 native 단위 항목 1개가 2.50초에 통과했고 독립 코드 검토에서 차단 문제는 발견하지 못했다.

무료 진단 산출물은 native 표 4개·fallback 0개이며 실제 Office 4쪽 열기·렌더와 세 문단의 분산된 간격 복원을 직접 확인했다. [painted glyph 간격 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/paragraph-spacing-diagnostic-01/painted-glyph-spacing-proof.json)의 두 간격은 기대 60.5142·67.0918pt, 관측 61.3839·66.9643pt다. 차이는 각각 +2.3378px·−0.3429px이며 첫 간격에 약 0.87pt의 렌더 차이가 남는다. 탐색용 2px 엄격 기준에서 `passed=false`인 원본 결과를 그대로 보존했다. 전체 문단 간격의 복원 확인과 strict pixel match는 구분하며 픽셀 일치 성공으로 기록하지 않는다. 이 진단을 정상 웹 게시나 최종 QA 통과 증거로 사용하지 않는다.

두 번째 동일 입력 fresh A4는 HTTP 202로 접수됐으며 source operation은 `47e3f6a7-53ba-420a-8a70-58f7b519d8ba`, 문서는 `7a93f776-5688-51c0-a52f-978360c87639`다. 첫 표본의 성공·실패와 이전 진단 증거는 유지하며 두 번째 결과와 후속 선택 페이지 수정 상태는 아래에 구분한다.

## 두 번째 A4 결과와 선택 페이지 수정

두 번째 fresh A4는 생성·게시·내보내기와 실제 Office 4쪽 확인을 완료했고 native 표 4개·fallback 0개, 46개 셀·수치 24개·원문 3문단 보존을 확인했다. 문단 간격 보강 뒤 4쪽의 `unbalanced`는 최종 QA에서 해소됐다. 그러나 최종 QA `62041247`은 1·2·3쪽에 `empty_band`를 보고했고 현재 artifact fidelity는 `failed`다. 독립 평가는 94점으로 첫 표본 93점·기존 A4 94점과 구분하며, 이를 최종 QA 통과로 사용하지 않는다.

같은 문서 revision 1의 index 0·1·2만 공식 `slide.regenerate.preview`로 수정 후보를 만들고, 후보 확인 후 `slide.regenerate.batch`로 게시하는 경로를 준비했다. 원문·native 표와 미선택 index 3의 세 문단을 유지하며 전체 fresh 생성을 다시 반복하지 않는다. 이 경로는 이전 SVG와 구체적인 `empty_band` 피드백을 LLM에 전달하지만 실제 Office PNG를 직접 전달하는 typed 입력은 없다.

첫 preview `34968c45-2898-4e8b-b745-505f07553763`는 provider logical call 0인 상태에서 `svg_common_header_digest_mismatch`로 실패했다. 공개 timeline의 일반 실패 분류와 raw operation의 실제 원인코드를 구분한다. preview가 저장된 common-header v2 계약은 전달하면서 페이지별 frozen binding을 복원하지 않은 연결 누락이었다. source QA 원칙 강화나 provider UNKNOWN이 원인은 아니다. 원 실패 operation·command·response·terminal 증거는 그대로 보존한다.

범용 수정은 저장된 outline 전체와 해당 index의 원문·제목 출처를 기존 validator로 검증한 뒤, 권위 있는 binding을 author·finalize에 전달한다. preview와 publish는 실제 furniture attestation 및 기존 header quality 측정을 사용하고 그 결과를 게시 receipt에 유지한다. digest 검사를 완화하거나 저장된 계약을 재바인딩하지 않는다. 기존 preview header 검증 node 1개가 5.55초에 통과했다.

source QA의 새 검토 원칙은 legacy 호환 경계를 유지하도록 별도로 보강했다. 최종 좁은 검증 2개가 5.55초에 통과했고 기존 legacy 원칙은 1,796 bytes 및 SHA가 동일함을 확인했다. 이는 새 실제 source QA·최종 PPTX QA 통과 근거와 구분한다.

해당 코드 수정은 `fa669a208`로 runtime에 반영됐다. 새 page0 replacement preview `6dc07a7e-afc0-4460-a4bf-37361b112f46`를 포함한 선택 3쪽의 preview는 모두 succeeded·preview_ready·restorable로 완료됐다. 실제 사용량은 페이지별 author 1회씩 reported이며 이전 실패를 재라벨하지 않는다. 이후 게시 문제와 최종 revision 2의 실제 결과는 아래에 구분한다.

## 선택 페이지 게시의 tenant·잠금 문제와 보완

- **tenant 연결:** batch apply `31e2d343-0ce1-4579-9231-881bff3f953f`와 후속 `5560` 식별자의 실패는 그대로 보존한다. 기존 단일 preview·regenerate와 달리 batch 게시의 principal 연결이 누락돼 source artifact의 tenant 문맥이 달랐다. root가 실제 PostgreSQL 읽기 전용으로 확인한 [tenant 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/batch-principal-tenant-readonly-proof.json)와 [최초 실패 상세](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/targeted-batch-failure-detail.json)를 유지한다. 보완은 기존 presentation principal 복원 경로에 batch를 연결하는 범위이며 소유권·tenant·artifact 검사를 완화하지 않는다.
- **원인 진단:** 일반 `page_remake_publish_failed`는 원 예외를 포장하므로 원인코드와 구분한다. 최소 로그는 operation ID·예외 클래스·마지막 frame의 파일명·함수·줄 번호만 기록한다. 오류 원문·본문·자격 정보·SQL 매개변수·전체 traceback은 기록하지 않는다. [안전한 실패 위치 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/batch-safe-cause-location-proof.json)는 기존 실패 근거와 함께 보존한다.
- **self-lock:** batch `17e1dc7e-0baf-4c07-8295-555abfa825de`는 주 게시 transaction의 asset 재결합이 operation 행을 `SELECT FOR UPDATE`로 잠근 뒤, 페이지별 `context.progress`가 별도 session에서 같은 행의 UPDATE를 기다리는 구조였다. 주 transaction도 progress 완료를 기다려 자체 대기가 발생했다. [읽기 전용 잠금 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/batch-publication-lock-proof.json)는 row lock과 연결별 대기를 확인하며 raw SQL은 수집하지 않았다.
- **정상 취소와 rollback:** root는 공식 cancel action을 적용하고 해당 generation worker만 중지해 원 transaction을 rollback했다. DB 직접 terminate·operation reset·과거 상태 덮어쓰기는 하지 않았다. rollback 후 문서 revision 1과 원 source가 유지됐다는 root의 읽기 전용 근거를 보존한다. 당시 cancelling과 lease 잔여 상태였던 원 operation은 최종 lineage 확인에서 정상 cancelled로 완료됐다.
- **최소 수정과 보존 계약:** `aeecba249`는 transaction 안에서 대기하던 페이지별 progress 11줄을 제거했다. 기존 바깥 진행 보고, 페이지별 savepoint, batch의 한 transaction·한 revision 게시, 원 preview/source digest 검증, owner·tenant·lease·fence·revision CAS, 원문·native 표와 미선택 4쪽 보존 요구는 유지한다. 기존 좁은 node 1개가 6.04초에 통과했고 독립 읽기 검토에서 차단 문제는 발견하지 못했다. 이는 실제 게시나 품질 통과 결과가 아니다.

새 batch `45865f02-44c1-4d31-b2a8-598f12431d62`는 후보를 다시 생성하거나 기존 실패 operation을 재실행하지 않고 보존된 preview 3개를 사용하는 별도 게시 요청이다. 실제 attempt 1회에 succeeded로 완료됐으며 최종 결과는 아래에 기록한다.

## 최종 revision 2의 실제 결과와 제한

문서 `7a93f776-5688-51c0-a52f-978360c87639` revision 2가 정상 게시됐다. [선택 범위 검증](C:/project/PPT-agent/delivery-backups/svg-header-completion-20261017/followup-a4-targeted-r2-published-targeted-scope-proof.json)은 문서·4쪽 수·페이지 index와 ID 보존, 선택 index 0·1·2의 SVG 변경, 미선택 index 3의 SVG UTF-8 bytes 불변을 통과했다. 이 비교 자체로 제목·본문 전체 의미의 동일성을 주장하지 않는다. [최종 operation lineage](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/targeted-operation-lineage-final-proof.json)는 원 `17e1dc7e` cancelled와 새 `45865f02`의 attempt 1 succeeded를 확인하며 과거 실패는 보존한다.

처음 시도한 revision 2 export `feb0a8ff-e651-4ab3-80fa-c1385994f32e`는 `svg_quality_recovery_export_review_required`로 실패한 원본을 유지한다. 수정된 페이지에는 이전 source QA를 재사용할 수 없으므로 공식 source QA `355a5486-49bc-4ab2-aae7-cc289b3abd35`를 별도로 시행했다. [최종 source QA 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/targeted-r2-source-quality-final-proof.json)는 새 원칙 v2로 revision 2의 네 페이지 전체 통과를 확인한다. source QA가 SVG를 추가 수정한 결과로 기록하지 않는다.

새 정상 export `5d553465-bc1d-4f69-8652-3e9668333269`가 succeeded로 완료됐고 artifact `b492b44d-3476-486f-8bf6-9f01b2b486bb`를 실제 웹에서 다운로드했다. 다운로드와 artifact의 SHA-256은 `85a358916ff97eaa5a8daf1ce51689e559a3375bed1a984bc6cc05cfde67ff09`로 일치한다. 별도 최종 PPTX QA `412b7aa6-c13c-41f3-b650-4b8fd078a406`가 완료됐으며 [최종 웹 artifact 근거](C:/project/PPT-agent/delivery-backups/svg-header-completion-20261017/followup-a4-targeted-r2-final-web-artifact-proof.json)의 HTTP 200 이력은 동일 revision·artifact·digest에 결합된 `currentArtifactFidelity.status=passed`를 확인한다. 이전 revision 1 artifact의 failed·stale 검토는 그대로 보존했다.

[실제 Office 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/followup-a4-targeted-r2-office/office-proof.json)는 4쪽 열기·렌더가 passed다. [최종 native·문단 근거](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/followup-a4-targeted-r2-native-paragraph-proof.json)는 표 4개·fallback 0개, 46개 셀·원문 3문단 경계·수치 24개를 확인했다. 전체 spacing 복원과 앞선 진단의 최대 약 0.87pt 잔여 렌더 차이는 구분하며, 2px strict 진단의 `passed=false` 원본도 유지한다.

[최종 독립 시각 검토](C:/project/PPT-agent/delivery-backups/svg-quality-followup-20261017/followup-a4-targeted-r2-office/independent-visual-review.json)는 실제 Office 화면 8장을 같은 5개 항목·각 20점 기준으로 비교해 새 revision 2를 95점, 기존 A4를 94점으로 평가했다. 밀도 항목만 18→19점이고 다른 항목은 같다. 2·3쪽 빈 띠 감소를 확인했고 4쪽 PNG는 bytes가 동일하며 세 문단의 강조를 유지했다. 표 내부 여백은 여전히 넉넉하다는 한계가 있다. 별도 검토자의 주관적인 명목 1점 차이이며 범용 개선의 인과 증명·최종 QA·release 판정을 대신하지 않는다. 독립 근거의 final QA pending은 검토자가 전달받은 당시 입력 상태이므로 수정하지 않는다. 실제 최종 QA 완료와 fidelity passed는 위 canonical 웹 artifact 근거를 따른다.

기발급 export receipt는 `releaseReadiness=blocked` 그대로다. 해당 receipt의 필수 `visualReview`는 export 발급 당시 최종 PPTX 검토가 없어 `failed`, 이유는 정확히 `svg_final_pptx_visual_review_unavailable`다. 이후 별도 최종 검토로 현재 artifact fidelity가 passed인 사실과 이 과거 receipt를 구분한다. 선택 항목인 performance는 `not_assessed`·`svg_performance_evidence_missing`이며 전역 성능 통과 근거가 아니다. receipt를 다시 발급하거나 READY로 재라벨하지 않았고 formal READY·release 완료는 주장하지 않는다.
