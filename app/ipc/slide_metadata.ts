import { ipcMain } from "electron";
import puppeteer from "puppeteer";
import fs from 'fs';
import path from 'path';
import { tempDir } from "../constants";
interface Position {
  left: number;
  top: number;
  width: number;
  height: number;
}

interface FontStyles {
  name: string;
  size: number;
  bold: boolean;
  weight: number;
  color: string;
}

interface TextElement {
  position: Position;
  paragraphs: {
    alignment: number;
    text: string;
    font: FontStyles;
  }[];
}

interface PictureElement {
  position: Position;
  picture: {
    is_network: boolean;
    path: string;
  };
  shape: string | null;
  object_fit: {
    fit: string | null;
    focus: number[];
  };
  overlay: string | null;
  border_radius: number[];
}

interface BoxElement {
  position: Position;
  type: number;
  fill: {
    color: string;
  };
  border_radius: number;
  stroke: {
    color: string;
    thickness: number;
  };
  shadow: {
    radius: number;
    color: string;
    offset: number;
    opacity: number;
    angle: number;
  };
}

interface LineElement {
  position: Position;
  lineType: number;
  thickness: string;
  color: string;
}

interface GraphElement {
  position: Position;
  picture: {
    is_network: boolean;
    path: string;
  };
  border_radius: number[];
}

type SlideElement = TextElement | PictureElement | BoxElement | LineElement | GraphElement;

interface SlideMetadata {
  slideIndex: number;
  backgroundColor: string;
  elements: SlideElement[];
}

interface ThemeParams {
  theme: string;
  customColors?: {
    slideBg: string;
    slideTitle: string;
    slideHeading: string;
    slideDescription: string;
    slideBox: string;
  };
}

export function setupSlideMetadataHandlers() {
  ipcMain.handle("get-slide-metadata", async (_, url: string, theme: string, customColors?: ThemeParams["customColors"]) => {
    let browser;
    try {
      browser = await puppeteer.launch({
        headless: true,
        args: ['--no-sandbox', '--disable-setuid-sandbox']
      });

      const page = await browser.newPage();
      await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });

      await page.goto(url, { waitUntil: "networkidle0", timeout: 60000 });
      await page.waitForSelector('[data-element-type="slide-container"]', { timeout: 80000 });
      
      // Apply theme
      await page.evaluate((params: ThemeParams) => {
        const { theme, customColors } = params;
        document.querySelectorAll(".slide-theme").forEach((container) => {
          container.setAttribute("data-theme", theme);
        });

        if (theme === "custom" && customColors) {
          const root = document.documentElement;
          root.style.setProperty("--custom-slide-bg", customColors.slideBg);
          root.style.setProperty("--custom-slide-title", customColors.slideTitle);
          root.style.setProperty("--custom-slide-heading", customColors.slideHeading);
          root.style.setProperty("--custom-slide-description", customColors.slideDescription);
          root.style.setProperty("--custom-slide-box", customColors.slideBox);
        }
      }, { theme, customColors });

      // Get slide metadata
      const metadata = await page.evaluate(() => {
        const rgbToHex = (color: string): string => {
          if (!color || color === "transparent" || color === "none") return "000000";
          if (color.startsWith("#")) return color.replace("#", "");
          const matches = color.match(/\d+/g);
          if (!matches) return "000000";
          const [r, g, b] = matches.map(x => parseInt(x));
          return [r, g, b].map(x => x.toString(16).padStart(2, "0")).join("");
        };

        const slidesMetadata: SlideMetadata[] = [];
        const slideContainers = document.querySelectorAll('[data-element-type="slide-container"]');

        slideContainers.forEach((container) => {
          const containerEl = container as HTMLElement;
          containerEl.style.width = "1280px";
          containerEl.style.height = "720px";
          containerEl.style.transform = "none";

          const containerRect = containerEl.getBoundingClientRect();
          const slideIndex = parseInt(containerEl.getAttribute("data-slide-index") || "0");
          const backgroundColor = rgbToHex(window.getComputedStyle(containerEl).backgroundColor);

          const elements: SlideElement[] = [];
          const slideElements = containerEl.querySelectorAll('[data-slide-element]:not([data-element-type="slide-container"])');

          slideElements.forEach((element) => {
            const el = element as HTMLElement;
            const elementRect = el.getBoundingClientRect();
            const computedStyle = window.getComputedStyle(el);

            const position: Position = {
              left: Math.round(elementRect.left - containerRect.left),
              top: Math.round(elementRect.top - containerRect.top),
              width: Math.round(elementRect.width),
              height: Math.round(elementRect.height),
            };

            const elementType = el.getAttribute("data-element-type");
            if (!elementType) return;

            switch (elementType) {
              case "text":
                elements.push({
                  position,
                  paragraphs: [{
                    alignment: el.getAttribute("data-is-align") === 'true' ? 2 : 1,
                    text: el.getAttribute("data-text-content") || el.textContent || "",
                    font: {
                      name: computedStyle.fontFamily.split('_')[2] || 'Inter',
                      size: parseInt(computedStyle.fontSize),
                      bold: parseInt(computedStyle.fontWeight) >= 500,
                      weight: parseInt(computedStyle.fontWeight),
                      color: rgbToHex(computedStyle.color),
                    },
                  }],
                } as TextElement);
                break;

              case "picture":
                const imgEl = el.tagName.toLowerCase() === "img" ? el as HTMLImageElement : el.querySelector("img") as HTMLImageElement;
                if (imgEl) {
                  elements.push({
                    position,
                    picture: {
                      is_network: imgEl.src.startsWith("http"),
                      path: imgEl.src || imgEl.getAttribute("data-image-path") || "",
                    },
                    shape: imgEl.getAttribute('data-image-type'),
                    object_fit: {
                      fit: imgEl.getAttribute('data-object-fit'),
                      focus: [
                        parseFloat(imgEl.getAttribute('data-focial-point-x') || '0'),
                        parseFloat(imgEl.getAttribute('data-focial-point-y') || '0'),
                      ],
                    },
                    overlay: el.getAttribute("data-is-icon") ? "ffffff" : null,
                    border_radius: Array(4).fill(parseInt(computedStyle.borderRadius) || 0),
                  } as PictureElement);
                }
                break;

              case "graph":
                elements.push({
                  position,
                  picture: {
                    is_network: true,
                    path: `__GRAPH_PLACEHOLDER__${el.getAttribute("data-element-id")}`,
                  },
                  border_radius: [0, 0, 0, 0],
                } as GraphElement);
                break;
            }
          });

          slidesMetadata.push({ slideIndex, backgroundColor, elements });
        });

        return slidesMetadata;
      });

      // Handle graph elements
      const graphElements = await page.$$('[data-element-type="graph"]');
      for (const graphElement of graphElements) {
        const graphId = await graphElement.evaluate(el => el.getAttribute("data-element-id"));
        const screenshot = await graphElement.screenshot({
          type: "jpeg",
          encoding: "base64",
          quality: 100,
          omitBackground: true,
        });

       
        const filename = `chart-${graphId}-${Date.now()}.jpg`;
        const filePath = path.join(tempDir, filename);

        fs.writeFileSync(filePath, Buffer.from(screenshot, 'base64'));

        metadata.forEach(slide => {
          slide.elements.forEach(element => {
            if ('picture' in element && element.picture.path === `__GRAPH_PLACEHOLDER__${graphId}`) {
              element.picture.path = filePath;
            }
          });
        });
      }

      return metadata;
    } catch (error) {
      console.error("Error during page preparation:", error);
      throw error;
    } finally {
      if (browser) await browser.close();
    }
  });
}
