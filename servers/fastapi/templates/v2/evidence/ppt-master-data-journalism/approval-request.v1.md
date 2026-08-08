# Data Journalism Template V2 approval request

Status: `pending-independent-review`

Prepared: 2026-08-02 (Asia/Seoul)

This document is an approval request and provenance summary. It is not itself a
rights-holder grant, an operator approval, or an HMAC attestation.

## Requested publication artifact

- Template ID: `ppt-master-data-journalism`
- Product name: `Data Journalism`
- Artifact: `docs/artifacts/ppt-master-data-journalism/ai-capital-2026-data-journalism.pptx`
- Repository revision containing the artifact: `f1dd602db95274d0048ff05e3bf87ee3d10ec348`
- SHA-256: `5ea1bd7f13ebaa324be3b60167bb259c7049d13c33865c0abaa2306a17d67e37`
- Size: `192581` bytes
- Preservation proposal: `native_clone`

The requested artifact is intentionally different from the upstream sample
`examples/ppt169_global_ai_capital_2026/exports/global_ai_capital_2026.pptx`
whose SHA-256 is
`2a323935de5a736e301e46b00707521026c31b1436663d7d7ebdec3166991972`.
The upstream sample must not be approved or substituted for this request.

## Verified package facts

- 20 slides, one slide master, 11 slide layouts, and 20 notes slides are retained.
- The PPTX ZIP contains no part below `ppt/media/`, `ppt/embeddings/`, or
  `ppt/fonts/`.
- No logo or verbatim quotation is included.
- The three raster images present in the upstream sample were removed.
- All content is represented with editable native shapes and text.
- The package rendered successfully for all 20 slides and passed the retained
  template-frame fidelity check with zero reported issues.

## License and provenance chain for review

1. The design source is `https://github.com/hugohe3/ppt-master` at commit
   `8858c4960b870f12baf952994fbd0c76ab5ba4ec`.
2. The source repository's `LICENSE` is MIT, copyright 2025-2026 Hugo He. Its
   pinned file SHA-256 is
   `cd9cf27f52d3c336dd42e13f4a1df453da118e25e15520bbe02f91d75531e91b`.
3. The local repository license is Apache-2.0. Its file SHA-256 at the requested
   repository revision is
   `72daa21cf08efa3d5f3580a34a6f48eaa0077a9deee8d260df3b50cd7fc3d01a`.
4. The downstream deck was rebuilt for this project with new Korean editorial
   content and native charts. It does not copy the upstream raster media.
5. Numeric claims are attributed in presenter notes to official public source
   pages from Stanford HAI, IEA, NVIDIA, Alphabet, OpenAI, and Anthropic. The
   deck does not reproduce their charts, logos, photographs, or long-form text.

The independent reviewer must determine whether the MIT grant, the project's
license and contribution authority, and the source-attribution approach jointly
cover redistribution, modification, and commercial use of the exact requested
artifact. This request does not make that legal determination on the reviewer's
behalf.

## Independent reviewer checklist

- [ ] Recompute the requested artifact SHA-256 and byte size.
- [ ] Confirm the requested artifact contains no media, embedding, or embedded
      font package parts.
- [ ] Confirm the upstream MIT license at the pinned source commit.
- [ ] Confirm the project has authority to license the newly authored content.
- [ ] Confirm attribution requirements are satisfied in the artifact and
      accompanying distribution notices.
- [ ] Confirm the official-source facts are paraphrased and not copied charts or
      protected expressive content.
- [ ] Approve or reject all three required permissions: `redistribute`,
      `modify`, and `commercial_use`.
- [ ] Record a stable reviewer identifier and a timezone-aware review time.
- [ ] Sign the evidence-set digest with the deployment-held HMAC key.

## Operator prerequisites after rights review

The following deployment values must be supplied outside the repository and
must never be written into this document, Git history, CI logs, or chat:

- `TEMPLATE_V2_RIGHTS_APPROVER_ALLOWLIST`
- `TEMPLATE_V2_RIGHTS_ATTESTATION_KEY` (at least 32 bytes)

Publication also requires a shared Template V2 database row, durable native
PPTX storage scoped to template ID and revision, publishable native-clone
metadata, `ENABLE_TEMPLATE_V2=true`, and an exact
`TEMPLATE_V2_TEMPLATE_ALLOWLIST` entry. Until those checks pass, the catalog
must remain `blocked`.
