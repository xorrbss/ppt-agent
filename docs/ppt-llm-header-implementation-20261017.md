# LLM 상단 디자인 구현 기록

기준 설계: [문서별 LLM 상단 디자인](ppt-llm-header-design-20261015.md).

## 적용한 동작

- 새 SVG 공통 상단은 두 후보를 한 번에 생성하고, 같은 원문의 대표 페이지를 후보별 상단과 본문으로 완성한 뒤 LLM이 화면을 비교해 선택한다. 선택한 원시 본문은 최종 페이지에 재사용한다.
- 본문은 기존 자유 SVG authorer를 사용한다. 본문을 새 IR, 카드, 슬롯 또는 레이아웃 목록으로 대체하지 않는다. 기존 v1 상단과 동결된 입력의 재생은 보존한다.
- 생성 및 상단 수정 화면에서 자유 디자인 요구와 선택적인 상단 보조 문구를 입력할 수 있다. API의 `common_header_design.document_values`와 `page_values`는 정확한 사용자 값과 출처를 안정적인 페이지 ID에 결합한다. 적용 페이지에서 고정 문구를 빠뜨린 후보는 거부한다.
- 상단 수정은 현재 revision과 작업 소유권을 검사한다. 본문 영역과 공유 스타일을 보존할 수 있으면 기존 본문을 재사용한다. 후보와 사용량, 이전 게시 및 검토 증거는 보존한다.
- 상단 수정에서 메타데이터 scope를 생략하면 현재 값과 출처를 계승하고, 명시적인 빈 객체나 배열이면 해당 scope를 제거한다. 수정된 revision은 이전 검토를 새 READY 증거로 재사용하지 않는다.
- 상단의 글자와 도형 전체를 편집 가능한 PPTX readback 대상으로 연결한다. OOXML 검증과 실제 PowerPoint 편집·저장·재열기 증거는 구분한다.
- 상단 호출 한도는 일반 2회, 예약 수정과 재선택을 포함해 최대 4회다. 최초 본문은 정상 경로 N+1회이며 제목·최종 QA·수정 비용은 별도다. UNKNOWN을 새 호출로 자동 대체하지 않는다.
- 상단 수정의 동일 작업 재개는 저장 결과를 사용한다. 명시적 terminal retry는 새 child lineage에서 확정된 결과와 원 누적 횟수·비용 상한을 승계한다. 원 실패 행·본문·영수증은 보존하며 UNKNOWN, 미정산, 증거 누락과 오래된 문서 버전은 거부한다.

## 현재 확인한 범위

좁은 입장·재생 검사 33개, 권한 및 호출 한도 교차 차용 거부 검사 1개, 페이지 ID 저장·복원과 실제 고정 문구 결합 검사 2개, 저장된 실제 후보의 글자 측정 회귀 검사 1개, 실제 자식 반환에 따른 native group alias 회귀 검사 1개가 통과했다. 전체 테스트·빌드 또는 대규모 성능 측정의 결과는 아니다.

마지막 연결 변경에 대응하는 기존 내보내기 권한 검사와 v2 저장 검토 검사 2개도 통과했다. 실제 누락을 수정한 뒤 확장된 v2 검사만 다시 실행해 통과했으며, 반복 실행은 별도 항목으로 세지 않는다. 총 40개 고유 항목의 좁은 검증 근거다.

PostgreSQL은 전용 마이그레이션 권한으로 `f4a6c8e0b2d5`에서 `f5b7d9e1a3c6`로 갱신했다. 작업과 상단 후보의 nullable JSON 체크포인트 필드를 추가했고, 기존 generation job 489개와 workspace operation 1152개의 수가 유지됨을 확인했다. API와 생성·내보내기 컨트롤러만 관리 스크립트로 갱신했다.

실생성에서 발견한 페이지 ID 준비 누락은 제공자 호출 전에 안정적인 ID와 제목을 저장하도록 수정했다. 이미 호출한 과거 후보에 새 ID를 덧붙여 재라벨하지 않는다. 첫 실패와 뒤이은 측정 실패의 원본 작업·결과·사용량은 보존했다.

브라우저의 실제 글자가 정적 글꼴 예상 paint box보다 조금 크다는 이유로 발생한 거짓 넘침 판정도 수정했다. 허용 영역은 변환된 `field.box`이며, 실제 ink가 상단·본문·브랜드 경계를 넘거나 다른 상단 글자와 겹치는지는 별도로 검사한다. 기존 후보 두 개의 저장된 실제 측정값은 통과했고, 영역 침범·겹침·측정 누락·폰트·잘림·불투명도·그룹 변조의 거부 회귀도 같은 좁은 검사에서 확인했다. 이 offline 결과를 과거 실패 행의 성공 재분류나 새 게시 증거로 사용하지 않았다.

실제 2페이지 생성 `cb31b16f-4353-48ce-8ee2-69001584cb9c`는 성공했고 문서 `12130088-8098-5697-8194-4f7fe025bdd4` revision 1로 게시됐다. 대표 두 안은 각각 검증을 통과했고 LLM이 선택했다. 최초 본문 호출은 3회로 N+1을 확인했으며, 선택 후보의 원시 본문과 최종 체크포인트 본문이 정확히 같다. 상단은 첫 후보 수정 1회를 포함해 3회, 최종 QA는 1회, 기존 illustrate 단계는 별도로 1회였다. 이 한 표본의 작업 실행 시간은 174.42초이며 성능 향상을 의미하지 않는다.

