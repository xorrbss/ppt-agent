"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "@/hooks/use-toast";
import { Info, ExternalLink, PlayCircle } from "lucide-react";
import Link from "next/link";

interface ModelOption {
  value: string;
  label: string;
  description?: string;
}

interface ProviderConfig {
  textModels: ModelOption[];
  imageModels: ModelOption[];
  apiGuide: {
    title: string;
    steps: string[];
    videoUrl?: string;
    docsUrl: string;
  };
}

const PROVIDER_CONFIGS: Record<string, ProviderConfig> = {
  openai: {
    textModels: [
      {
        value: "gpt-4",
        label: "GPT-4",
        description: "Most capable model, best for complex tasks",
      },
    ],
    imageModels: [
      {
        value: "dall-e-3",
        label: "DALL-E 3",
        description: "Latest version with highest quality",
      },
    ],
    apiGuide: {
      title: "How to get your OpenAI API Key",
      steps: [
        "Go to platform.openai.com and sign in or create an account",
        'Click on your profile icon and select "View API keys"',
        'Click "Create new secret key" and give it a name',
        "Copy your API key immediately (you won't be able to see it again)",
        "Make sure you have sufficient credits in your account",
      ],
      videoUrl: "https://www.youtube.com/watch?v=OB99E7Y1cMA",
      docsUrl: "https://platform.openai.com/docs/api-reference/authentication",
    },
  },
  google: {
    textModels: [
      {
        value: "gemini-pro",
        label: "Gemini Pro",
        description: "Balanced model for most tasks",
      },
    ],
    imageModels: [
      {
        value: "imagen",
        label: "Imagen",
        description: "Google's primary image generation model",
      },
    ],
    apiGuide: {
      title: "How to get your Google AI Studio API Key",
      steps: [
        "Visit aistudio.google.com",
        'Click on "Get API key" in the top navigation',
        'Click "Create API key" on the next page',
        'Choose either "Create API Key in new Project" or select an existing project',
        "Copy your API key - you're ready to go!",
      ],
      videoUrl: "https://www.youtube.com/watch?v=o8iyrtQyrZM&t=66s",
      docsUrl: "https://aistudio.google.com/app/apikey",
    },
  },
};

interface ConfigState {
  provider: string;
  apiKey: string;
  textModel: string;
  imageModel: string;
}

