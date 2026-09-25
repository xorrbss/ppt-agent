# 원인 분석과 최소 보정 실험

## 확인된 원인

기존 42개는 내용 평가와 배치 평가를 분리해 집계했다. 결함 라벨 중 카드/패널 밖 탈출 계열이 반복되며, 별도로 연결선-텍스트 충돌과 원본 이미지 가림/비가시성이 존재한다. 각 라벨 빈도는 cause-catalog-42.json에 있으며 한 페이지에 복수 라벨이 있으므로 라벨 수를 실패 페이지 수로 해석하지 않는다.

대표 case01/C는 높이 137px grid 안의 내부 카드가 min-height:auto의 텍스트 최소 콘텐츠 크기 때문에 186px가 되었다. 외부 사업이해도 카드 bottom415, 내부 카드 bottom446, 다음 추진체계 패널 top439다. 원본 생성 HTML에 이미 이 구조가 있고 기존 시험의 fit=False 경로가 그대로 렌더했다. 모든 42개가 같은 원인이라고 일반화하지 않는다.

검출기는 텍스트의 가장 가까운 painted 카드 하나만 확인했다. 텍스트가 내부 카드 안에 있어도 내부 카드가 바깥 카드 밖으로 나오는 중첩 구조를 놓쳤다. 원래부터 존재한 authored_dom_text_escape 모듈을 확장하는 것이 최소 범위이며 별도 검사기 신설은 불필요했다. 수정 전 현재 코드로 저장 18개를 재판정했을 때 과거 판정과 동일했다. 수정 뒤 저장 18개는 case01/C 한 건만 pass에서 fail로 바뀌고 geometry 정상 5건은 그대로 통과했다. 이것은 검출 개선이며 슬라이드 자체 개선이 아니다.

## 기존 보정 재현

처음 capture 직접호출은 완전 QID 준비가 빠져 source_qid_unavailable로 끝났다. 이 결과는 fitter 품질 실패로 사용하지 않는다. 원본을 보존하고 data-qid 속성만 추가한 준비본으로 재실행했다. preparation.json의 onlyQidAttributesChanged=true가 이를 확인한다.

완전 QID 준비 후 기존 fitter는 case01에서 spacing_adjusted를 반환했으나 내부 flow의 height:auto 수정만 만들었다. 실제 내부 카드186px 및 외부 카드 경계가 그대로여서 결함은 남았다. 최종 HTML을 fit=False로 다시 캡처하고 같은 HTML을 검사했으며 HTML 해시가 일치한다. 정상 case05는 unchanged였다. fit status만으로 품질 통과를 보고하지 않는다. 이 실행의 Python 검사기는 새 검출 로직을 이미 포함했으며 소스 digest를 실행 시작에 고정하지 않아 이전 시간과 정밀 비교하지 않는다.

## 최소 후보

측정된 중첩 painted 카드 탈출만 골라 수직 padding을 최대4px, owned text의 행간을 글꼴 크기의1.1배 이하, 위 여백을 최대2px로 압축하고 min-height:0을 적용했다. 기존 간격보다 늘리지 않는 clamp를 사용한다. 글자 크기나 내용 및 이미지 src를 바꾸지 않았다. 이 후보는 artifact 전용이며 일반 제품에 아직 일괄 적용하지 않았다.

case01 내부 카드 높이는186→137px가 되었고 외부 카드와 다음 패널의 위치는 그대로였다. 새 캡처에서 blocker0, 텍스트와 글자 크기 보존을 확인했다. 독립 시각 검수는 root가 별도로 진행하며 이 문서는 그 결과를 대신하지 않는다.

## 대표10개 확대

representative-10.json은 원인 중복을 줄인 실패8개와 정상2개를 포함한다. spacing-replay-10/summary.json과 각 result.json에 실제 fresh 렌더/검사 시간, HTML 해시, 내용/글자 크기/이미지 보존을 기록했다. 10개 중 3개에 후보가 적용되고 나머지7개는 준비본 기준 byte no-op였다. 정상 두 건도 no-op였다. 10개 모두 owned text, font size, 이미지 src/natural size/complete가 유지됐다. 이는 기존의 의미 누락을 고쳤다는 뜻이 아니다. 원래 contentPreserved 판정은 그대로 유지한다.

적용된 3개 중 case01만 자동 blocker0이고 case04/07에는 각각8개가 남았다. 후자는 후보를 자동 채택하면 안 되며 원본을 유지해야 한다. 자동 통과하지만 기존 독립 검수에서 연결선/이미지 문제였던 페이지를 새 품질 통과로 승격하지 않는다.

신규 provider 호출은0회다. 측정은 오프라인 HTML 보정+렌더+검사이며 제품 생성/큐/내보내기 시간이 아니다. cold/warm 단일 관측이 섞여 있어 서비스 속도 우열을 결론내리지 않는다.


## 실제 제품 공통 경로 확대

제품의 기존 local-fit API를 사용해 10개를 실행했다. 새 Python 중첩 카드 검출과 browser 후보 containment hard gate를 포함한 소스 해시를 동결했으며 실행 종료에도 동일했다. 정책 slide_index는 source.json의 원래 값이고, 기본 floor9pt / maxshrink10%를 유지했다. index0으로 실행한 초기 재현과 속도를 직접 비교하지 않는다.

case01은 실제 제품 search가 combined 후보를 선택해 해결했다. 원본 본문14px 한 항목을13.3px(9.975pt)로5% 줄였고 나머지는 간격을 조정했다. floor9pt보다 높고10% 축소 제한 안이다. 독립 제품 시각 검수에서 case01 통과, 정상case09 동일을 확인했다. 총15.641초 중 fit 렌더12.282초, 변경 HTML의 fit-disabled 검증 렌더3.174초, 검사0.185초다. 후보57개 중20개를 평가했고 실제 browser 검색은1.232초였다. capture2회와 reconstruction1회다. 이는 cold/warm 차이가 섞인 단일 관측이며 서비스 처리시간 우열이 아니다.

제품 10개 중 case01과 case06만 HTML이 변하고 나머지는 원본 준비본 그대로였다. case06은 자동 blocker0이지만 기존에 가려진 설명이 남았을 수 있어 독립 시각 판정 전 품질 통과로 취급하지 않는다. DOM문자 동일은 가시성 보존이 아니다. 정상case10도 원본 HTML은 그대로지만 이미지 글자 확인 부족으로 fit_report는 font_unverifiable/unresolved다. 자동 검사, fit 정책, 독립 시각 품질은 서로 다른 판정이다.

B 재사용 결과의 source.json 부재로 한 번 metadata 준비가 멈췄다. 렌더 전에 발생했고 provider 호출은 없었다. a-reference의 원본A HTML/PNG digest를 확인한 뒤 같은 A의 source.json을 상속하여 이어서 실행했다. 완료한 렌더는 재실행하지 않았다.

제품 결과는 product-fit-replay/summary.json 및 galleries/product-fit.html에 있다. 원래 contentPreserved=false 결과는 DOM문자 보존만으로 승격하지 않았다. 이 검증은 제품 공통 renderer/fitter 실제 실행이며 UI 생성·큐·PPTX 내보내기 전체 경로 검증은 아니다.
