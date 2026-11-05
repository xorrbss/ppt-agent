/**
 * The canonical provider registry generates one English sentence per provider
 * (`utils/provider-registry.generated.ts`). The display layer localizes that
 * sentence; any other description is shown as the registry sent it.
 */
const REGISTRY_DESCRIPTION = /^(.+) provider managed by the canonical provider registry\.$/u;

export function localizeProviderDescription(
  description: string,
  translateRegistryDescription: (providerName: string) => string,
): string {
  const match = REGISTRY_DESCRIPTION.exec(description);
  return match ? translateRegistryDescription(match[1]) : description;
}
