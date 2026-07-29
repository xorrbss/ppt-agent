

export const FONT_OPTIONS: any[] = [
  { name: 'Inter', displayName: 'Inter', cssUrl: 'https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap' },
  { name: 'DM Sans', displayName: 'DM Sans', cssUrl: 'https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;700&display=swap' },
  { name: 'Overpass', displayName: 'Overpass', cssUrl: 'https://fonts.googleapis.com/css2?family=Overpass:wght@100..900&display=swap' },
  { name: 'Barlow', displayName: 'Barlow', cssUrl: 'https://fonts.googleapis.com/css2?family=Barlow:wght@100..900&display=swap' },
  { name: 'Nunito', displayName: 'Nunito', cssUrl: 'https://fonts.googleapis.com/css2?family=Nunito:wght@200..1000&display=swap' },
  { name: 'Lora', displayName: 'Lora', cssUrl: 'https://fonts.googleapis.com/css2?family=Lora:wght@400;500;600;700&display=swap' },
  { name: 'Instrument Sans', displayName: 'Instrument Sans', cssUrl: 'https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap' },
  { name: 'Roboto Slab', displayName: 'Roboto Slab', cssUrl: 'https://fonts.googleapis.com/css2?family=Roboto+Slab:wght@100..900&display=swap' },
  { name: 'Montserrat', displayName: 'Montserrat', cssUrl: 'https://fonts.googleapis.com/css2?family=Montserrat:wght@100..900&display=swap' },
  { name: 'Libre Baskerville', displayName: 'Libre Baskerville', cssUrl: 'https://fonts.googleapis.com/css2?family=Libre+Baskerville:wght@400;700&display=swap' },
  { name: 'Prompt', displayName: 'Prompt', cssUrl: 'https://fonts.googleapis.com/css2?family=Prompt:wght@100..900&display=swap' },
  { name: 'Inconsolata', displayName: 'Inconsolata', cssUrl: 'https://fonts.googleapis.com/css2?family=Inconsolata:wght@200..900&display=swap' },
  { name: 'Fraunces', displayName: 'Fraunces', cssUrl: 'https://fonts.googleapis.com/css2?family=Fraunces:wght@300..900&display=swap' },
  { name: 'Gelasio', displayName: 'Gelasio', cssUrl: 'https://fonts.googleapis.com/css2?family=Gelasio:wght@300..700&display=swap' },
  { name: 'Raleway', displayName: 'Raleway', cssUrl: 'https://fonts.googleapis.com/css2?family=Raleway:wght@100..900&display=swap' },
  { name: 'Kanit', displayName: 'Kanit', cssUrl: 'https://fonts.googleapis.com/css2?family=Kanit:wght@100..900&display=swap' },
  { name: 'Corben', displayName: 'Corben', cssUrl: 'https://fonts.googleapis.com/css2?family=Corben:wght@400;700&display=swap' },
  { name: 'Poppins', displayName: 'Poppins', cssUrl: 'https://fonts.googleapis.com/css2?family=Poppins:wght@100..900&display=swap' },
  { name: 'Open Sans', displayName: 'Open Sans', cssUrl: 'https://fonts.googleapis.com/css2?family=Open+Sans:wght@300..800&display=swap' },
  { name: 'Lato', displayName: 'Lato', cssUrl: 'https://fonts.googleapis.com/css2?family=Lato:wght@100..900&display=swap' },
  { name: 'Source Sans Pro', displayName: 'Source Sans Pro', cssUrl: 'https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@200..900&display=swap' },
  { name: 'Playfair Display', displayName: 'Playfair Display', cssUrl: 'https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400..900&display=swap' },
  { name: 'Roboto', displayName: 'Roboto', cssUrl: 'https://fonts.googleapis.com/css2?family=Roboto:wght@100..900&display=swap' }
]

