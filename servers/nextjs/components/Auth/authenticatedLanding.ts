export type AuthenticatedLanding = "onboarding" | "upload";

type AuthenticatedLandingOptions = {
  canChangeKeys: boolean;
  hasValidConfig: boolean;
  forceOnboarding?: boolean;
};

/**
 * Selects the first authenticated screen without coupling authentication to
 * provider setup. Deployments with immutable, server-managed keys can enter
 * the app directly; locally configurable deployments stay in onboarding until
 * their LLM configuration is valid.
 */
export function getAuthenticatedLanding({
  canChangeKeys,
  hasValidConfig,
  forceOnboarding = false,
}: AuthenticatedLandingOptions): AuthenticatedLanding {
  if (!canChangeKeys) {
    return "upload";
  }

  if (forceOnboarding || !hasValidConfig) {
    return "onboarding";
  }

  return "upload";
}