native 변환에서 빈 그룹과 실제 도형 하나가 함께 있는 경우, 실제 반환한 도형 하나를 부모가 그대로 반환하는 정상 alias를 중복 도형으로 판정하는 문제도 수정했다. 태그 이름이나 상단 ID의 특례 대신 실제 직접 자식의 반환 결과와 완전한 OOXML signature로 alias를 증명한다. 기존 중복 소유와 geometry 거부는 유지한다.

동일 문서의 상단 변경 `26c7c03a-86dc-4127-bc5a-75fa95e6a6c5`는 revision 2로 게시됐다. 실제 사용량 원장에서 확인한 호출은 상단 후보 생성과 선택 각 1회이며 본문 호출은 0회다. 두 페이지의 slide ID, 원문, 원시 본문 및 native objects는 수정 전과 정확히 같고, 생략한 보조 문구 scope는 출처와 함께 계승됐다. 이 한 표본의 작업 실행 시간은 86.26초다. 이전 시각 검토는 새 revision의 통과 증거로 사용하지 않는다.

실행 근거는 저장소 밖 `C:\project\PPT-agent\delivery-backups\svg-llm-header-v2-20261017`에 보존한다. revision 1 editable-hybrid PPTX 1건은 생성·다운로드 SHA 일치 및 native OOXML 통과까지 확인했다. 당시 releaseReadiness는 최종 PPTX 시각 검토 미실시로 blocked였으므로 READY 완료로 기록하지 않는다. revision 2의 내보내기·최종 검토·Office 결과는 아래에 구분해 기록한다. 이전 v1 산출물이나 단위 검사를 신규 v2의 디자인 품질·출시 준비 완료 증거로 사용하지 않는다.

## revision 2 최종 확인

상단 수정 경로에서 일반 생성의 최종 품질 분류가 빠진 것을 실제 저장 자료로 확인했다. 새 수정은 기존 `classify_pages`를 거쳐 게시한다. 이미 완료된 자료는 원본 행과 체크포인트를 바꾸지 않고, 통과 측정·실패 없음·terminal/flag/inflight 없음·분류 부재인 경우에만 기존 classifier를 적용한 복사본으로 검증한다. 내보내기, 검토 준비와 품질 상태 계산은 동일한 서버 검증 권한을 사용한다. 검토 준비에는 검증된 원래 outline의 상단 binding을 그대로 전달한다.

새 source QA `1b2dc8a8-eb01-40ec-b114-5a50459847f8`는 현재 두 페이지의 측정·의미·필수 품질 검사를 통과했고 차단 페이지는 없다. 브랜드 표현의 minor 권고는 남았다. v2 source `analyze_only`의 비차단 권고와 generation 개선 반복의 종료 조건을 구분하며, 실패·미평가·필수 증거 누락을 통과시키지 않는다. 기존 generation의 `satisfied` 조건은 유지한다.

revision 2 내보내기 `d28b40d2-8546-4aeb-ba1b-256e8a648d4f`가 성공했다. 실제 artifact `0f18071a-915f-45ee-a9f1-6f3c71141ab5`의 다운로드 SHA-256은 `1ce45c6e725100a9f8cb38cad9d9a669c49cf7d526f7e9e0ff1413d63fcccd6c`이며, 패키지의 두 페이지에 각각 native table이 하나씩 있다. source QA와 별개로 시행한 최종 PPTX QA `e598b05e-b987-4f91-a29d-2e96c3cbc278`도 문제 0건으로 완료됐다. 실제 원래 소유자와 tenant 문맥에서 현재 artifact fidelity가 `passed`임을 재검증했다. 기발급 내보내기 receipt의 당시 `blocked` 결과는 그대로 보존하며 READY로 재라벨하지 않는다.

실제 PowerPoint에서 두 페이지를 렌더링하고, 소유한 복사본의 보조 문구를 수정·저장·재열기하여 편집 가능성을 확인했다. 원본 파일의 해시와 기존 열린 문서는 보존했고 PowerPoint 전체 종료는 호출하지 않았다. 반복 상단의 보조 문구와 장식선 영역은 두 PNG에서 픽셀이 정확히 같다. 초기 화면 판독에서 의심한 누락은 반증됐으며, 그 가설로 변환 코드를 변경하지 않았다.

작업 이력 GET의 명시적인 로컬 개발 요청은 기존 principal context로 연결해 현재 artifact 검증에 tenant가 전달되게 했다. 일반 요청의 권한 처리와 다른 tenant 거부는 유지하며 새 tenant 추측이나 전역 fallback을 추가하지 않는다.

서버 갱신 후 기존 호환 이력 GET과 문서별 이력 GET 모두 실제 HTTP 200을 반환했고, 동일 revision 2와 동일 artifact의 `currentArtifactFidelity.status=passed`를 확인했다. 이 확인에 제공자 호출은 없었다. 이력에서 제외된 요청 본문은 검증 함수가 소유자·문서·완료 검토 작업·동일 tenant admission을 조건으로 명시적으로 조회하므로, 원래의 최소 조회에 모든 payload를 추가하지 않는다. 최종 API 결과와 해시는 `final-web-success-proof.json`에 보존한다.

## 미측정 및 제한

현재 2페이지 샘플만으로 기존 기준 PPT보다 우수하다는 품질 점수, 대규모 문서의 처리량 또는 p50/p95 향상을 주장하지 않는다. 여러 캔버스와 장문·이미지 중심 문서의 수용 범위는 별도 근거가 필요하다. 화면의 버튼 클릭 전체 흐름을 자동 브라우저로 검증한 결과도 아니다.


## 추가 구현과 실제 검증 근거

