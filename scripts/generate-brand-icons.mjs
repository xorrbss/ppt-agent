import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import sharp from "sharp";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const repositoryRoot = path.resolve(scriptDirectory, "..");
const defaultSourcePath = path.join(
  repositoryRoot,
  "servers",
  "nextjs",
  "public",
  "dx-browser-icon-v2.png",
);
const sourcePath = process.argv[2] || defaultSourcePath;
const absoluteSourcePath = path.resolve(sourcePath);

await fs.access(absoluteSourcePath);

const resolveFromRoot = (...segments) =>
  path.join(repositoryRoot, ...segments);

async function writeSquarePng(relativePath, size) {
  const outputPath = resolveFromRoot(...relativePath.split("/"));
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await sharp(absoluteSourcePath)
    .resize(size, size, { fit: "cover" })
    .png({ compressionLevel: 9 })
    .toFile(outputPath);
}

async function createPngBuffer(size) {
  return sharp(absoluteSourcePath)
    .resize(size, size, { fit: "cover" })
    .ensureAlpha()
    .png({ compressionLevel: 9 })
    .toBuffer();
}

async function writeIco(relativePath, sizes) {
  const images = await Promise.all(sizes.map(createPngBuffer));
  const directorySize = 6 + images.length * 16;
  const directory = Buffer.alloc(directorySize);

  directory.writeUInt16LE(0, 0);
  directory.writeUInt16LE(1, 2);
  directory.writeUInt16LE(images.length, 4);

  let imageOffset = directorySize;
  images.forEach((image, index) => {
    const size = sizes[index];
    const entryOffset = 6 + index * 16;

    directory.writeUInt8(size === 256 ? 0 : size, entryOffset);
    directory.writeUInt8(size === 256 ? 0 : size, entryOffset + 1);
    directory.writeUInt8(0, entryOffset + 2);
    directory.writeUInt8(0, entryOffset + 3);
    directory.writeUInt16LE(1, entryOffset + 4);
    directory.writeUInt16LE(32, entryOffset + 6);
    directory.writeUInt32LE(image.length, entryOffset + 8);
    directory.writeUInt32LE(imageOffset, entryOffset + 12);

    imageOffset += image.length;
  });

  const outputPath = resolveFromRoot(...relativePath.split("/"));
  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  await fs.writeFile(outputPath, Buffer.concat([directory, ...images]));
}

async function writeWideAppxLogo() {
  const iconBuffer = await createPngBuffer(150);
  const outputPath = resolveFromRoot(
    "electron",
    "build",
    "appx",
    "Wide310x150Logo.png",
  );

  await sharp({
    create: {
      width: 310,
      height: 150,
      channels: 4,
      background: "#ffffff",
    },
  })
    .composite([{ input: iconBuffer, left: 80, top: 0 }])
    .png({ compressionLevel: 9 })
    .toFile(outputPath);
}

const squarePngTargets = [
  ["servers/nextjs/public/logo-with-bg.png", 512],
  ["servers/nextjs/app/icon.png", 512],
  ["servers/nextjs/app/apple-icon.png", 180],
  ["electron/resources/ui/assets/images/presenton_short_filled.png", 512],
  ["electron/build/logo.png", 1024],
  ["electron/build/appx/Square44x44Logo.png", 44],
  ["electron/build/appx/Square44x44LogoTransParent.png", 44],
  ["electron/build/appx/Square150x150Logo.png", 150],
  ["electron/build/appx/Square150x150LogoTransParent.png", 150],
  ["electron/build/appx/StoreLogo.png", 1080],
  ["electron/build/appx/StoreLogoTransParent.png", 1080],
];

for (const size of [16, 32, 48, 64, 128, 256, 512]) {
  squarePngTargets.push([
    `electron/build/icons/${size}x${size}.png`,
    size,
  ]);
}

await Promise.all(
  squarePngTargets.map(([relativePath, size]) =>
    writeSquarePng(relativePath, size),
  ),
);

const icoSizes = [16, 32, 48, 64, 128, 256];
await Promise.all([
  writeIco("servers/nextjs/app/favicon.ico", icoSizes),
  writeIco("electron/build/icon.ico", icoSizes),
  writeIco("electron/build/appx/icon.ico", icoSizes),
  writeWideAppxLogo(),
]);

console.log(
  `Generated ${squarePngTargets.length + 4} brand icon assets from ${absoluteSourcePath}`,
);
