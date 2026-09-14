import { createHash } from "node:crypto";
import sharp from "sharp";

export const RASTER_FRAGMENT_LIMIT = 128;
export const RASTER_FRAGMENT_BYTES = 32 * 1024 * 1024;
export interface RasterRect { x: number; y: number; width: number; height: number }
export interface LegacyRasterBand { zBand: number; rasterPng: Buffer; semanticOwnerQids: readonly string[] }
export interface FragmentRasterBand extends LegacyRasterBand {
  schemaVersion: "authored-raster-band/v2";
  captureWidthPx: number; captureHeightPx: number; sourceDigest: string;
  backgroundEligible: boolean; opaque: boolean;
  warnings: readonly string[];
  fragments: readonly { id: string; order: number; rect: RasterRect; png: Buffer; digest: string; sizeBytes: number }[];
}
export type RasterBand = LegacyRasterBand | FragmentRasterBand;
export const pngDigest = (bytes: Buffer): string => `sha256:${createHash("sha256").update(bytes).digest("hex")}`;
const seals = new WeakMap<object, string>();
const seal = (band: FragmentRasterBand): string => JSON.stringify({ ...band, rasterPng: pngDigest(band.rasterPng), fragments: band.fragments.map(f => ({ ...f, png: pngDigest(f.png) })) });
export const isFragmentBand = (band: RasterBand): band is FragmentRasterBand => "schemaVersion" in band && band.schemaVersion === "authored-raster-band/v2";
export function assertFragmentBand(band: FragmentRasterBand): void {
  if (seals.get(band) !== seal(band)) throw new Error("Hybrid raster fragment integrity or composition is unverified.");
}
const intersects = (a: RasterRect, b: RasterRect): boolean => a.x < b.x+b.width && b.x < a.x+a.width && a.y < b.y+b.height && b.y < a.y+a.height;

