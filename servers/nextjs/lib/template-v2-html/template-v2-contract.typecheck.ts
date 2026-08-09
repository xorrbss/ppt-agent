import type {
  Chart,
  Image,
  SlideElement,
  TemplateV2Contract,
} from "./template-v2-contract.generated";


const chart: Chart = {
  chart_type: "bar",
  decorative: false,
  name: "revenue",
  type: "chart",
};

const image: Image = {
  data: "data:image/png;base64,AA==",
  decorative: true,
  is_icon: false,
  name: "background",
  type: "image",
};

const element: SlideElement = chart;
const contract: TemplateV2Contract = {
  layouts: [
    {
      components: [
        {
          description: "A component with generated contract types.",
          elements: [element, image],
          id: "hero",
          position: { x: 0, y: 0 },
        },
      ],
      description: "A layout with generated contract types.",
      id: "title-slide",
    },
  ],
};

// @ts-expect-error Pydantic requires the chart discriminator.
const chartWithoutType: Chart = {
  chart_type: "bar",
  decorative: false,
  name: "invalid",
};

// @ts-expect-error Pydantic requires image data.
const imageWithoutData: Image = {
  decorative: false,
  is_icon: false,
  name: "invalid",
  type: "image",
};

// @ts-expect-error Unknown discriminators are excluded from SlideElement.
const unknownElement: SlideElement = { type: "unknown" };

export const TEMPLATE_V2_GENERATED_TYPECHECK = {
  chartWithoutType,
  contract,
  imageWithoutData,
  unknownElement,
};