- 전송: 전체 표시값과 출처를 복원 검증하는 공유 값·짧은 출처 handle을 사용한다. 원 durable 입력과 replay digest는 보존하며 실제 prompt/schema/projection 근거를 호출 전 checkpoint에 기록한다. 합성 80쪽은 한도 내 수용을 확인했지만 모든 장문 80쪽의 수용이나 실제 시각 품질을 보장한 결과는 아니다.
- 정확성: 제목 role과 필수 필드의 schema 계약을 일치시켰다. 검증된 요청 고정값의 실제 줄바꿈만 작성 정책에서 보호한다. 본문 위반은 기존 1회 repair에 실제 노드·기호 위치와 glyph 경계를 전달한다. 빈 text 부모의 오판은 제거하고 자식 측정과 실제 경계 침범 검사는 유지한다. 벡터 아이콘 검사를 기존 repair 판정 앞에 배치했다.
- 웹: 상단 수정 버튼과 작업 이력 준비 전 내보내기 race를 수정했다. 실제 수정 후 버전 3의 PPTX 다운로드와 PowerPoint 2쪽을 확인했다. 이후 본문 영역 변경은 y=204,height=452에서 y=150,height=506으로 실제 바뀌며 버전 4로 게시됐다. author 3회, header 2회, retry 0회이며 실행 127.33초와 대기 326.82초를 분리 기록했다.
- 비PPT: 사용자 실제 PNG를 정상 업로드·스냅샷·시각 참조 경로로 사용해 2쪽을 게시했다. 원본 PNG 해시를 보존했고, 본문은 직접 확인한 원문을 전달했다. 자동 OCR 또는 승인된 raster redraw 증거가 아니다. author 3회로 N+1을 확인했으며 총 initial 7회, retry 0회, 실행 137.63초와 대기 89.37초였다. 브라우저 다운로드 PPTX SHA-256은 9d7d578b43ae71bd8c97e49c2f54cf145ea121162173dc80485bb994bbecacd4이며, 읽기 전용 PowerPoint에서 텍스트 42개와 native 표 1개를 확인했다.
- 비PPT 복구: style snapshot에 분리 저장된 업로드 참조를 정확히 복원한 뒤 원래 전체 digest로 다시 검증한다. 변조·누락·추가 참조는 거부한다. 이미 다른 문서에 귀속된 snapshot은 재바인딩하지 않고 같은 원본 bytes로 별도 asset/snapshot을 만든다.
- 다중 사용자: quota trigger가 전체 counter를 principal별 RLS 부분 합계와 비교하던 오류를 실제 겹친 요청에서 확인했다. f6c8e0a2b4d7 migration은 기존 계산과 행·RLS·role을 보존하면서 제한된 SECURITY DEFINER trigger 집계만 적용한다. 함수 owner 권한·search_path·정확 trigger binding을 제한하고 실행권을 축소했다. 독립 검토와 좁은 검사, PostgreSQL 적용 및 대상 원행 불변을 확인했다.
- retry 검증 범위: 실제 SQLite service/admission의 확정 호출 0건 source positive, 모의 settled ledger의 paid-known/body/ancestor/변조 거부, 실제 API stale revision 409를 구분한다. 실제 유료 body retry positive를 완료한 증거는 아니다. 기존 generation partial recovery는 selected representative receipt와 제자리 rebind 때문에 이번 원행 불변 조건에 사용하지 않는다.
- 보존과 제한: 모든 실패 operation/job/checkpoint 및 replacement manifest는 별도로 보존한다. 12쪽 기준 대비 최종 시각·Office 비교, A4와 웹 5쪽 최종 게시 검증은 진행 중이며 완료로 판정하지 않았다. 비용 원장의 0 기록은 실제 청구 금액이 무료라는 증거가 아니다.

## 추가 수용 검사에서 확인한 미완료 항목

12쪽 작업 a5676fd7-e3b3-4018-9e17-ce1a540c0088과 A4 작업 5c65e7d9-f5a9-4064-96d5-f2357a648d44는 각각 모든 페이지의 개별 측정을 통과했지만 `svg_quality_recovery_review_required`로 종료됐다. 최종 검토의 무결성·현재 SVG/PNG·작업 binding·coverage는 일치하며, 남은 디자인 개선 목표 때문에 `designImprovementStatus=satisfied`를 충족하지 못했다. A4의 표 색상 위계와 12쪽의 밀도 문제는 별개로 남아 있다. 실패를 게시 성공이나 실제 PPTX 통과로 기록하지 않는다.

기존 두 번째 author를 필수 오류 보정에 사용한 페이지는 이후 디자인 검토에서 새로 발견한 목표를 수정할 호출이 없었다. 3회 plan에는 검증된 후보 적용을 위한 별도 예약이 포함되며 일반 자유 author 3회를 의미하지 않는다. 이번 확인에서 호출 한도나 게시 기준을 완화하지 않았다.

기존 12쪽 비교 입력은 현재 API의 원문 문자열만 사용해 원본 9쪽 표의 7개 병합 관계를 빠뜨렸다. 따라서 당시 게시 전 웹 후보의 주관 평가 81점과 기준 86점은 품질 향상 근거가 아니다. 원본 editable PPTX를 정식 분석한 새 manifest에서는 12쪽, 텍스트 432개, 표 2개, 차트 1개와 해당 병합 7개를 확인했다. 원본 3쪽의 방향은 connector endpoint 대신 custom geometry로 표현되어 있으며 관련 명령과 좌표를 확인했다. 전체 원본 페이지 미리보기가 author에 자동 전달된다는 주장은 하지 않는다. 새 source admission은 차트의 `axis_semantics` 지원 판정에서 제공자 호출 전에 거부돼 원인을 별도 조사한다.