export async function validateFragmentBand(band: FragmentRasterBand): Promise<void> {
  if (seals.get(band) === seal(band)) return;
  if (band.sourceDigest !== pngDigest(band.rasterPng) || band.fragments.length > RASTER_FRAGMENT_LIMIT) throw new Error("Hybrid raster source digest or fragment limit mismatch.");
  const { data, info } = await sharp(band.rasterPng, { limitInputPixels: 32_000_000 }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  if (info.channels !== 4 || info.width !== band.captureWidthPx || info.height !== band.captureHeightPx) throw new Error("Hybrid raster capture dimensions mismatch.");
  const covered = new Uint8Array(info.width * info.height);
  let bytes = 0;
  const ids = new Set<string>();
  for (const [index, fragment] of band.fragments.entries()) {
    const r = fragment.rect;
    if (!/^[A-Za-z0-9._:-]{1,96}$/.test(fragment.id) || ids.has(fragment.id) || fragment.order !== index || !Object.values(r).every(Number.isInteger) || r.x < 0 || r.y < 0 || r.width < 1 || r.height < 1 || r.x+r.width > info.width || r.y+r.height > info.height || band.fragments.slice(0,index).some(f => intersects(r,f.rect))) throw new Error("Hybrid raster fragment rect/order overlap violation.");
    ids.add(fragment.id);
    bytes += fragment.png.length;
    if (fragment.sizeBytes !== fragment.png.length || fragment.digest !== pngDigest(fragment.png)) throw new Error("Hybrid raster fragment digest/size mismatch.");
    const crop = await sharp(fragment.png, { limitInputPixels: 32_000_000 }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    if (crop.info.channels !== 4 || crop.info.width !== r.width || crop.info.height !== r.height) throw new Error("Hybrid raster fragment PNG dimensions mismatch.");
    for (let y=0; y<r.height; y++) for (let x=0; x<r.width; x++) {
      const target = (r.y+y)*info.width+r.x+x, source = y*r.width+x;
      covered[target] = 1;
      for (let channel=0; channel<4; channel++) if (crop.data[source*4+channel] !== data[target*4+channel]) throw new Error("Hybrid raster fragment composition mismatch.");
    }
  }
  const retainedOriginal = band.fragments.length === 1 && band.fragments[0].png.equals(band.rasterPng) && band.fragments[0].rect.x === 0 && band.fragments[0].rect.y === 0;
  if (bytes > RASTER_FRAGMENT_BYTES && !retainedOriginal) throw new Error("Hybrid raster fragment byte limit exceeded.");
  let opaque = true;
  for (let i=0; i<covered.length; i++) { if (data[i*4+3] && !covered[i]) throw new Error("Hybrid raster fragment omitted painted pixels."); if(data[i*4+3] !== 255) opaque = false; }
  if (band.opaque !== opaque || (band.backgroundEligible && band.semanticOwnerQids.length)) throw new Error("Hybrid raster background evidence mismatch.");
  seals.set(band, seal(band));
}

/** Partition occupied scanline runs into nonoverlapping rectangles; retain all alpha, including shadows. */
export async function fragmentRasterBand(input: LegacyRasterBand, backgroundEligible = false, maxFragments = RASTER_FRAGMENT_LIMIT, maxBytes = RASTER_FRAGMENT_BYTES): Promise<FragmentRasterBand> {
  if (!Number.isInteger(maxFragments) || maxFragments < 1 || maxFragments > RASTER_FRAGMENT_LIMIT || !Number.isInteger(maxBytes) || maxBytes < 0 || maxBytes > RASTER_FRAGMENT_BYTES) throw new Error("Hybrid raster fragment budget invalid.");
  const { data, info } = await sharp(input.rasterPng, { limitInputPixels: 32_000_000 }).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  if (info.channels !== 4) throw new Error("Hybrid raster requires RGBA capture.");
  const rects: RasterRect[] = [];
  let active = new Map<string,RasterRect>(), exceeded = false, opaque = true;
  let minX=info.width, minY=info.height, maxX=-1, maxY=-1;
  for (let y=0;y<info.height;y++) {
    const next = new Map<string,RasterRect>();
    for(let x=0;x<info.width;) {
      if(data[(y*info.width+x)*4+3] !== 255) opaque=false;
      if(!data[(y*info.width+x)*4+3]) { x++; continue; }
      const start=x;
      while(x<info.width && data[(y*info.width+x)*4+3]) { if(data[(y*info.width+x)*4+3] !== 255) opaque=false; x++; }
      minX=Math.min(minX,start); maxX=Math.max(maxX,x-1); minY=Math.min(minY,y); maxY=y;
      if(exceeded) continue;
      const key=`${start}:${x-start}`;
      const prior=active.get(key);
      if(prior) { prior.height++; next.set(key,prior); }
      else { const r={x:start,y,width:x-start,height:1}; rects.push(r); next.set(key,r); if(rects.length>maxFragments) exceeded=true; }
    }
    active=next;
  }
  const warnings: string[] = [];
  let crops = rects;
  if(exceeded) { crops=maxX<0?[]:[{x:0,y:0,width:info.width,height:info.height}]; warnings.push("raster-fragment-count-limit"); }
  if(backgroundEligible && opaque) crops=[{x:0,y:0,width:info.width,height:info.height}];
  let bytes=0;
  let fragments: FragmentRasterBand["fragments"][number][]=[];
  for(const r of crops) {
    const png=r.x===0 && r.y===0 && r.width===info.width && r.height===info.height ? input.rasterPng : await sharp(input.rasterPng).extract({left:r.x,top:r.y,width:r.width,height:r.height}).png().toBuffer(); bytes+=png.length;
    if(bytes>maxBytes) { warnings.push("raster-fragment-byte-limit"); fragments=[]; break; }
    fragments.push({id:`fragment-${fragments.length}`,order:fragments.length,rect:r,png,digest:pngDigest(png),sizeBytes:png.length});
  }
  if(warnings.includes("raster-fragment-byte-limit")) fragments=[{id:"original",order:0,rect:{x:0,y:0,width:info.width,height:info.height},png:input.rasterPng,digest:pngDigest(input.rasterPng),sizeBytes:input.rasterPng.length}];
  const band: FragmentRasterBand={...input,schemaVersion:"authored-raster-band/v2",captureWidthPx:info.width,captureHeightPx:info.height,sourceDigest:pngDigest(input.rasterPng),backgroundEligible,opaque,warnings,fragments};
  await validateFragmentBand(band);
  return band;
}
