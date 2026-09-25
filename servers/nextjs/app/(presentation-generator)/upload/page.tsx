import React from "react";

import UploadPage from "./components/UploadPage";
import Header from "@/app/(presentation-generator)/(dashboard)/dashboard/components/Header";
import { Metadata } from "next";
import { GenerationPageFrame } from "../generation-workspace/GenerationPageFrame";

export const metadata: Metadata = {
  title: "DevAX | AI 발표자료 생성기",
  description:
    "Open-source AI presentation generator with custom layouts, multi-model support (OpenAI, Gemini, Ollama), and PDF/PPTX export. A free Gamma alternative.",
  alternates: {
    canonical: "https://presenton.ai/create",
  },
  keywords: [
    "presentation generator",
    "AI presentations",
    "data visualization",
    "automatic presentation maker",
    "professional slides",
    "data-driven presentations",
    "document to presentation",
    "presentation automation",
    "AI-authored presentation tool",
    "business presentations",
  ],
  openGraph: {
    title: "Create Data Presentation | PresentOn",
    description:
      "Open-source AI presentation generator with custom layouts, multi-model support (OpenAI, Gemini, Ollama), and PDF/PPTX export. A free Gamma alternative.",
    type: "website",
    url: "https://presenton.ai/create",
    siteName: "PresentOn",
  },
  twitter: {
    card: "summary_large_image",
    title: "Create Data Presentation | PresentOn",
    description:
      "Open-source AI presentation generator with custom layouts, multi-model support (OpenAI, Gemini, Ollama), and PDF/PPTX export. A free Gamma alternative.",
    site: "@presenton_ai",
    creator: "@presenton_ai",
  },
};

const page = () => {
  return (
    <GenerationPageFrame header={<Header />}>
      <UploadPage />
    </GenerationPageFrame>
  );
};

export default page;