웹 5쪽 작업 0b7800f4-42fe-45da-948e-8475424c9b30의 브라우저 대기 종료 뒤에도 서버 작업이 계속된 것을 확인했다. 최종 실패는 표의 숫자·단위를 공통 단위와 셀로 나눈 표현에 따른 필수 문자열 3개 누락이다. 해당 검사와 한도를 보존하면서 실제 누락 literal을 최대 8개, 각 160자 이내로 기존 repair에 전달하도록 보완했고 좁은 검사 1개가 통과했다. 아이콘 누락은 기존 1회 repair에서 실제 복구된 것을 확인했다.

원본 PPTX의 native 화살표 끝·접속 속성은 명시된 값만 추출해 source fact에 보존하고 대상 shape ID를 같은 페이지 source ID로 연결한다. 과거 manifest는 새 extractor identity로 구분하며 덮어쓰지 않는다. 실제 PPTX 파싱부터 author fact 전달까지 좁은 검사 1개가 통과했다. 이 검사는 3쪽에 존재하지 않는 endpoint를 보존했다고 주장하는 근거가 아니다.

A4의 후속 독립 입력에서는 모든 수치·표·장문 제목과 고정 보조 정보를 유지하고 Markdown 줄바꿈을 정규화했으며, 수정 가능한 본문 표 스타일과 강조 위계를 최초 입력에서 명시했다. 작업 4f9e874c-383b-4882-96d7-44704e167236은 문서 5c13a709-5300-565a-80e1-7da90ed51e2f revision 1의 4쪽을 실제 게시했다. 브라우저에서 PPTX 내보내기 1회·다운로드 1회를 확인했고, SHA-256은 6a00ef28f889cc16778add32469f73d3668190239b4c734acac59bbd2b1bdef2다. 읽기 전용 PowerPoint에서 A4 비율 4쪽을 렌더링해 편집 가능한 텍스트 62개·표 3개와 날짜·담당·선행 관계 및 수치를 확인했다. 원본 해시와 기존 열린 문서를 보존했다. 이 작은 A4 결과는 기존 가로 12쪽 기준보다 우수하다는 근거와 구분한다.

웹의 새 5쪽 요청은 create 1회와 generation 1회로 접수됐고 작업 97d65a47-1711-4b3c-802b-5c97a01477f1이 문서 39b9580d-36c0-40cc-9261-b7c238166b81 revision 1로 게시됐다. create 이후 약 3분 16초는 content brief와 outline 스트리밍 단계이며 durable generation queue와 구분한다. 브라우저 내보내기 1회·다운로드 1회 및 읽기 전용 Office 5쪽 렌더링을 확인했다. PPTX SHA-256은 c865e5b3ad67f4994a1d693630ef09617a324241999d53e8e7f8c9fbacca5294이며 텍스트 88개와 native 표 1개가 있다. 원본 해시와 기존 열린 문서는 보존했다.

검증 코드만 갱신된 과거 게시본은 기존 digest를 바꾸지 않고 새 명시적 상단 수정 작업에서 원본 SVG·PNG·입력·본문·native 객체를 검증한 다음 현재 renderer로 실제 재측정한다. 새 revision의 전용 witness와 local receipt에 연결하며, 증거 누락·소유권·본문·native 값 동시 변조는 거부한다. 해당 경로에는 body author 호출이나 측정 실패 뒤 자동 작성 전환이 없다. 동일 작업 재개만 가능하며 다른 child 작업에 이전 소유 증거를 재라벨하지 않는다. 독립 리뷰에서 찾은 누락·동시 변조 경계를 보강한 뒤 기존 좁은 노드가 통과했다. 실제 새 revision 실행 결과는 별도 확인 전까지 성공으로 기록하지 않는다.

이후 실제 재측정 작업 a1aa6dc8-8502-46c9-902f-25461a894726이 문서 12130088-8098-5697-8194-4f7fe025bdd4 revision 5로 게시됐다. 확정 사용량 원장에는 상단 2회만 있고 본문 호출은 0회이며, 실행 86.84초와 대기 0.91초를 분리 확인했다. 현재 버전의 브라우저 내보내기·다운로드와 읽기 전용 Office 2쪽도 통과했다. PPTX SHA-256은 e9d78dd212b73a74ee25c0171fbf273cad969937a0ab89e80bc18e4f215bbf06이며 텍스트 8개·native 표 2개를 확인했다. 과거 revision 4의 실패 내보내기와 옛 verifier digest는 변경하지 않는다.

12쪽에 원본 시각 참조와 native 의미를 함께 전달한 후속 입력은 최초 상단 호출 전 용량 한도에서 거부됐다. 동일한 `style.design_pack_template`과 `header_requirements.reference_content`를 v3 전송에서 한 번만 표시하고 명시적 alias로 연결한다. 원본 전체 subtree·canonical digest·출처 매핑은 복원 검증하며 v1/v2 재생도 보존한다. 실제 실패 job의 원 asset·선택 참조를 포함한 읽기 전용 재구성에서 context는 32,687→30,023 bytes로 줄었고 요청 31,835 bytes가 기존 32,768 한도 안에 들어왔다. raw 입력과 원 dump는 불변이며 한도를 늘리지 않았다. 변조·누락·타입 차이 검증을 포함한 기존 좁은 metadata 노드와 독립 코드 검토도 통과했다. 이 preflight는 새 12쪽 게시나 품질 우위의 증거가 아니다.

