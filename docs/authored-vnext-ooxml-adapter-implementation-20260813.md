# Authored vNext OOXML adapter 구현 기록

- 작성일: 2026-08-13
- 기준 HEAD: `b387a34735482d4f6dfbf2b955fbf19fd0d2315e`
- 제품 위치: `servers/nextjs/lib/ooxml-vnext/`
- 선행 결정: `03-ooxml-roundtrip`의 GenOffice 일반 편집 엔진 NO-GO를 유지한다.

## 구현 결과

`OoxmlMasterCompilerPort`는 opaque document handle을 통해 다음을 제공한다.

1. bounded ZIP/XML inspect와 imported package inventory
2. 실제 `slideMaster`, standard/long-title `slideLayout`, theme, placeholder,
   rels, content-types 생성과 deterministic semantic metadata read-back
3. layout 관계와 stable placeholder `type/idx/role`을 사용한 slide materialize
4. part/relationship/reachable-closure digest precondition
5. 한 개의 승인된 `<a:t>` byte slice만 바꾸는 targeted text operation
6. temp candidate write → validate/reopen → digest → fsync → atomic publication
7. unsupported/external part exact preservation과 macro package quarantine

GenOffice exact pin은 buffered no-op lab oracle로만 선택 가능하다. 제품 API에
text mutation, streaming save, master/layout CUD 또는 mutable engine type을
노출하지 않는다. PPTist/AGPL, GenOffice `ee`, apps, AI, Electron 및 sibling
package는 compliance gate가 거부한다.

## 확인한 결과

- focused Node tests: 33/33 통과
- synthetic 90장 generated → buffered save → import no-op: part payload,
  package/relationship/closure digest 변화 0
- 기존 03 실험 90장 corpus no-op: uncompressed part 변화/누락 0
- targeted edit: 변경 part 1개, 승인 slice 밖 byte 변화 0
- master 삭제 → 재생성, standard/long-title layout, metadata read-back 일치
- SmartArt형 diagram, OLE binary, custom XML sentinel과 external rel 보존,
  silent loss 0
- macro fixture: 전 package `QUARANTINED`, publication 차단
- TypeScript focused strict check와 focused ESLint 통과
- Microsoft Open XML SDK validator: 오류 0
- PowerPoint 16.0.20228.20158 hidden read-only COM: 4→4장 reopen 통과,
  save call 0, 입력 digest 불변
- LibreOffice 26.2.5.2 isolated headless conversion: 4→4장 통과
- PowerPoint slide 1 PNG export: 1280×720 생성 및 육안 확인

검증 중 PowerPoint가 거부한 두 구조 결함을 실제로 발견해 수정했다.

- empty `p:sldIdLst` 뒤에 두 번째 list를 추가하던 중복 구조
- 유효하지 않은 slide layout type `titleAndObj` (유효한 `obj`로 수정)

## 미검증·수동 witness

- hidden COM은 repair UI를 관찰하지 못하므로 `repair dialog = 0`은 주장하지
  않는다. 03 실험의 disposable-copy visible manual witness가 별도로 필요하다.
- 실제 고객 macro/SmartArt/OLE/modern comments corpus의 semantic 편집은 지원
  범위가 아니다. 해당 객체는 exact opaque preservation 대상이다.
- 전체 저장소 테스트 스위트는 프로젝트 지침에 따라 실행하지 않았다.
