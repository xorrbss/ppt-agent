# GenOffice `pptx-engine` adapter 경계 분석

작성일: 2026-08-13 (Asia/Seoul)  
대상 저장소: `https://github.com/genspark-ai/genoffice.git`  
exact pin: `dc4d7e5927864498913b7ba42d0da06cc7cf628e` (`v0.6.279`, upstream `HEAD` 확인 시점과 동일)

## 결론

이 엔진은 **기존 deck의 no-op open-save와 기존 slide/master/layout의 제한된 in-place patch**를 시험하기에는 적합하다. 저장 시 새 ZIP을 만들기 때문에 `.pptx` 파일 자체의 SHA-256/압축 스트림/central-directory 동일성은 보장하지 않지만, `archive.entries`의 모든 file entry를 다시 넣고 dirty slide만 재조립하므로 **압축 해제한 untouched part payload 동일성**을 검증할 수 있다.

그러나 현재 공개 API에는 master/layout **Create/Delete**가 없고 Read와 제한된 Update만 있다. 또한 text-only edit도 dirty element 안의 모든 run을 patch하며 상속된 bold/italic을 명시 속성으로 물질화할 가능성이 있다. 따라서 다음 두 항목은 PoC의 선결 kill criterion이다.

1. target text edit의 변경 범위가 승인된 `<a:t>` text node 이외로 번지면 중단한다.
2. master CRUD에서 Create/Delete가 필수라면 현재 pin은 부적합하므로, 관계/콘텐츠 타입 수술을 facade 밖에서 임의 구현하지 않고 중단한다.

## 공급망과 허용 경계

