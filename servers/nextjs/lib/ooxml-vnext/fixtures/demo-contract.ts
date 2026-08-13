import type { DeckMasterContract } from "../contracts.ts";

const COLORS = Object.freeze({
  dk1: "#07101F",
  lt1: "#FFFFFF",
  dk2: "#22344C",
  lt2: "#D9E2EF",
  accent1: "#1463FF",
  accent2: "#19B5A5",
  accent3: "#F4B740",
  accent4: "#E85D75",
  accent5: "#7257D9",
  accent6: "#37A65A",
  hlink: "#1463FF",
  folHlink: "#7257D9",
});

const ownership = Object.freeze({ locked: false, modelWritable: true });
const lockedOwnership = Object.freeze({ locked: true, modelWritable: false });

export const DEMO_DECK_MASTER_CONTRACT = Object.freeze({
  contractVersion: "deckmaster-contract/v1",
  id: "master:ooxml-vnext-demo",
  name: "OOXML vNext Demo Master",
  slideSizePx: { x: 0, y: 0, width: 1280, height: 720 },
  theme: { name: "OOXML vNext Navy", colors: COLORS },
  tokens: {
    "color.background": "#FFFFFF",
    "color.text": "#07101F",
    "color.accent": "#1463FF",
    "spacing.safe.left": 76,
    "spacing.safe.right": 76,
  },
  fontManifest: {
    primary: "Malgun Gothic",
    fallbacks: ["Arial", "sans-serif"],
    windowsFallback: "Malgun Gothic",
    themeScripts: {
      latin: "Malgun Gothic",
      eastAsian: "Malgun Gothic",
      complexScript: "Arial",
      Hang: "Malgun Gothic",
      Jpan: "Yu Gothic",
      Hans: "Microsoft YaHei",
      Hant: "Microsoft JhengHei",
    },
    files: [
      {
        style: "regular",
        path: "C:/Windows/Fonts/malgun.ttf",
        version: "6.68",
        sizeBytes: 13_457_164,
        sha256: "0".repeat(64),
      },
    ],
  },
  furniture: [
    {
      id: "furniture:logo",
      kind: "logo",
      bounds: { x: 32, y: 24, width: 28, height: 28 },
      visibility: "always",
      ownership: lockedOwnership,
    },
    {
      id: "furniture:top-rail",
      kind: "rect",
      bounds: { x: 0, y: 0, width: 1280, height: 10 },
      fill: "#1463FF",
      ownership: lockedOwnership,
    },
    {
      id: "furniture:brand",
      kind: "text",
      text: "PRESENTON · AUTHORED vNEXT",
      bounds: { x: 76, y: 35, width: 500, height: 28 },
      fill: "#22344C",
      ownership: lockedOwnership,
    },
    {
      id: "furniture:footer-rule",
      kind: "line",
      bounds: { x: 76, y: 672, width: 1128, height: 1 },
      stroke: "#D9E2EF",
      ownership: lockedOwnership,
    },
    {
      id: "furniture:page-number",
      kind: "pageNumber",
      text: "1",
      bounds: { x: 1160, y: 680, width: 44, height: 22 },
      fill: "#22344C",
      visibility: "exceptCover",
      ownership: lockedOwnership,
    },
  ],
  layouts: [
    {
      id: "layout:cover",
      name: "Cover",
      kind: "cover",
      placeholders: [
        {
          id: "placeholder:cover-title",
          type: "title",
          index: 0,
          bounds: { x: 76, y: 168, width: 1128, height: 190 },
          ownership,
          textStyle: {
            fontPt: 44,
            lineHeight: 1.12,
            maxLines: 3,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
        {
          id: "placeholder:cover-body",
          type: "body",
          index: 1,
          bounds: { x: 86, y: 398, width: 1090, height: 120 },
          ownership,
          textStyle: {
            fontPt: 22,
            lineHeight: 1.25,
            maxLines: 3,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
      ],
    },
    {
      id: "layout:section",
      name: "Section",
      kind: "section",
      placeholders: [
        {
          id: "placeholder:section-title",
          type: "title",
          index: 0,
          bounds: { x: 76, y: 205, width: 1128, height: 145 },
          ownership,
          textStyle: {
            fontPt: 42,
            lineHeight: 1.12,
            maxLines: 2,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
        {
          id: "placeholder:section-body",
          type: "body",
          index: 1,
          bounds: { x: 86, y: 380, width: 1090, height: 130 },
          ownership,
          textStyle: {
            fontPt: 20,
            lineHeight: 1.25,
            maxLines: 4,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
      ],
    },
    {
      id: "layout:standard",
      name: "Standard",
      kind: "standard",
      placeholders: [
        {
          id: "placeholder:standard-title",
          type: "title",
          index: 0,
          bounds: { x: 76, y: 92, width: 1128, height: 126 },
          ownership,
          textStyle: {
            fontPt: 38,
            lineHeight: 1.12,
            maxLines: 2,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
        {
          id: "placeholder:standard-body",
          type: "body",
          index: 1,
          bounds: { x: 86, y: 260, width: 1090, height: 300 },
          ownership,
          textStyle: {
            fontPt: 20,
            lineHeight: 1.25,
            maxLines: 8,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
      ],
    },
    {
      id: "layout:long-title",
      name: "Long Title",
      kind: "long-title",
      placeholders: [
        {
          id: "placeholder:long-title",
          type: "title",
          index: 0,
          bounds: { x: 76, y: 92, width: 1128, height: 250 },
          ownership,
          textStyle: {
            fontPt: 35,
            lineHeight: 1.12,
            maxLines: 3,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
        {
          id: "placeholder:long-body",
          type: "body",
          index: 1,
          bounds: { x: 86, y: 390, width: 1090, height: 170 },
          ownership,
          textStyle: {
            fontPt: 18,
            lineHeight: 1.3,
            maxLines: 5,
            marginsPx: { top: 5, right: 10, bottom: 5, left: 10 },
          },
        },
      ],
    },
  ],
} satisfies DeckMasterContract);
