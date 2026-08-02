# PPT Master 후보 권리 검증 및 공개 준비 런북

작성일: 2026-08-02

감사 대상: `https://github.com/hugohe3/ppt-master`

고정 revision: `8858c4960b870f12baf952994fbd0c76ab5ba4ec`

## 현재 결론

PPT Master 후보 4종은 모두 `blocked` 상태를 유지한다. 저장소 루트의 MIT 라이선스와 저작권 고지는 확인했지만, 후보 PPTX에 포함된 사진·생성 이미지·파생 SVG의 재배포 권리를 개별적으로 입증하는 문서는 찾지 못했다. 저장소 라이선스를 임베디드 미디어의 권리 승인으로 추정하지 않는다.

| 후보 | 원본 PPTX SHA-256 | 크기 | 현재 차단 근거 |
|---|---|---:|---|
| Swiss Grid | `8082dcb4d6c9ff93c60912daa417d62b1b31a3744fe2dd9d9f3b5e68a3e0745d` | 1,555,342 bytes | 생성 이미지가 `ai / Pending`으로만 기록됨 |
| Editorial Magazine | `095f9085bd12d03e10cb537078e3a903047e710af85fdb7301f7297e2674f625` | 15,854,611 bytes | 사진이 `user / Existing`으로만 기록되고 라이선스·제공자 권한 증빙이 없음 |
| Data Journalism | `2a323935de5a736e301e46b00707521026c31b1436663d7d7ebdec3166991972` | 3,486,177 bytes | 생성 이미지가 `ai / Pending`으로만 기록됨 |
| Glassmorphism SaaS | `291bdc5f8a203a7076d387bc667b67b074c2a9bd94d5e7ee0892d8b22b44b46b` | 7,843,702 bytes | 생성 이미지가 `ai / Pending`으로만 기록됨 |

감사 시 후보 디렉터리에서 자산별 `LICENSE`, `NOTICE`, `CREDIT`, `ATTRIBUTION`, `SOURCE` 문서와 폰트 바이너리를 별도로 검색했다. 권리 승인에 사용할 수 있는 자산별 문서는 없었고, 폰트 파일도 없었다. 디자인 사양의 `Source` 또는 `Image Resource List`는 제작 메모이므로 법적 권리 증거로 취급하지 않는다.

## 기계 판독 가능한 검증 자료

- 디자인·출처 카탈로그: `servers/fastapi/templates/v2/manifests/ppt_master_catalog.v1.json`
- 권리 증거 및 반입 영수증: `servers/fastapi/templates/v2/manifests/ppt_master_rights.v1.json`
- 검증기: `servers/fastapi/templates/v2/catalog.py`

권리 매니페스트는 후보별로 다음을 고정한다.

- 저장소 URL과 40자 commit SHA
- 후보 PPTX의 저장소 상대 경로, SHA-256, byte 크기
- 해당 카탈로그 엔트리 전체의 canonical SHA-256
- 필수 권리 범위: 후보 PPTX와 모든 임베디드 미디어
- 필수 허가: 재배포, 수정, 상업 이용
- 로컬 증거 문서의 경로와 SHA-256
- 증거 전체를 묶은 approval digest와 검토자·검토 시각·결정문

현재 네 레코드의 `evidence`는 비어 있고 `approval.status`는 `pending`이다. 이 값은 누락이 아니라 확인되지 않은 권리를 명시적으로 표현한다.

## 운영자 승인 절차