- 원격 `origin`은 `https://github.com/genspark-ai/genoffice.git`이며 `git ls-remote origin HEAD`와 local `HEAD`가 모두 `dc4d7e5927864498913b7ba42d0da06cc7cf628e`였다.
- root와 package manifest 모두 Apache-2.0을 선언한다. root `LICENSE`는 Apache License 2.0 전문이며 `NOTICE`는 `GenOffice`, `Copyright 2026 Mainfunc, Inc.`를 명시한다.
- 허용 runtime은 `packages/pptx-engine`과 직접 dependency `fast-xml-parser`, `jszip`뿐이다. package는 `private: true`이고 export entry는 source TypeScript를 직접 가리킨다. [package.json L1-L20](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/package.json#L1-L20)
- sparse checkout에는 `LICENSE`, `NOTICE`, `LICENSE-UNICODE.txt`, root manifests와 `packages/pptx-engine/`만 둔다. `apps/*`, Electron, AI, renderer, branding은 dependency graph와 복사 대상에서 제외한다.
- upstream tree에는 `ee/`가 실제로 존재한다. import/vendor/install 단계에서 경로가 `ee/` 또는 `enterprise/`인 파일이 하나라도 들어오면 fail-closed한다. core source가 `ee`를 import하는지도 정적 검사한다.
- NOTICE는 release bundle의 third-party notices가 packaging time에 생성되며 source에 체크인되지 않는다고 명시한다. 따라서 이 PoC는 root NOTICE만으로 배포 NOTICE가 완성됐다고 간주하면 안 된다. lockfile로 SBOM을 생성하고 `fast-xml-parser`, `jszip` 및 transitive dependency의 license를 별도 수집해야 한다.

권장 pin 형식은 Git submodule/archived source에서 SHA 전체를 쓰고, facade build 시 아래 조건을 모두 검사하는 것이다.

```text
GENOFFICE_SHA == dc4d7e5927864498913b7ba42d0da06cc7cf628e
allowed source prefix == packages/pptx-engine/
forbidden prefixes == apps/, ee/, enterprise/
runtime package names == @genoffice/pptx-engine, fast-xml-parser, jszip (+ lockfile transitives)
```

## 공개 API와 실제 저장 경로

### No-op open-save

- `openPptx(bytes)`는 `PackageArchive.open`으로 모든 file entry의 압축 해제 payload를 `Map<string, Uint8Array>`에 저장한 뒤 presentation slide 순서를 읽어 각 slide를 파싱한다. [zip.ts L28-L46](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/zip.ts#L28-L46), [index.ts L468-L479](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/index.ts#L468-L479)
- `savePptx`/`savePptxToFile`은 새 JSZip archive를 생성한다. `buildZip`은 모든 `archive.entries`를 순회하고 dirty slide만 `patchSlideXml`로 대체하며 나머지 payload는 원래 `Uint8Array`를 넣는다. XML/rels는 다시 deflate하고 opaque binary는 `STORE`한다. [index.ts L499-L537](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/index.ts#L499-L537), [index.ts L574-L608](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/index.ts#L574-L608)
- 따라서 검증 단위는 ZIP 전체 bytes가 아니라 file-entry inventory와 각 entry의 **uncompressed SHA-256**이다. directory entry, ZIP timestamp, entry order, compression method/level, CRC/central directory bytes는 fidelity 기준에 넣지 않는다.
- upstream test도 no-op에서 slide XML의 압축 해제 bytes만 비교한다. 모든 package part나 relationship closure를 검증하는 test는 아니다. [parse.test.ts L555-L578](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/tests/parse.test.ts#L555-L578)

예상 no-op rewrite set:

```text
uncompressed part payload: 0 changes
file entry inventory: 0 additions / 0 removals
ZIP container bytes: changed (new JSZip serialization)
```

### Targeted text edit

안전성이 가장 높은 공개 경로는 `replaceAllInDeck(deck, find, replace, { slideIndex, elementId, firstOnly: true })` 후 `savePptxToFile`이다. 이 API는 field run을 건너뛰며 cross-run match를 지원하지 않는다. [index.ts L2014-L2105](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/index.ts#L2014-L2105)

하지만 `elementId`는 parse session에서만 유효한 transient id이고, dirty element 저장은 해당 element의 모든 model run을 순회한다. aligned run 구조에서는 `<a:t>`를 in-place 교체하고 outer bytes를 보존하지만, run 수/종류가 달라지면 `txBody` paragraph 전체 rebuild로 내려간다. [generate.ts L1-L17](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/generate.ts#L1-L17), [generate.ts L26-L55](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/generate.ts#L26-L55), [generate.ts L598-L623](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/generate.ts#L598-L623)

특히 파서는 `bold`와 `italic`을 상속값까지 항상 boolean으로 만든다. writer는 값이 `undefined`가 아니면 기존 `<a:rPr>`에 `b="0|1"`, `i="0|1"`을 설정한다. 이는 text-only edit가 상속을 명시 formatting으로 바꾸는 collateral rewrite를 낳을 수 있다는 소스 수준 위험이다. [parse.ts L1250-L1321](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/parse.ts#L1250-L1321), [generate.ts L250-L347](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/generate.ts#L250-L347), [generate.ts L582-L595](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/generate.ts#L582-L595)

targeted edit 허용 조건:

- find 값은 대상 element 안에서 정확히 1회 존재해야 한다. 0회 또는 2회 이상이면 reject한다.
- field run, cross-run phrase, leading/trailing whitespace를 새로 요구하는 값, paragraph/run 구조 변경은 reject한다.
- 변경 전후 XML diff를 token-aware하게 검사해, 승인된 한 `<a:t>` content 외 변화가 있으면 결과를 quarantine하고 이 pin의 product 적용을 중단한다.
- unrelated parts와 같은 slide의 unrelated top-level element raw slices는 SHA-256/byte equality를 만족해야 한다. scanner는 top-level shape slice와 shape 사이 gap을 보존한다. [scan.ts L1-L29](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/scan.ts#L1-L29), [index.ts L611-L665](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/index.ts#L611-L665)

### Master CRUD

공개 master API는 다음뿐이다.

- Read/list: `listMasterParts(archive)`
- Parse for edit: `parseMasterPart(archive, partPath)`
- Update helper: generic `patchSlideXml(masterOrLayoutModel)`

`listMasterParts`는 master relationship을 따라 layout을 열거하고, `parseMasterPart`는 master/layout을 slide-isomorphic model로 읽는다. 이 모듈은 명시적으로 “enumerates and parses; caller writes back via patchSlideXml and archive entry”라고 선언한다. [master-edit.ts L1-L9](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/master-edit.ts#L1-L9), [master-edit.ts L32-L49](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/master-edit.ts#L32-L49), [master-edit.ts L64-L119](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/master-edit.ts#L64-L119)

upstream Update test도 `archive.entries.set(masterPath, patchSlideXml(master))`를 직접 호출한다. untouched layout의 text payload 동일성만 확인한다. [master-edit.test.ts L36-L62](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/tests/master-edit.test.ts#L36-L62)

| 작업 | 현재 pin | facade 정책 |
|---|---|---|
| Create master/layout | 공개 API 없음 | `UNSUPPORTED_MASTER_CREATE`로 fail-closed |
| Read/list | 지원 | own DTO로만 반환 |
| Update existing text/transform | 제한 지원 | patch 후 허용 diff와 closure 검사 통과 시만 출력 |
| Delete master/layout | 공개 API 없음 | `UNSUPPORTED_MASTER_DELETE`로 fail-closed |

Create/Delete는 `[Content_Types].xml`, `presentation.xml(.rels)`, master-layout-theme rel chain, slide-to-layout refs를 모두 일관되게 갱신해야 한다. 현재 pin에 이를 수행하는 원자적 public operation이 없으므로 adapter가 임의 구현하면 안 된다.

## Facade 형태

GenOffice의 `OpenedPptx`, `Slide`, `TextElement`, `PackageArchive`, mutable `archive.entries`를 product 쪽에 export하지 않는다. facade class의 private state에만 보관하고, 외부에는 JSON-safe own DTO와 bytes만 노출한다.

```ts
export type RoundtripErrorCode =
  | 'UNSUPPORTED_MASTER_CREATE'
  | 'UNSUPPORTED_MASTER_DELETE'
  | 'AMBIGUOUS_TEXT_TARGET'
  | 'UNSAFE_FEATURE_MUTATION'
  | 'DIFF_OUTSIDE_ALLOWLIST'
  | 'RELATIONSHIP_CLOSURE_CHANGED'
  | 'PART_MISSING'
  | 'QUARANTINED_MACRO_DECK'

export interface TextTarget {
  slideIndex: number
  elementOrdinal: number
  expectedText: string
  replacement: string
}

export interface MasterPartDto {
  ordinal: number
  kind: 'master' | 'layout'
  name: string
  partPath: string
}

export class PptxRoundtripSession {
  static open(bytes: Uint8Array, sourceName: string): Promise<PptxRoundtripSession>
  inventory(): ReadonlyArray<{ path: string; size: number; sha256: string }>
  slides(): ReadonlyArray<{ index: number; elementCount: number }>
  masters(): ReadonlyArray<MasterPartDto>
  replaceText(target: TextTarget): { replacements: 1 }
  updateExistingMasterText(partOrdinal: number, expected: string, replacement: string): void
  createMaster(): never
  deleteMaster(): never
  saveNoopTo(path: string): Promise<void>
  saveEditedTo(path: string): Promise<void>
}
```

구현상 `elementOrdinal`을 현재 session의 transient `elementId`로 내부 변환한다. facade는 import한 엔진 타입을 declaration output에 노출하지 않고, `#opened: Awaited<ReturnType<typeof openPptx>>` 같은 private field로만 둔다. `archive.entries.set`은 existing master/layout Update adapter 내부 한 지점에서만 허용하고, Create/Delete나 임의 part write API는 제공하지 않는다.

## 특수 feature 정책

이 정책은 “엔진이 해당 feature를 완전 편집할 수 있다”는 뜻이 아니라, **generic no-op/승인된 text 또는 existing master patch에서 payload와 rel을 손대지 않는 정책**이다.

| Feature | 정책 | 소스 근거와 제한 |
|---|---|---|
| Macro/VBA (`pptm`, `vbaProject`) | **quarantine** | source에 `vbaProject`, `pptm`, `macro` handler가 전혀 없다. opaque part는 일반 entry copy로 보존될 가능성이 높지만 macro semantics/extension/signature 검증이 없다. lab에서는 uncompressed hash 보존만 측정하고 product mutation/export는 reject한다. |
| External relationships | **preserve** | relationship reader가 `TargetMode`를 보존하며 [zip.ts L68-L84](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/zip.ts#L68-L84), generic save는 rels payload를 그대로 복사한다. facade는 dereference/fetch하지 않고 rel mutation API를 노출하지 않는다. |
| SmartArt | **preserve** | `passthrough/smartart`이며 prerendered drawing은 read-only preview, save anchor는 original bytes다. [parse.ts L738-L792](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/parse.ts#L738-L792) 직접 edit/transform은 facade에서 reject한다. |
| OLE | **preserve** | `passthrough/ole`이고 fallback picture는 read-only preview다. [types.ts L366-L377](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/types.ts#L366-L377) OLE/frame transform mutation은 facade에서 reject한다. |
| Animation/timing | **preserve** | text element는 `spTree` 안이고 timing은 `bodySuffix`라 generic text patch에서 원문 보존된다. animation edit API는 mainSeq/bldP를 rewrite하며 복잡 timing은 best-effort downgrade 대상이므로 facade에서는 reject한다. [animation.ts L1-L18](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/animation.ts#L1-L18), [animation.ts L525-L580](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/animation.ts#L525-L580) |
| Comments | **preserve** | classic comment parts는 별도 archive entries이고 generic save에서 유지된다. 엔진에 classic comment add/delete가 있으나 [comments.ts L148-L218](https://github.com/genspark-ai/genoffice/blob/dc4d7e5927864498913b7ba42d0da06cc7cf628e/packages/pptx-engine/src/comments.ts#L148-L218), modern/threaded comment 인식은 없으므로 facade는 comment mutation을 노출하지 않는다. |
| Custom XML | **preserve** | source에 custom XML handler가 없다. 일반 entry copy로 opaque-preserve하고 direct mutation은 reject한다. |
| Unknown part/shape | **preserve** | unknown `graphicFrame`은 passthrough, top-level 비인식 XML은 shape 사이 gap/body prefix/suffix로 재사용한다. scanner가 인식할 수 없는 namespace/prefix 또는 malformed `spTree`로 open이 실패하면 reject한다. |

`preserve` feature는 before/after entry SHA-256와 relationship closure가 모두 같을 때만 성공이다. 해당 feature를 포함한 slide에서 target text edit를 수행하는 경우에도 feature 관련 part와 rels뿐 아니라 target element 밖 raw XML slice가 같아야 한다.

## 검증과 diff 분류

각 run은 save 전후로 다음을 기록한다.

1. file-entry inventory: path, uncompressed length, SHA-256, category(content types/rels/presentation/slide/master/layout/theme/media/diagram/OLE/customXml/unknown)
2. 모든 `.rels` edge: source part, Id, Type, normalized internal target 또는 exact external target, TargetMode
3. `[Content_Types].xml` default/override multiset
4. 관계 closure: internal target 존재, presentation slide refs 존재, slide-layout-master-theme chain 존재
5. before-only, after-only, changed payload 목록

operation별 허용 diff:

| operation | 허용 part diff |
|---|---|
| no-op | 없음 |
| targeted text | 대상 `ppt/slides/slideN.xml` 하나만; 그 안에서 승인된 `<a:t>` 하나만 |
| existing master/layout text update | 대상 master/layout XML 하나만; 승인된 `<a:t>` 하나만 |
| master read | 없음 |
| master create/delete | 실행 자체를 reject |

모든 실제 변화는 다음 원인 중 하나로 분류한다.

- `requested-text`: 승인된 `<a:t>` payload 변화
- `engine-format-normalization`: `b/i/sz/fill/font` 등 요청하지 않은 run-property 변화
- `engine-structural-rebuild`: `<a:p>`/`txBody` 재생성
- `package-reserialization-only`: uncompressed payload는 같고 ZIP container만 변함
- `unexpected-part-or-rel-change`: inventory/relationship/content-type 변화

`engine-format-normalization`, `engine-structural-rebuild`, `unexpected-part-or-rel-change`가 하나라도 있으면 결과를 성공으로 표기하지 않는다.

## Kill criteria

- no-op에서 file entry 누락/추가 또는 uncompressed payload SHA-256 변화가 1개 이상
- generic text/master Update에서 relationship edge/closure 또는 `[Content_Types].xml` 변화가 1개 이상
- target slide/master/layout 외 part 변화, 또는 target XML에서 allowlisted `<a:t>` 외 변화
- macro deck을 non-quarantine output으로 내보내거나 macro/OLE payload hash가 달라짐
- external relationship을 network로 dereference하거나 target/TargetMode가 달라짐
- SmartArt diagram, OLE embedding, timing, comments, custom XML/unknown part의 payload 또는 rel 변화
- open 후 slide count가 presentation `sldIdLst`의 resolvable slide refs와 다름
- Windows PowerPoint reopen에서 repair dialog 발생, 또는 reopen 후 package 저장 시 PowerPoint가 누락/복구를 보고함
- master Create/Delete가 acceptance criterion인데 unsupported 상태
- pin mismatch, ee/enterprise/app/Electron/AI/branding 파일 유입, NOTICE/SBOM 불완전
- 고객 파일을 외부 서비스/네트워크로 전송해야만 검증할 수 있는 상황

## 확인 범위와 미검증

확인한 사실은 exact-pin source/API/test와 Git metadata에 근거한다. 이 문서 작성 단계에서는 엔진 dependency install, fixture test 실행, 90-slide corpus 실행, LibreOffice 또는 Windows PowerPoint GUI reopen을 수행하지 않았다. 특히 upstream unit test는 package 전체 inventory/closure, 90-slide stress, PowerPoint repair dialog를 증명하지 않으므로 별도 harness 결과가 필요하다.
