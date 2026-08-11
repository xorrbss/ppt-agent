type PathContainer = Record<string | number, unknown>;

export function setNestedMediaValue(
  obj: PathContainer,
  path: string,
  url: string,
  query: string | undefined,
  urlKey: "__image_url__" | "__icon_url__",
  queryKey: "__image_prompt__" | "__icon_query__",
) {
  const keys = path.split(/[.\[\]]+/).filter(Boolean);
  let current = obj;
  for (let index = 0; index < keys.length - 1; index += 1) {
    const key = Number.isNaN(Number(keys[index])) ? keys[index] : Number(keys[index]);
    current[key] ||= {};
    current = current[key] as PathContainer;
  }
  const finalKey = Number.isNaN(Number(keys.at(-1))) ? keys.at(-1)! : Number(keys.at(-1));
  const existing = current[finalKey];
  current[finalKey] = {
    ...(existing && typeof existing === "object" ? existing : {}),
    [urlKey]: url,
    [queryKey]: query || ((existing as Record<string, unknown> | undefined)?.[queryKey] as string | undefined) || "",
  };
}