export default function Home() {
  const router = useRouter();
  const [config, setConfig] = useState<ConfigState>({
    provider: "openai",
    apiKey: "",
    textModel: PROVIDER_CONFIGS.openai.textModels[0].value,
    imageModel: PROVIDER_CONFIGS.openai.imageModels[0].value,
  });

  const handleProviderChange = (provider: string) => {
    setConfig((prev) => ({
      ...prev,
      provider,
      textModel: PROVIDER_CONFIGS[provider].textModels[0].value,
      imageModel: PROVIDER_CONFIGS[provider].imageModels[0].value,
    }));
  };

  const handleConfigChange = (
    field: keyof ConfigState,
    value: string | number
  ) => {
    setConfig((prev) => ({
      ...prev,
      [field]: value,
    }));
  };

  const currentProvider = PROVIDER_CONFIGS[config.provider];
  const handleSaveConfig = () => {
    console.log("cllee");
    if (!config.apiKey) {
      toast({
        title: "Error",
        description: "Please enter an API key",
      });
      return;
    }
    toast({
      title: "Configuration saved",
      description: "You can now upload your presentation",
    });
    router.push("/upload");
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-gray-50 to-white">
      <main className="container mx-auto px-4 py-12 max-w-3xl">
        {/* Branding Header */}
        <div className="text-center mb-12">
          <div className="flex items-center justify-center gap-3 mb-4">
            <img src="/Logo.png" alt="Presenton Logo" className="" />
          </div>
          <p className="text-gray-600 text-lg">
            Open-source AI presentation generator
          </p>
        </div>

        {/* Main Configuration Card */}
        <div className="bg-white rounded-xl shadow-lg border border-gray-100 p-8">
          {/* Provider Selection */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-gray-700 mb-3">
              Select AI Provider
            </label>
            <div className="grid grid-cols-2 gap-4">
              {Object.keys(PROVIDER_CONFIGS).map((provider) => (
                <button
                  key={provider}
                  onClick={() => handleProviderChange(provider)}
                  className={`relative p-4 rounded-lg border-2 transition-all duration-200 ${
                    config.provider === provider
                      ? "border-blue-500 bg-blue-50"
                      : "border-gray-200 hover:border-blue-200 hover:bg-gray-50"
                  }`}
                >
                  <div className="flex items-center justify-center gap-3">
                    <span
                      className={`font-medium text-center ${
                        config.provider === provider
                          ? "text-blue-700"
                          : "text-gray-700"
                      }`}
                    >
                      {provider.charAt(0).toUpperCase() + provider.slice(1)}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* API Key Input */}
          <div className="mb-8">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              {config.provider.charAt(0).toUpperCase() +
                config.provider.slice(1)}{" "}
              API Key
            </label>
            <div className="relative">
              <input
                type="text"
                value={config.apiKey}
                onChange={(e) => handleConfigChange("apiKey", e.target.value)}
                className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                placeholder="Enter your API key"
              />
            </div>
            <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
              <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
              Your API key will be stored locally and never shared
            </p>
          </div>

          {/* Model Information */}
          <div className="mb-8 p-4 bg-blue-50 rounded-lg border border-blue-100">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-blue-500 mt-0.5" />
              <div>
                <h3 className="text-sm font-medium text-blue-900 mb-1">
                  Selected Models
                </h3>
                <p className="text-sm text-blue-700">
                  Using {currentProvider.textModels[0].label} for text
                  generation and {currentProvider.imageModels[0].label} for
                  images
                </p>
                <p className="text-sm text-blue-600 mt-2 opacity-75">
                  We've pre-selected the best models for optimal presentation
                  generation
                </p>
              </div>
            </div>
          </div>
          {/* API Guide Section */}
          <div className="mb-8 bg-gray-50 rounded-lg p-6 border border-gray-200">
            <div className="flex items-start gap-3 mb-4">
              <Info className="w-5 h-5 text-blue-600 mt-1" />
              <h3 className="text-lg font-medium text-gray-900">
                {currentProvider.apiGuide.title}
              </h3>
            </div>

            <div className="space-y-4">
              <ol className="list-decimal list-inside space-y-2 text-gray-600">
                {currentProvider.apiGuide.steps.map((step, index) => (
                  <li key={index} className="text-sm">
                    {step}
                  </li>
                ))}
              </ol>

              <div className="flex flex-col sm:flex-row gap-4 mt-6">
                {currentProvider.apiGuide.videoUrl && (
                  <Link
                    href={currentProvider.apiGuide.videoUrl}
                    target="_blank"
                    className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 transition-colors"
                  >
                    <PlayCircle className="w-4 h-4" />
                    Watch Video Tutorial
                    <ExternalLink className="w-3 h-3" />
                  </Link>
                )}
                <Link
                  href={currentProvider.apiGuide.docsUrl}
                  target="_blank"
                  className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 transition-colors"
                >
                  <span>Official Documentation</span>
                  <ExternalLink className="w-3 h-3" />
                </Link>
              </div>
            </div>
          </div>

          {/* Save Button */}
          <button
            onClick={handleSaveConfig}
            className="mt-8 w-full font-semibold  bg-gradient-to-r from-blue-600 to-indigo-600 text-white py-3 px-4 rounded-lg hover:from-blue-700 hover:to-indigo-700 focus:ring-4 focus:ring-blue-200 transition-all duration-500"
          >
            Save Configuration
          </button>
        </div>
      </main>
    </div>
  );
}