원본 PPTX의 선택 참조 이미지는 실제 파일의 내장 thumbnail에서 유래했지만, 변환 결과 256×192 PNG 전체가 흰색임을 확인했다. 원본·내장 thumbnail·파생 PNG의 해시는 일치하며 mock은 아니다. 실제 페이지 시각 참조로 인정하지 않고, 새 비교 입력에서는 기준 게시 화면의 PNG를 명시적으로 업로드했다. 이 대표 PNG 한 장을 12쪽 전체의 페이지별 시각 전달로 주장하지 않는다.

실제 대표 PNG를 사용한 작업 efce298f-bb07-4f93-84a7-cdc41753be9f는 12쪽을 렌더링했지만 1·8쪽의 가운데점 각 1개로 종료됐다. 원문·수치·관계의 주관 시각 평가는 복구됐으나, 같은 고정 평가표에서 기준 86점 대비 후보 85점이어서 품질 향상 목표도 미달이다. 문서 전체 VQA·게시·신규 Office는 완료되지 않았다. 9쪽 8×6 표와 병합 7개, 3쪽 5개 노드·순방향 연결 4개는 실제 SVG와 native 투영에서 확인했다. 차트는 그림의 값·축은 맞지만 source evidence용 축 필드를 지원 native JSON에 복사해 선택적 선언 투영에서 제외됐다. 이를 원본 source 고정 축 계약의 지원 확대로 해결한 것으로 기록하지 않는다.

첫 4개 노드만 전달하던 writing repair 증거는 최대 12개 노드를 총 excerpt 720자·JSON 2,200자 안에서 전달하도록 보완했다. source 보호·실패 기준·호출 한도·본문 문구는 바꾸지 않았다. 기존 metadata 노드에서 노드 범위·offset·예산·보호 회귀가 통과했다. 서버가 원문에서 추출한 native 표 셀은 전체 값과 함께 공백으로 구분한 완전한 토큰도 보호한다. 추가 토큰은 모든 금지 기호가 내부에 있고 양옆이 문자·숫자인 경우로 제한한다. 모델 선언·일반 본문·앞뒤 또는 단독 기호에는 이 추가 보호를 적용하지 않는다. 원본 문구 자동 치환이나 검사 우회는 없다.

후속 비교 입력은 과거 생성 이전 자료의 공백 메모를 현재 화면의 사실로 쓰지 않고, 현재 기준 SVG의 가시 텍스트와 검증된 관계를 재구성한다. 원본 파일과 기존 요청은 별도로 보존한다. 표의 실제 줄바꿈만 공백으로 정규화한 Markdown을 원본 행과 함께 기록해 기존 source parser와 literal 보호에 연결한다. chart source 요구는 기존 `차트:` 표현으로 바인딩하고, 지원 appearance와 실제 tick·bar geometry로 축 0~7을 기존 export helper가 재증명하는 경로만 사용한다. 해당 경로의 오프라인 복사본 투영은 성공했지만 신규 게시 성공 증거는 별도다. 공통 상단 후보에는 본문 영역 y=128,height=528을 요청하며 내부 본문 배치는 자유 SVG로 남긴다. 이는 한 비교 입력의 명시적 디자인 요구이며 모든 문서의 고정 레이아웃이나 전역 높이 제한이 아니다.

원본 표 토큰 보호의 독립 검토와 기존 metadata 테스트 노드 1개가 통과했다(7.51초). 실제 과거 fixture의 원본 표 토큰 1개를 보호 목록에서 제거하면 위반 수가 10→11로 돌아오는 것도 검증한다. 최종 비교 입력은 가시 본문 415개·표 2개·차트 1개의 감사 후 작업 92ad6187-d33d-44a6-95ad-6693617d3f7f로 새로 접수했다. 원 실패 작업을 재바인딩하지 않았으며, 접수는 품질 평가·게시·PPTX 성공을 뜻하지 않는다.

해당 작업은 개별 12쪽 검증을 모두 통과했으나 문서 전체 검토의 `svg_quality_recovery_review_required`로 실패했다. 대기 1.02초, 실행 532.88초이며 author 13회·repair 9회·VQA 6회가 모두 reported 상태다. 독립 평가자는 기준·후보 실제 PNG 24장을 같은 평가표로 비교해 86→89점으로 평가했다. 본문 관계·수치는 보존됐고 표 2개·차트 1개가 native 투영에서 채택됐으나, 이는 게시 또는 PowerPoint 검증 성공이 아니다. 최초 검토 복사본 뒤 문서 복구가 4쪽 강조만 변경했으며 최종 실패 12쪽도 별도 해시 검증 복사본으로 보존한다.

문서 검토의 주요 실패는 원본이 요구한 9쪽 병합 7개를 행위 열 손실로 판단한 것이었다. 원본 병합 지시는 frozen request.instructions에 있지만 기존 문서 검토 입력에는 이 지시가 전달되지 않았다. 모델 선언을 원본으로 격상하거나 검사를 무효화하지 않고 실제 사용자 지시와 서버 추출 native 요구를 검토 입력에 연결하는 보완이 필요하다. 3·7쪽의 경미한 밀도·연결 표현 의견과 원본 의미 오탐을 구분한다.

12쪽의 실제 0·4·7 눈금은 26px/unit의 막대와 일치하지만, chart 내부 grid와 외부 tick을 조합한 경우 기존 exporter가 불균등 grid 간격만 보고 축 재증명을 놓쳤다. 검증된 내부 grid도 retained-axis 경로에서 검사하도록 좁게 보완했다. 눈금 유일성·공통 비율·범위 검사를 유지하며 눈금 없는 불균등 grid 거부는 바꾸지 않았다. 기존 retained-axis 테스트 노드 4개가 7.03초에 통과했고 독립 코드 검토도 완료했다. 실제 실패 SVG의 오프라인 투영은 숨김 축과 0~7을 복원했으며 원본 파일은 불변이다. 최종 PPTX/Office 증거는 별도로 필요하다.

