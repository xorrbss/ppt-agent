/**
 * A deliberately narrow local harness override. The durable product contract
 * remains a 90-slide deck; accepting arbitrary public values here would make
 * a development convenience into a second production contract.
 */
export const AUTHORED_VNEXT_DEV_SLIDE_COUNT_FEATURE_FLAG =
  "NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT" as const;

export function authoredVnextSlideCount(
  value = process.env.NEXT_PUBLIC_AUTHORED_VNEXT_DEV_SLIDE_COUNT,
  nodeEnvironment = process.env.NODE_ENV,
): 3 | 90 {
  return nodeEnvironment !== "production" && value === "3" ? 3 : 90;
}
