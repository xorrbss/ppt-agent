export const PRESENTATION_SOURCE_SHA256 = Symbol(
  "presenton.presentationSourceSha256"
);

export interface PersistedPresentation {
  id?: unknown;
  version?: unknown;
  mode?: unknown;
  theme?: unknown;
  slides?: unknown;
  revision?: unknown;
  lifecycle_status?: unknown;
  profile?: unknown;
  capabilities?: unknown;
  [PRESENTATION_SOURCE_SHA256]?: string;
}