문서 QA에는 admitted job의 원본 instructions를 sourceDirectives로 덱에 한 번만 전달하고, 실제 원본문에서 서버가 재해석한 sourceNativeRequirements를 넣는다. 생성 SVG 선언을 출처로 사용하지 않으며 입력·페이지·그룹·덱·비교 digest에도 새 출처를 결합한다. 기존 advisory message-shape 노드 [2]가 통과했다. 정책 판정과 호출 한도는 유지한다.

최종 검토만 이어가려던 시험 작업 d66d11ca-8496-46df-94da-504730f95ea8은 실제 상단 checkpoint 충돌로 실패했으며 provider logical call은 0건이다. 이 경로의 미완성 변경은 patch로 외부 보관하고 출시 코드에서 제외했다. 기존 partial recovery가 별도 operation을 만들면서 compatibility job과 checkpoint 행은 재사용한다는 점도 확인했다. 이를 job 행까지 불변인 fork라고 주장하지 않는다. 원 job 28개 필드·12 checkpoint 전체 필드·parent operation control 40개 필드와 호출 30건은 봉인 archive에서 실패 전 덤프와 일치했다. DB 원상복구·이력 삭제는 하지 않았다.

후속 정상 생성 작업 28360642-1079-41a2-ae7d-4619fba4cebf는 이전 입력의 원문·요구·예산을 유지하고 새로 업로드한 동일 PNG 참조만 교체했다. 검증된 QA 출처·차트 수정으로 정상 게시와 Office 품질을 확인하기 위한 새 작업이며, 접수만으로 성공을 주장하지 않는다.

해당 정상 작업도 최종 게시에는 도달하지 못했다. 실제 12쪽의 개별 검증은 전부 통과했으며 독립 PNG 평가 89점(기준 86점), native 표 2개·차트 1개 채택, 원문 셀·병합 7개·차트 수치·축 0~7 보존을 확인했다. 문서 VQA는 major/critical 0건이며 원본 병합 오판도 해소됐다. 다만 7·8·9쪽의 밀도 개선 목표가 남아 최종 만족 판정은 미완료다. 8쪽의 두 번째 author/repair 요청에서 `response.created` 뒤 upstream server_error가 발생해 `public_provider_result_unknown`으로 종료됐다. author 13회·header 2회·repair 8회·VQA 4회 중 26건은 reported, 1건은 unavailable이다.

UNKNOWN 호출은 Codex/gpt-6.1-sol, repair/slide_index=7, 논리 호출 88a37693-ae64-46bf-bc95-dd2394df2655다. 지연 130,234ms 후 완료 원문·output digest·usage가 저장되지 않았다. created 이벤트 서명은 완료 응답 증거가 아니며 기존 코드에 출력 재조회 경로도 없다. 기존 유효 SVG는 last_valid_svg로 보존됐고 12개 PNG는 검토 복사본과 동일하다. UNKNOWN을 성공·known으로 바꾸거나 자동 재요청하지 않았다. 89점은 후보 이미지 점수이며 신규 게시·PPTX·Office 성공을 뜻하지 않는다.

재시도 실험 후 live 원 operation·admission·논리 호출 30개 행도 전후 exact 비교에서 불변임을 확인했다. 원 job/checkpoint 행 자체는 재결합됐다는 한계를 별도로 유지한다. 실패 기록·archive·출력 파일을 삭제하거나 DB를 원상복구하지 않았다. 현재 출시 브랜치의 코드 변경은 검증된 QA 출처 전달과 sparse chart 축 보존까지이며, 미완성 continuation 실험은 포함하지 않는다. main/serve 병합은 실제 12쪽 게시·PPTX 검증이 남아 보류한다.

사용자가 추가 생성 1회를 승인한 뒤 작업 cdb8603d-3cc6-44ea-9cd9-e418c68798e7을 제출했다. 대표 후보 2개의 로컬 검증은 통과했으나 header selection 요청이 180,781ms 후 UNKNOWN으로 종료됐다. header 2·author 2·repair 2회 중 5건 reported, 1건 unavailable이며 전체 12쪽 게시에는 도달하지 않았다. 동결된 plan과 SDK timeout은 모두 180초지만 상세 예외가 저장되지 않아 timeout을 직접 원인으로 확정하지 않는다. 이전 8쪽 server_error와 같은 원인이라고도 단정하지 않는다. 추가 재호출은 하지 않았다.

웹 게시와 구분한 진단용 편집 PPTX를 기존 실제 convert_pptx 서비스로 생성했다. 원본은 89점 후보의 해시 검증된 SVG 12개·원 native 선언이며, 임의 operation identity나 테스트용 영수증을 만들지 않고 DB/provider/publish 경로를 사용하지 않았다. 실제 PowerPoint 읽기 전용 12쪽은 통과했고 기존 열린 문서와 원본도 보존했다. 첫 Office 수동 평가는 차트 기준선 누락 때문에 88점으로 낮아졌다.

확인된 export 회귀는 strict scale/tick/category/coextent/bounds 증명을 만족한 내부 grid 선을 원 paint 순서대로 native chart 바로 앞의 source underlay로 보존하도록 수정했다. 선의 유일 대응·paint 일치·pre-bar 순서를 요구하며 중복·불일치·bar 이후 선은 거부한다. 상속 paint와 중첩 transform을 보존하고 clone의 중복 ID/native 메타데이터는 제거한다. 원 SVG·선언·데이터·축은 불변이며 vendor 수정은 없다. 기존 retained-axis 테스트 node 4개가 6.05초에 통과했고 독립 읽기 검토도 완료했다. 영향을 받는 export worker만 재시작했다.