export const DEFAULT_THEMES: any[] = [
  {
    id: "edge-yellow",
    name: "Edge Yellow",
    description: "Yellow and dark theme for professionalish and edge.",
    logo: null,
    logo_url: null,
    company_name: null,

    data: {
      colors: {
        primary: "#f5f547",
        background: "#1f1f1f",
        card: "#424242",
        stroke: "#585858",
        primary_text: "#161616",
        background_text: "#f5f547",
        graph_0: "#ffff54",
        graph_1: "#f1f142",
        graph_2: "#dada15",
        graph_3: "#c1bf00",
        graph_4: "#a8a600",
        graph_5: "#908c00",
        graph_6: "#797400",
        graph_7: "#625c00",
        graph_8: "#4d4500",
        graph_9: "#382f00"
      },
      fonts: {
        textFont: {
          name: "Playfair Display",
          url: "https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400..900&display=swap"
        },
        bodyFont: {
          name: "Inter",
          url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap"
        }
      },
      density: "spacious"
    }
  },
  {
    id: "light-rose",
    name: "Light Rose",
    description: "Rose background with punchy font",
    logo: null,
    logo_url: null,
    company_name: null,

    data: {
      colors: {
        "primary": "#030204",
        background: "#f69c9c",
        card: "#ffaeb4",
        stroke: "#bf6a6b",
        primary_text: "#bebebe",
        background_text: "#030202",
        graph_0: "#2f2c32",
        graph_1: "#444147",
        graph_2: "#5a565d",
        graph_3: "#706d73",
        graph_4: "#88848b",
        graph_5: "#a09da4",
        graph_6: "#b9b6bd",
        graph_7: "#d3cfd6",
        graph_8: "#eae6ed",
        graph_9: "#f7f3fb"
      },
      fonts: {
        textFont: {
          name: "Overpass",
          url: "https://fonts.googleapis.com/css2?family=Overpass:wght@100..900&display=swap"
        }
      }
    }
  },
  {
    id: "mint-blue",
    name: "Mint Blue",
    description: "Mint Greent with blue heading.",
    logo: null,
    logo_url: null,
    company_name: null,

    data: {
      colors: {
        primary: "#3b3172",
        background: "#ffffff",
        card: "#80e7cf",
        stroke: "#d1d1d1",
        primary_text: "#ffffff",
        background_text: "#3b3172",
        graph_0: "#003d2d",
        graph_1: "#005341",
        graph_2: "#006a57",
        graph_3: "#00826d",
        graph_4: "#2b9a85",
        graph_5: "#4ab39d",
        graph_6: "#65cdb6",
        graph_7: "#80e7cf",
        graph_8: "#98ffe6",
        graph_9: "#a5fff4"
      },
      fonts: {
        textFont: {
          name: "Prompt",
          url: "https://fonts.googleapis.com/css2?family=Prompt:wght@100..900&display=swap"
        }
      }
    }
  },
  {
    id: "professional-blue",
    name: "Professional Blue",
    description: "Clean and professional blue theme",
    logo: null,
    logo_url: null,
    company_name: null,

    data: {
      colors: {
        primary: "#161616",
        background: "#ffffff",
        card: "#dae6ff",
        stroke: "#d1d1d1",
        primary_text: "#eeeaea",
        background_text: "#000000",
        graph_0: "#2e2e2e",
        graph_1: "#424242",
        graph_2: "#585858",
        graph_3: "#6f6f6f",
        graph_4: "#868686",
        graph_5: "#9e9e9e",
        graph_6: "#b7b7b7",
        graph_7: "#d1d1d1",
        graph_8: "#e8e8e8",
        graph_9: "#f5f5f5"
      },
      fonts: {
        textFont: {
          name: "Inter",
          url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap"
        },
        headingFont: {
          name: "Space Grotesk",
          url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap"
        }
      }
    }
  },
  {
    id: "professional-dark",
    name: "Professional Dark",
    description: "Clean and professional for dark corporate usage.",
    logo: null,
    logo_url: null,
    company_name: null,

    data: {
      colors: {
        primary: "#eff5f1",
        background: "#050505",
        card: "#424242",
        stroke: "#585858",
        primary_text: "#050505",
        background_text: "#eff5f1",
        graph_0: "#ebf6ff",
        graph_1: "#dee8fa",
        graph_2: "#c7d2e3",
        graph_3: "#aeb8c9",
        graph_4: "#959fb0",
        graph_5: "#7d8797",
        graph_6: "#666f7f",
        graph_7: "#505867",
        graph_8: "#3a4351",
        graph_9: "#262e3c"
      },
      fonts: {
        textFont: {
          name: "Instrument Sans",
          url: "https://fonts.googleapis.com/css2?family=Instrument+Sans:ital,wght@0,400..700;1,400..700&display=swap"
        },
        headingFont: {
          name: "Space Grotesk",
          url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap"
        }
      },
      density: "compact"
    }
  },
  // v2 tone-&-manner presets: these exercise the per-theme typography / shape /
  // elevation tokens (deriveThemeTokens) so the adaptive renderer produces
  // genuinely distinct "templates" — no new layout code. Colours+fonts also apply
  // to legacy decks; the typography/shape/elevation keys are adaptive-only.
  {
    id: "carbon",
    name: "Carbon",
    description: "Dark, sharp, flat — a tight technical look (no rounding, no shadows).",
    logo: null,
    logo_url: null,
    company_name: null,
    data: {
      colors: {
        primary: "#1f6feb",
        background: "#0d1117",
        card: "#161b22",
        stroke: "#30363d",
        primary_text: "#ffffff",
        background_text: "#e6edf3",
        graph_0: "#79c0ff",
        graph_1: "#58a6ff",
        graph_2: "#4493f8",
        graph_3: "#3081f0",
        graph_4: "#1f6feb",
        graph_5: "#1158c7",
        graph_6: "#0d419d",
        graph_7: "#0a3069",
        graph_8: "#082145",
        graph_9: "#051026"
      },
      fonts: {
        textFont: { name: "Inter", url: "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap" },
        headingFont: { name: "Space Grotesk", url: "https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300..700&display=swap" }
      },
      density: "compact",
      typography: { scale: 0.98, headingWeight: 700, headingLetterSpacing: "-0.02em" },
      shape: { radiusScale: 0, borderWidth: "1px" },
      elevation: { flat: true }
    }
  },
  {
    id: "pebble",
    name: "Pebble",
    description: "Warm, rounded, softly elevated — an airy, friendly look.",
    logo: null,
    logo_url: null,
    company_name: null,
    data: {
      colors: {
        primary: "#ad5230",
        background: "#faf6f0",
        card: "#ffffff",
        stroke: "#ece3d8",
        primary_text: "#ffffff",
        background_text: "#2f2a26",
        graph_0: "#e8a87c",
        graph_1: "#e0976a",
        graph_2: "#d98559",
        graph_3: "#cf7347",
        graph_4: "#ad5230",
        graph_5: "#a8512e",
        graph_6: "#8f4327",
        graph_7: "#743620",
        graph_8: "#5a2a19",
        graph_9: "#3f1e12"
      },
      fonts: {
        textFont: { name: "Nunito", url: "https://fonts.googleapis.com/css2?family=Nunito:wght@200..1000&display=swap" },
        headingFont: { name: "Poppins", url: "https://fonts.googleapis.com/css2?family=Poppins:wght@100..900&display=swap" }
      },
      density: "spacious",
      typography: { scale: 1.04, headingWeight: 700 },
      shape: { radiusScale: 1.8, borderWidth: "1px" },
      elevation: { shadowSm: "0 2px 6px rgba(0,0,0,0.05)", shadowMd: "0 10px 28px rgba(0,0,0,0.08)", shadowLg: "0 20px 48px rgba(0,0,0,0.10)" }
    }
  },
  {
    id: "broadsheet",
    name: "Broadsheet",
    description: "Editorial serif on paper — large display type, spacious, flat.",
    logo: null,
    logo_url: null,
    company_name: null,
    data: {
      colors: {
        primary: "#1a1a1a",
        background: "#fffdf8",
        card: "#f6f1e7",
        stroke: "#e3dccb",
        primary_text: "#fffdf8",
        background_text: "#1a1a1a",
        graph_0: "#2b2926",
        graph_1: "#454039",
        graph_2: "#5f594f",
        graph_3: "#797166",
        graph_4: "#938b7e",
        graph_5: "#ada595",
        graph_6: "#c7beac",
        graph_7: "#e1d8c4",
        graph_8: "#efe7d6",
        graph_9: "#f7f1e6"
      },
      fonts: {
        textFont: { name: "Source Sans Pro", url: "https://fonts.googleapis.com/css2?family=Source+Sans+Pro:wght@200..900&display=swap" },
        headingFont: { name: "Fraunces", url: "https://fonts.googleapis.com/css2?family=Fraunces:wght@300..900&display=swap" }
      },
      density: "spacious",
      typography: { scale: 1.06, headingWeight: 600, headingLineHeight: "1.1", headingLetterSpacing: "-0.02em" },
      shape: { radiusScale: 0.4 },
      elevation: { flat: true }
    }
  }
]