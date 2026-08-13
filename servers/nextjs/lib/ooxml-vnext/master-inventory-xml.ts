import { posix } from "node:path";

import type { PackageGraph, PackageRelationship } from "./package-graph.ts";
import { decodeAndValidateOoxmlXml, type OoxmlSecurityPolicy } from "./security.ts";
import {
  MasterInventoryError,
  type MasterLayoutRelationshipRecord,
} from "./master-inventory-contracts.ts";

export function relationshipPartFor(source: string): string {
  return posix.join(posix.dirname(source), "_rels", `${posix.basename(source)}.rels`);
}

export function isRelationshipType(
  relationship: PackageRelationship,
  localName: string
): boolean {
  return relationship.type.endsWith(`/${localName}`);
}

function xmlUnescape(value: string): string {
  return value.replace(
    /&(#x[0-9a-f]+|#\d+|amp|apos|gt|lt|quot);/gi,
    (match, entity: string) => {
      if (entity[0] === "#") {
        const hexadecimal = entity[1]?.toLowerCase() === "x";
        const point = Number.parseInt(entity.slice(hexadecimal ? 2 : 1), hexadecimal ? 16 : 10);
        if (!Number.isInteger(point) || point < 0 || point > 0x10ffff) return match;
        return String.fromCodePoint(point);
      }
      return ({ amp: "&", apos: "'", gt: ">", lt: "<", quot: '"' } as const)[
        entity.toLowerCase() as "amp" | "apos" | "gt" | "lt" | "quot"
      ] ?? match;
    }
  );
}

function attributes(fragment: string): Readonly<Record<string, string>> {
  const result: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const match of fragment.matchAll(/([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g)) {
    result[match[1]] = xmlUnescape(match[2] ?? match[3] ?? "");
  }
  return Object.freeze(result);
}

function relationshipId(attrs: Readonly<Record<string, string>>): string | null {
  const item = Object.entries(attrs).find(([name]) => name.includes(":") && name.endsWith(":id"));
  return item?.[1] ?? null;
}

export function orderedIdReferences(
  xml: string,
  localName: "sldMasterId" | "sldLayoutId"
): readonly { relationshipId: string; numericId: string | null; order: number }[] {
  const records: { relationshipId: string; numericId: string | null; order: number }[] = [];
  const pattern = new RegExp(`<(?:[A-Za-z_][\\w.-]*:)?${localName}\\b([^>]*)\\/?>`, "gi");
  for (const match of xml.matchAll(pattern)) {
    const attrs = attributes(match[1]);
    const relId = relationshipId(attrs);
    if (relId) records.push({ relationshipId: relId, numericId: attrs.id ?? null, order: records.length });
  }
  return Object.freeze(records.map((record) => Object.freeze(record)));
}

export function commonSlideDataName(xml: string): string | null {
  const match = /<(?:[A-Za-z_][\w.-]*:)?cSld\b([^>]*)>/i.exec(xml);
  return match ? attributes(match[1]).name ?? null : null;
}

export function xmlFor(
  graph: PackageGraph,
  partName: string,
  policy: Readonly<OoxmlSecurityPolicy>
): string {
  const bytes = graph.entries.get(partName);
  if (!bytes) throw new MasterInventoryError("MISSING_PART", `Required OOXML part is missing: ${partName}`);
  return decodeAndValidateOoxmlXml(partName, bytes, policy);
}

export function relationshipSnapshot(
  relationship: PackageRelationship
): MasterLayoutRelationshipRecord {
  return Object.freeze({
    source: relationship.source,
    relsPart: relationship.relsPart,
    id: relationship.id,
    type: relationship.type,
    target: relationship.target,
    targetMode: relationship.targetMode,
    targetModeRaw: relationship.targetModeRaw,
    normalizedTarget: relationship.normalizedTarget,
    digest: relationship.digest,
  });
}

export function partNamesForContentType(
  graph: PackageGraph,
  contentType: string
): readonly string[] {
  return Object.freeze(
    graph.parts
      .filter((part) => part.contentType === contentType)
      .map((part) => part.name)
      .sort((left, right) => left.localeCompare(right, "en"))
  );
}