새 진단용 PPTX는 외부 근거 폴더 normal12-grid-preserved/editable-candidate.pptx이며 SHA-256은 ad683c939a7a7573da8c92709962754a854264159ff51522f4825b876b55c132다. 실제 Office 12쪽 재검증에서 1~11쪽 PNG는 이전 파일과 byte-identical, 12쪽만 0·4·7 선이 막대 뒤에 복원됐다. 최종 Office 수동 평가는 89점(기준 86점)이다. native 표 2개·차트 1개·텍스트 객체 494개·벡터 객체 704개, 그림과 fallback 0개를 확인했다. 표 원문·병합 master 7/covered 21·차트 cache/내장 XLSX의 6개 값과 합 30.90·숨김 축 0~7은 일치한다. 원본 26개 파일 해시와 별도 source job 해시도 보존됐다.

이 결과는 실제 편집 가능한 진단 PPTX와 Office 증거까지이며, 정상 웹 생성·최종 VQA 승인·게시 성공으로 확대하지 않는다. 7·8·9쪽의 구체적인 밀도 개선 목표도 남아 있다. 정상 12쪽 게시가 미완료여서 main/serve 병합은 계속 보류한다. UNKNOWN 및 실패 이력과 이전 PPTX는 그대로 남긴다.

## 후속 범용 수정과 검증 범위

- 첫 작성 및 기존 수정 프롬프트는 최소 폰트를 일반 본문의 목표 크기로 사용하지 않도록 보강했다. 조건·예외·담당 관계와 긴 표 셀을 위한 공간과 의미 단위 줄바꿈을 우선하며, 원문 병합·대응 관계를 유지한다. 고정 레이아웃, 특정 페이지 예외, 폰트 하한 변경, 추가 호출은 없다. 기존 prompt-route 노드 1개가 8.82초에 통과했다. 새 출력의 디자인 향상은 아직 실측하지 않았다.
- 일반 structured 호출에서 완료 스트림 이후 파싱이 실패하면 원래 예외 타입을 유지한 채 관측된 안전 진단을 보존한다. 기존 completed-stream 노드의 두 provider 매개변수가 0.41초에 통과했다. 과거 서버 오류나 180초 실패의 직접 원인을 수정한 증거와 구분한다.
- 헤더는 실제 completed 원문·digest·안전한 usage를 기존 fenced 저장 경로에 먼저 기록한다. 후속 사용량·admission 처리 실패 시 status는 UNKNOWN이며, 원문 증거만으로 성공 판정 또는 재호출을 허용하지 않는다. 정상 완료, 사용량 실패, admission 종료 실패, terminal 저장 실패를 포함한 기존 metadata 노드 1개가 7.46초에 통과했다. 이 검증은 가짜 SDK 스트림을 사용하며 실제 provider 실행 증거가 아니다.
- 새로운 partial recovery는 기존 append-only staging 문서 생성 경로와 별도 job/task/checkpoint를 사용한다. 원본 job·페이지·operation·provider 이력은 재바인딩하지 않는다. manifest와 새 v2 lineage에서 producer와 consumer를 분리하며, presentation routing 값만 명시적으로 producer에 투영한다. 전역 frozen payload 계약과 과거 v1 episode 검증은 유지한다.
- 원본 job 및 전체 checkpoint 행을 실제 DB 조회와 대조한다. 서로 다른 문서의 조상 연결은 검증된 append-only edge로 제한한다. 이미 완료된 헤더는 검증된 조상을 따라 원래 envelope를 소비하며, 원문 응답이나 producer identity를 새 작업으로 재라벨하지 않는다. local quality 소비도 producer identity와 consumer binding 전체를 검증한다.
- 기존 append-only 테스트 매개변수 1개에 실제 격리 SQLite 행의 첫 fork, 두 번째 fork, 헤더 무호출 재사용, 원본 보존, 입력·소유 연결 변조 거절을 확장해 최종 6.74초에 통과했다. PostgreSQL JSON 저장 해시 adapter만 대체했으며 실제 PostgreSQL 복구 제출이나 유료 이어가기 성공을 뜻하지 않는다. 기존 source-author terminal projection 검증 노드도 별도로 통과했다. 코드 검토에서 제기된 중간 상태와 반복 복구 경계도 확인·보완했다.
- 실제 12쪽 실패 작업의 읽기 전용 복구 검토에서 두 추가 결함을 확인했다. 먼저 뒤 호출이 inflight인 상태에서 앞 호출의 completed 응답을 현재 결과로 읽던 선택을 순번으로 제한했다. 명시적인 과거 결과 소비는 기존 provenance 검증을 유지한 canonical key로 분리한다. 다음으로 선택된 대표페이지의 author style은 선택 메타데이터 추가 전 snapshot이라는 점을 실제 published envelope·후보 identity·재계산 style·author witness로 증명한다. 임의 style 불일치를 허용하지 않고 검증된 producer 조상을 따라 원래 증거를 소비한다.
- 실제 PostgreSQL 상태에 대한 롤백되는 읽기 전용 service 검토는 알려진 11쪽과 미확정 8쪽을 분리하는 계획을 산출했다. 새 본문 작성은 1회이며, 기존 동결 정책의 검토·수정 슬롯과 한도는 유지된다. 원본 operation/job/checkpoint/UNKNOWN 변경이나 복구 작업 제출, provider 호출은 수행하지 않았다.
- API와 생성 워커를 관리 스크립트로 재시작한 뒤 실제 인증된 웹 recovery-plan GET도 HTTP 200과 동일한 11쪽/1쪽 분리를 반환했다. `normal12-final-readonly-recovery-plan.json`에 근거를 남겼다. 별도 PostgreSQL 읽기 전용 대조에서는 원 operation·job·12 checkpoint·admission·logical call 전체 행이 이전 terminal 덤프와 동일했다(`normal12-post-fix-preservation-proof.json`). 실제 복구 실행과 최종 게시·다운로드는 아직 수행하지 않았다.
- 호출 한도, deadline, UNKNOWN 상태, 디자인·원본 보존·게시 기준은 완화하지 않았다. 실제 웹 생성과 게시·다운로드 검증 및 main 병합은 계속 별도 완료 조건이다.

