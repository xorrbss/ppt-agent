# PPT Master 후보 권리 검증 및 공개 절차

작성일: 2026-08-02
감사 대상: `https://github.com/hugohe3/ppt-master`
고정 revision: `8858c4960b870f12baf952994fbd0c76ab5ba4ec`

## 현재 결론

후보 4종은 모두 `blocked`를 유지한다. 저장소 코드는 MIT이지만, PPTX에 포함된 AI 이미지·외부 사진·브랜드 로고·아이콘·인용문·데이터의 수정 및 재배포 권리가 별도로 입증되지 않았다. 저장소 공개 여부나 매니페스트의 boolean 변경만으로는 승인할 수 없다.

| 후보 | PPTX SHA-256 | 크기 | 주 차단 사유 |
|---|---|---:|---|
| Swiss Grid | `8082dcb4d6c9ff93c60912daa417d62b1b31a3744fe2dd9d9f3b5e68a3e0745d` | 1,555,342 | AI 이미지 3개가 Pending, 브랜드 로고와 인용문 권리 미확인 |
| Editorial Magazine | `095f9085bd12d03e10cb537078e3a903047e710af85fdb7301f7297e2674f625` | 15,854,611 | PPTX에 외부 건축 PNG 20개 내장, upstream 소스에는 raster 25개·vector 22개 존재, 출처·사진가·라이선스 없음 |
| Data Journalism | `2a323935de5a736e301e46b00707521026c31b1436663d7d7ebdec3166991972` | 3,486,177 | AI 이미지 3개 Pending, 브랜드·아이콘·데이터 권리 미확인 |
| Glassmorphism SaaS | `291bdc5f8a203a7076d387bc667b67b074c2a9bd94d5e7ee0892d8b22b44b46b` | 7,843,702 | AI 이미지 8개 Pending, Phosphor MIT 고지 누락 |

## 승인에 필요한 증거

후보별로 다음을 고정 commit, PPTX SHA-256 및 자산 SHA-256에 연결한다.

- PPTX와 모든 내장 자산의 복제·수정·상업 이용·재배포 허가
- AI 이미지의 생성 계정 소유자, provider/model/version, job ID, 생성 시각, 입력물 provenance, 당시 약관 사본과 출력 SHA-256
- 외부 사진의 원본 URL, 창작자·권리자, 라이선스 버전 또는 서면 허가, 필수 attribution
- Simple Icons 로고별 브랜드 가이드와 상표 사용 근거
- Tabler/Phosphor의 정확한 upstream revision과 배포물에 포함할 MIT 저작권·허가문
- 인용문·데이터·기사의 정확한 URL, 작성자, 발행일, 재사용 조건

증거 파일은 `servers/fastapi/templates/v2/evidence/`에 보관하고, `ppt_master_rights.v1.json`의 각 항목에 로컬 경로·문서 SHA-256·대상 자산 SHA-256·허가 범위를 기록한다.

## 외부 승인 및 HMAC 증명

저장소 수정자가 스스로 승인 레코드를 만들어 공개하는 것을 막기 위해, 배포 환경에서 다음 두 값을 별도로 관리한다.

- `TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST`: 독립 검토자 식별자의 쉼표 구분 목록
- `TEMPLATE_V2_RIGHTS_ATTESTATION_KEY`: 저장소와 CI 로그에 남기지 않는 32바이트 이상의 비밀값

승인 절차:

1. `approval.status=pending` 상태에서 `rights_evidence_set_sha256()`를 계산한다.
2. 독립 검토자가 발행 주체, 허가 범위, attribution, 원본·증거·카탈로그 digest를 확인한다.
3. `reviewer`, timezone 포함 `reviewed_at`, `evidence_set_sha256`, `decision`을 확정한다.
4. 배포 비밀을 사용해 `rights_approval_attestation_hmac()`로 HMAC-SHA256을 계산하고 `attestation_hmac_sha256`에 기록한다.
5. 검토자 allowlist와 HMAC 키가 주입된 배포 환경에서 `evaluate_rights_readiness()`가 blocker 없이 통과하는지 확인한다.

비밀값 자체, 이를 출력하는 셸 history, 임시 파일은 커밋하지 않는다. evidence, catalog entry, 후보 PPTX, 검토자 또는 검토 시각이 바뀌면 기존 HMAC은 무효이며 재검토·재서명이 필요하다.

## 기술 출판 게이트

권리 승인만으로 공개하지 않는다. 공개할 정확한 revision의 `assets.native_pptx`가 다음 조건을 모두 만족해야 한다.

- catalog의 후보 경로·SHA-256과 rights receipt의 경로·SHA-256이 일치
- `source_sha256`, `source_size_bytes`, `preservation_mode`가 승인된 후보와 일치
- publication evidence가 `publishable`
- 저장된 PPTX를 다시 읽었을 때 실제 SHA-256과 크기가 일치
- master/layout/theme/relationships/media 보존 preflight와 생성·내보내기 회귀 테스트 통과
- 공개 목록, 직접 생성, catalog prompt 생성이 같은 통합 게이트를 사용

후보 4종은 누락된 notes master 관계 때문에 현재 artifact import가 실패한다. 임의로 원본을 수정해 승인된 SHA를 바꾸지 말고, upstream 수정본을 새 revision·새 SHA로 다시 감사한다.

## 상태 변경 체크리스트

- [ ] 모든 필수 권리 범위와 자산이 증거 문서에 포함됨
- [ ] 증거 파일과 covered artifact digest가 실제 파일과 일치함
- [ ] 독립 검토자가 승인했고 배포 환경 allowlist에 존재함
- [ ] HMAC attestation이 현재 evidence digest와 일치함
- [ ] catalog·rights·DB source의 경로, SHA-256, 크기, preservation mode가 일치함
- [ ] 실제 저장 객체 무결성 검사가 통과함
- [ ] OOXML import/render/export 회귀 테스트가 통과함
- [ ] 공개 목록·직접 생성·호환 생성 경로가 모두 fail-closed임
- [ ] 기존 V1 문서의 직접 조회·rehydrate·render 호환성이 유지됨

## 검증 명령

```powershell
cd servers/fastapi
uv run pytest tests/unit/test_template_v2_ppt_master_catalog.py -q
uv run pytest tests/unit/test_structured_templates_api.py tests/unit/test_template_v2_publication.py -q
uv run pytest tests/unit/test_template_v2_generation_entrypoint.py tests/unit/test_template_v2_prompted_layout.py -q
```

하나라도 실패하면 후보는 계속 `blocked`여야 한다.