1. 권리 보유자 또는 적법한 제공자로부터 서면 증빙을 받는다. 문서에는 식별된 PPTX 전체와 그 안의 모든 미디어에 대해 재배포·수정·상업 이용이 허용된다는 범위가 명시돼야 한다.
2. 증빙 파일을 권리 매니페스트 디렉터리 아래 `evidence/`에 저장한다. 외부 URL만 기록하거나 실행 중 다운로드하는 방식은 허용하지 않는다.
3. `ppt_master_rights.v1.json`의 해당 레코드에 증거 항목을 추가한다. `document_sha256`은 증빙 파일, `covered_artifact_sha256`은 후보 PPTX를 가리켜야 한다.
4. 증거의 `covered_scopes`가 `candidate_pptx`와 `embedded_media`를 모두 포함하는지 확인한다. 폰트 파일이 추가된 revision이면 `fonts`도 추가한다.
5. 증거의 `granted_permissions`가 `redistribute`, `modify`, `commercial_use`를 모두 포함하는지 확인한다. 하나라도 불명확하면 계속 차단한다.
6. 먼저 `approval.status=pending` 상태에서 레코드를 로드하고 `rights_evidence_set_sha256()` 결과를 산출한다.
7. 독립된 권리 검토자가 발행 주체, 권한, 대상 digest, 허용 범위, attribution 조건을 확인한다. 작성자 본인의 자기 승인은 운영 정책상 허용하지 않는 것을 권장한다.
8. 승인할 때만 검토자, timezone을 포함한 시각, 결정문, 정확한 evidence-set digest와 함께 `approval.status=approved`로 변경한다.
9. `evaluate_rights_readiness()`가 blocker 없이 `ready_for_publication_review=true`를 반환하는지 검증한다.
10. 권리 준비 완료는 자동 공개를 의미하지 않는다. 원본을 revision 전용 저장소로 반입한 뒤 digest·크기·OOXML 보존 preflight와 Template V2 publication gate를 별도로 통과시킨 후 카탈로그 공개 상태를 변경한다.

## 승인 전 필수 체크리스트

- [ ] source repository, commit, PPTX path가 카탈로그와 일치한다.
- [ ] PPTX SHA-256과 byte 크기가 반입 대상 파일과 일치한다.
- [ ] catalog-entry digest가 최신이다.
- [ ] 증빙 문서가 로컬에 존재하며 문서 SHA-256이 일치한다.
- [ ] 증빙 발행 주체가 권리를 부여할 권한을 보유했는지 확인했다.
- [ ] 후보 PPTX 전체와 모든 임베디드 미디어가 명시적으로 포함된다.
- [ ] 재배포, 수정, 상업 이용이 모두 허가됐다.
- [ ] 필수 attribution 문구와 라이선스 고지 위치를 기록했다.
- [ ] approval digest가 현재 증거 세트와 일치한다.
- [ ] 검토자와 timezone 포함 검토 시각, 결정문이 기록됐다.
- [ ] `evaluate_rights_readiness()`가 blocker 없이 통과한다.
- [ ] native PPTX 저장 영수증과 OOXML 보존 preflight가 별도로 통과한다.
- [ ] 공개 후 UI 목록·직접 조회·생성 경로의 fail-closed 회귀 테스트가 통과한다.

## 변경 시 재승인 규칙

다음 중 하나라도 바뀌면 기존 승인은 무효로 간주하고 새 증거 세트와 새 approval digest를 만들어야 한다.

- upstream commit 또는 repository
- 후보 PPTX 경로, 내용, SHA-256, 크기
- 카탈로그 엔트리의 출처·권리·자산 정보
- 증빙 문서의 내용이나 digest
- 필수 권리 범위 또는 허가 종류

검증기는 catalog-entry digest, source revision, PPTX digest, 증빙 문서 digest, approval digest의 불일치를 모두 차단한다. 증거가 준비되더라도 카탈로그의 `publication_status`를 자동으로 변경하지 않는다.

## 검증 명령

```powershell
cd servers/fastapi
uv run pytest tests/unit/test_template_v2_ppt_master_catalog.py -q
uv run pytest tests/unit/test_structured_templates_api.py tests/unit/test_template_v2_publication.py -q
```

증거가 부족하거나 서로 충돌하면 후보는 계속 `blocked`여야 한다. 권리를 추정해 boolean만 변경하는 것은 승인 절차가 아니다.