사용자가 추가 확인 없이 계속 진행하도록 승인한 후 준비된 복구 action을 제출했으나 provider admission 이전에 `svg_partial_recovery_episode_unbound`로 거절됐다. 실제 원본 durable request의 `presentation_id`는 null인 반면 격리 검증은 실제 UUID만 사용한 차이를 확인했다. 복구 manifest에 원본 요청 필드의 존재 여부와 정확한 값을 봉인하고 archive 원문과 다시 대조하여 없음·null·UUID를 그대로 복원하도록 수정했다. 전역 frozen digest를 완화하지 않으며 기존 HTTP 실패 응답은 보존한다. 같은 좁은 append-only 노드에 세 요청 형태의 첫·두 번째 복구 및 헤더 재사용을 포함해 8.19초에 통과했고 독립 코드 검토도 완료했다.

실제 append-only 복구 3676de99-875e-4c3f-a146-66f027c3a980은 11쪽을 재사용하고 8쪽만 새로 작성하여 12쪽 로컬 검증을 통과했다. 문서 VQA 한 그룹에서 upstream HTTP500/server_error가 발생해 UNKNOWN으로 종료됐으며 timeout으로 단정하지 않는다. 후속 11a1aee0-cb94-4fcc-832f-ae446835dd3e는 12쪽 전체를 재사용하고 VQA 8회·repair 2회를 모두 reported로 완료했다. 10·11쪽의 새 후보가 실제 selected가 됐고, 최종 SVG와 두 번째 검토 gate는 일치하며 gate 재사용·게시 전 검증도 저장 payload 재현에서 통과했다. 독립 이미지 평가는 후보 기준 91점(원본 86점)이며 최종 Office 점수는 아직 아니다.

해당 후속 작업은 게시 단계에서 generic HTTP500으로 실패했다. 원본 presentation의 asset manifest를 이어받은 12쪽이 있는데 `read_svg_slide_assets`의 inherited-source guard는 timeout-repurchase lineage만 허용하고 partial lineage와 실제 publication 상태 `staging`을 빠뜨리고 있었다. 이 두 누락만 기존 owner/token/lease 검사 아래 허용했으며, 원본 manifest·archive·lineage 검증은 기존 `_verify_inherited_source_manifest`와 `verify_episode`를 그대로 통과해야 한다. 기존 source-publication 정상 노드에 partial/staging 성공 및 잘못된 pointer·tenant·token·lease·owner 거절을 포함해 5.25초에 통과했다. 이전 작업·UNKNOWN·완료 호출·원본 파일은 보존하며 신규 게시 성공으로 표시하지 않는다.

다음 복구 e2135b8a-a50e-4fb1-b3dc-9f2891f17847은 초기 본문 호출 없이 12쪽을 재검증하고 첫 VQA 4회와 7·9·10쪽 repair 3회를 모두 reported로 완료했다. 9쪽의 후보 거절 후 원본 복원에서 `correction_mode`가 과거 값으로 돌아가 현재 수정 권한을 잃는 문제를 저장 payload로 재현했다. 그림·측정·native 증거는 archive에서 복원하면서 완료 호출 이력과 해당 correction scope를 함께 보존하도록 수정했다. 동일 실제 payload의 7·9·10쪽 권한 검사와 9쪽 원본 복원 검사가 통과했다. 실패 작업이나 완료 응답은 변경하지 않았다.

코드 갱신 후 과거 episode를 증거로 읽을 때 현재 실행 코드 hash를 요구하던 문제도 분리했다. 기본 `verify_episode`와 현재 admission/prepare는 계속 현재 코드 일치를 요구한다. 명시적인 과거 검증은 operation과 job이 모두 terminal일 때만 허용하며 archive·원행·소유권·요청·lineage 검사는 유지한다. 실제 PostgreSQL 읽기 전용 검증에서 과거 archive 검증 성공, 과거 코드 실행 거절, running operation의 historical 우회 거절을 확인했다.

9쪽 후보의 실제 기본 렌더는 통과했고 native 검사만 `native_requested_table_missing`으로 실패했다. 표가 정상적인 여러 M/H/V 경로로 격자선을 그렸는데 table primitive가 path를 지원하지 않는 원인이었다. table 전용 strict M/L/H/V 선분 해석을 추가해 원 SVG를 변경하지 않고 기존 line primitive와 같은 표·paint·export padding 처리로 전달한다. 곡선·대각선·채움·불명확한 부모 dash/cap/marker/style/filter는 거부하며 일반 path·chart 처리에는 적용하지 않는다. 기존 native-publish 노드가 6.63초에 통과했고 실제 저장된 후보에서도 declared 1/accepted 1/drop 0, 8×6 셀·병합 7개·원본 셀 텍스트·export style 및 입력 불변을 확인했다. 새 7·9·10쪽의 독립 화면 평가는 후보 기준 92점이며 게시·Office 결과로 확대하지 않는다.
