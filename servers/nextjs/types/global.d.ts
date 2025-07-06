interface ShapeProps {
  id: string;
  type: 'rectangle' | 'circle' | 'line';
  position: { x: number; y: number };
  size: { width: number; height: number };
  // Add other properties as needed
}

interface TextFrameProps {
  id: string;
  content: string;
  position: { x: number; y: number };
  // Add other properties as needed
}

interface LLMConfig {
  LLM?: string;
  LLM_PROVIDER_URL?: string;
  LLM_API_KEY?: string;
  OPENAI_API_KEY?: string;
  GOOGLE_API_KEY?: string;
  PEXELS_API_KEY?: string;
  MODEL?: string;

  // Only used in UI settings
  USE_CUSTOM_URL?: boolean;
}