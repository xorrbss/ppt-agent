"use client";
import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { toast } from "@/hooks/use-toast";
import { Info, ExternalLink, PlayCircle, Loader2, Check, ChevronsUpDown } from "lucide-react";
import Link from "next/link";
import {
    Accordion,
    AccordionContent,
    AccordionItem,
    AccordionTrigger,
} from "@/components/ui/accordion";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { handleSaveLLMConfig } from "@/utils/storeHelpers";
import { Select, SelectContent, SelectItem, SelectTrigger } from "./ui/select";
import { Button } from "./ui/button";
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "./ui/command";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "./ui/popover";
import { cn } from "@/lib/utils";
import { Switch } from "./ui/switch";
import { setLLMConfig } from "@/store/slices/userConfig";

interface ModelOption {
    value: string;
    label: string;
    description?: string;
    icon?: string;
    size: string;
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
                icon: "/icons/openai.png",
                size: "8GB",
            },
        ],
        imageModels: [
            {
                value: "dall-e-3",
                label: "DALL-E 3",
                description: "Latest version with highest quality",
                icon: "/icons/dall-e.png",
                size: "8GB",
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
                icon: "/icons/google.png",
                size: "8GB",
            },
        ],
        imageModels: [
            {
                value: "imagen",
                label: "Imagen",
                description: "Google's primary image generation model",
                icon: "/icons/google.png",
                size: "8GB",
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
    ollama: {
        textModels: [],
        imageModels: [
            {
                value: "pexels",
                label: "Pexels",
                description: "Pexels is a free stock photo and video platform that allows you to download high-quality images and videos for free.",
                icon: "/icons/pexels.png",
                size: "8GB",
            },
        ],
        apiGuide: {
            title: "How to get your Pexels API Key",
            steps: [
                "Visit pexels.com",
                'Click on "Get API key" in the top navigation',
                "Copy your API key - you're ready to go!",
            ],
            videoUrl: "https://www.youtube.com/watch?v=o8iyrtQyrZM&t=66s",
            docsUrl: "https://www.pexels.com/api/documentation/",
        },
    },
    custom: {
        textModels: [],
        imageModels: [
            {
                value: "pexels",
                label: "Pexels",
                description: "Pexels is a free stock photo and video platform that allows you to download high-quality images and videos for free.",
                icon: "/icons/pexels.png",
                size: "8GB",
            },
        ],
        apiGuide: {
            title: "How to get your Pexels API Key",
            steps: [
                "Visit pexels.com",
                'Click on "Get API key" in the top navigation',
                "Copy your API key - you're ready to go!",
            ],
            videoUrl: "https://www.youtube.com/watch?v=o8iyrtQyrZM&t=66s",
            docsUrl: "https://www.pexels.com/api/documentation/",
        },
    },
};

export default function Home() {
    const router = useRouter();
    const config = useSelector((state: RootState) => state.userConfig);
    const [llmConfig, setLlmConfig] = useState(config.llm_config);
    const [ollamaModels, setOllamaModels] = useState<{
        label: string;
        value: string;
        description: string;
        size: string;
        icon: string;
    }[]>([]);
    const [customModels, setCustomModels] = useState<string[]>([]);
    const [downloadingModel, setDownloadingModel] = useState({
        name: '',
        size: null,
        downloaded: null,
        status: '',
        done: false,
    });
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [openModelSelect, setOpenModelSelect] = useState(false);
    const [useCustomOllamaUrl, setUseCustomOllamaUrl] = useState<boolean>(llmConfig.USE_CUSTOM_URL || false);
    const [customModelsLoading, setCustomModelsLoading] = useState<boolean>(false);
    const [customModelsChecked, setCustomModelsChecked] = useState<boolean>(false);

    const canChangeKeys = config.can_change_keys;

    const input_field_changed = (new_value: string, field: string) => {
        if (field === 'openai_api_key') {
            setLlmConfig({ ...llmConfig, OPENAI_API_KEY: new_value });
        } else if (field === 'google_api_key') {
            setLlmConfig({ ...llmConfig, GOOGLE_API_KEY: new_value });
        } else if (field === 'ollama_url') {
            setLlmConfig({ ...llmConfig, OLLAMA_URL: new_value });
        } else if (field === 'ollama_model') {
            setLlmConfig({ ...llmConfig, OLLAMA_MODEL: new_value });
        } else if (field === 'custom_llm_url') {
            setLlmConfig({ ...llmConfig, CUSTOM_LLM_URL: new_value });
        } else if (field === 'custom_llm_api_key') {
            setLlmConfig({ ...llmConfig, CUSTOM_LLM_API_KEY: new_value });
        } else if (field === 'custom_model') {
            setLlmConfig({ ...llmConfig, CUSTOM_MODEL: new_value });
        } else if (field === 'pexels_api_key') {
            setLlmConfig({ ...llmConfig, PEXELS_API_KEY: new_value });
        }
    }

    const handleSaveConfig = async () => {
        try {
            await handleSaveLLMConfig(llmConfig);
            if (llmConfig.LLM === 'ollama') {
                setIsLoading(true);
                await pullOllamaModels();
            }
            toast({
                title: 'Success',
                description: 'Configuration saved successfully',
            });
            setIsLoading(false);
            router.push("/upload");
        } catch (error) {
            console.error('Error:', error);
            toast({
                title: 'Error',
                description: error instanceof Error ? error.message : 'Failed to save configuration',
                variant: 'destructive',
            });
            setIsLoading(false);
        }
    };

    const fetchOllamaModelsWithConfig = async (config: any) => {
        try {
            const response = await fetch('/api/v1/ppt/ollama/list-supported-models');
            const data = await response.json();
            setOllamaModels(data.models);

            // Check if currently selected model is still available
            if (config.OLLAMA_MODEL && data.models.length > 0) {
                const isModelAvailable = data.models.some((model: any) => model.value === config.OLLAMA_MODEL);
                if (!isModelAvailable) {
                    setLlmConfig({ ...config, OLLAMA_MODEL: '' });
                }
            }
        } catch (error) {
            console.error('Error fetching ollama models:', error);
        }
    }

    const changeProvider = (provider: string) => {
        const newConfig = { ...llmConfig, LLM: provider };
        setLlmConfig(newConfig);
        if (provider === 'ollama') {
            // Use the new config to avoid stale state issues
            fetchOllamaModelsWithConfig(newConfig);
        }
    }

    const resetDownloadingModel = () => {
        setDownloadingModel({
            name: '',
            size: null,
            downloaded: null,
            status: '',
            done: false,
        });
    }

    const pullOllamaModels = async (): Promise<void> => {
        return new Promise((resolve, reject) => {
            const interval = setInterval(async () => {
                try {
                    const response = await fetch(`/api/v1/ppt/ollama/pull-model?name=${llmConfig.OLLAMA_MODEL}`);
                    if (response.status === 200) {
                        const data = await response.json();
                        if (data.done && data.status !== 'error') {
                            clearInterval(interval);
                            setDownloadingModel(data);
                            resolve();
                        } else if (data.status === 'error') {
                            clearInterval(interval);
                            resetDownloadingModel();
                            reject(new Error('Error occurred while pulling model'));
                        } else {
                            setDownloadingModel(data);
                        }
                    } else {
                        clearInterval(interval);
                        resetDownloadingModel();
                        if (response.status === 403) {
                            reject(new Error('Request to Ollama Not Authorized'));
                        }
                        reject(new Error('Error occurred while pulling model'));
                    }
                } catch (error) {
                    clearInterval(interval);
                    resetDownloadingModel();
                    reject(error);
                }
            }, 1000);
        });
    }

    const fetchOllamaModels = async () => {
        await fetchOllamaModelsWithConfig(llmConfig);
    }

    const fetchCustomModels = async () => {
        try {
            setCustomModelsLoading(true);
            const response = await fetch('/api/v1/ppt/models/list/custom', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    url: llmConfig.CUSTOM_LLM_URL || '',
                    api_key: llmConfig.CUSTOM_LLM_API_KEY || ''
                })
            });
            const data = await response.json();
            setCustomModels(data);
            setCustomModelsChecked(true);
        } catch (error) {
            console.error('Error fetching custom models:', error);
            toast({
                title: 'Error',
                description: 'Failed to fetch available models. Please check your URL and API key.',
                variant: 'destructive',
            });
        } finally {
            setCustomModelsLoading(false);
        }
    }

    const setOllamaConfig = () => {
        if (!useCustomOllamaUrl) {
            setLlmConfig({ ...llmConfig, OLLAMA_URL: 'http://localhost:11434', USE_CUSTOM_URL: false });
        } else {
            setLlmConfig({ ...llmConfig, USE_CUSTOM_URL: true });
        }
    }

    useEffect(() => {
        if (!canChangeKeys) {
            router.push("/upload");
        }
        if (llmConfig.LLM === 'ollama') {
            fetchOllamaModels();
        }
    }, []);

    useEffect(() => {
        setOllamaConfig();
    }, [useCustomOllamaUrl]);

    // Reset custom models when URL or API key changes
    useEffect(() => {
        if (llmConfig.LLM === 'custom') {
            setCustomModels([]);
            setCustomModelsChecked(false);
            setLlmConfig({ ...llmConfig, CUSTOM_MODEL: '' });
        }
    }, [llmConfig.CUSTOM_LLM_URL, llmConfig.CUSTOM_LLM_API_KEY]);

    if (!canChangeKeys) {
        return null;
    }

    return (
        <div className="min-h-screen bg-gradient-to-b font-instrument_sans from-gray-50 to-white">
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
                                    onClick={() => changeProvider(provider)}
                                    className={`relative p-4 rounded-lg border-2 transition-all duration-200 ${llmConfig.LLM === provider
                                        ? "border-blue-500 bg-blue-50"
                                        : "border-gray-200 hover:border-blue-200 hover:bg-gray-50"
                                        }`}
                                >
                                    <div className="flex items-center justify-center gap-3">
                                        <span
                                            className={`font-medium text-center ${llmConfig.LLM === provider
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
                    {llmConfig.LLM !== 'ollama' && llmConfig.LLM !== 'custom' && <div className="mb-8">
                        <label className="block text-sm font-medium text-gray-700 mb-2">
                            {llmConfig.LLM!.charAt(0).toUpperCase() +
                                llmConfig.LLM!.slice(1)}{" "}
                            API Key
                        </label>
                        <div className="relative">
                            <input
                                type="text"
                                value={llmConfig.LLM === 'openai' ? llmConfig.OPENAI_API_KEY || '' : llmConfig.GOOGLE_API_KEY || ''}
                                onChange={(e) => input_field_changed(e.target.value, llmConfig.LLM === 'openai' ? 'openai_api_key' : 'google_api_key')}
                                className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                placeholder="Enter your API key"
                            />
                        </div>
                        <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                            <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                            Your API key will be stored locally and never shared
                        </p>
                    </div>}
                    {
                        llmConfig.LLM === 'ollama' && (<div>
                            <div className="mb-8">
                                <label className="block text-sm font-medium text-gray-700 mb-3">
                                    Choose a supported model
                                </label>
                                <div className="w-full">
                                    {ollamaModels.length > 0 ? (
                                        <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
                                            <PopoverTrigger asChild>
                                                <Button
                                                    variant="outline"
                                                    role="combobox"
                                                    aria-expanded={openModelSelect}
                                                    className="w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
                                                >
                                                    <div className="flex gap-3 items-center">
                                                        {llmConfig.OLLAMA_MODEL && (
                                                            <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0">
                                                                <img
                                                                    src={ollamaModels.find(m => m.value === llmConfig.OLLAMA_MODEL)?.icon}
                                                                    alt={`${llmConfig.OLLAMA_MODEL} icon`}
                                                                    className="rounded-sm"
                                                                />
                                                            </div>
                                                        )}
                                                        <span className="text-sm font-medium text-gray-900">
                                                            {llmConfig.OLLAMA_MODEL ? (
                                                                ollamaModels.find(m => m.value === llmConfig.OLLAMA_MODEL)?.label || llmConfig.OLLAMA_MODEL
                                                            ) : (
                                                                'Select a model'
                                                            )}
                                                        </span>
                                                        {llmConfig.OLLAMA_MODEL && (
                                                            <span className="text-xs text-gray-500 bg-gray-100 rounded-full px-2 py-1">
                                                                {ollamaModels.find(m => m.value === llmConfig.OLLAMA_MODEL)?.size}
                                                            </span>
                                                        )}
                                                    </div>
                                                    <ChevronsUpDown className="w-4 h-4 text-gray-500" />
                                                </Button>
                                            </PopoverTrigger>
                                            <PopoverContent className="p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
                                                <Command>
                                                    <CommandInput placeholder="Search model..." />
                                                    <CommandList>
                                                        <CommandEmpty>No model found.</CommandEmpty>
                                                        <CommandGroup>
                                                            {ollamaModels.map((model, index) => (
                                                                <CommandItem
                                                                    key={index}
                                                                    value={model.value}
                                                                    onSelect={(value) => {
                                                                        input_field_changed(value, 'ollama_model');
                                                                        setOpenModelSelect(false);
                                                                    }}
                                                                >
                                                                    <Check
                                                                        className={cn(
                                                                            "mr-2 h-4 w-4",
                                                                            llmConfig.OLLAMA_MODEL === model.value ? "opacity-100" : "opacity-0"
                                                                        )}
                                                                    />
                                                                    <div className="flex gap-3 items-center">
                                                                        <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0">
                                                                            <img
                                                                                src={model.icon}
                                                                                alt={`${model.label} icon`}
                                                                                className="rounded-sm"
                                                                            />
                                                                        </div>
                                                                        <div className="flex flex-col space-y-1 flex-1">
                                                                            <div className="flex items-center justify-between gap-2">
                                                                                <span className="text-sm font-medium text-gray-900 capitalize">
                                                                                    {model.label}
                                                                                </span>
                                                                                <span className="text-xs text-gray-500 bg-gray-100 px-2 py-1 rounded-full">
                                                                                    {model.size}
                                                                                </span>
                                                                            </div>
                                                                            <span className="text-xs text-gray-600 leading-relaxed">
                                                                                {model.description}
                                                                            </span>
                                                                        </div>
                                                                    </div>
                                                                </CommandItem>
                                                            ))}
                                                        </CommandGroup>
                                                    </CommandList>
                                                </Command>
                                            </PopoverContent>
                                        </Popover>
                                    ) : (
                                        <div className="w-full border border-gray-300 rounded-lg p-4">
                                            <div className="flex items-center space-x-3">
                                                <div className="w-4 h-4 bg-gray-200 rounded-full animate-pulse"></div>
                                                <div className="flex-1 space-y-2">
                                                    <div className="h-4 bg-gray-200 rounded animate-pulse"></div>
                                                    <div className="h-3 bg-gray-200 rounded w-3/4 animate-pulse"></div>
                                                </div>
                                            </div>
                                        </div>
                                    )}
                                </div>
                                {ollamaModels.length === 0 && (
                                    <p className="mt-2 text-sm text-gray-500">
                                        Loading available models...
                                    </p>
                                )}
                            </div>
                            <div className="mb-8">
                                <div className="flex items-center justify-between mb-4 bg-green-50 p-2 rounded-sm">
                                    <label className="text-sm font-medium text-gray-700">
                                        Use custom Ollama URL
                                    </label>
                                    <Switch
                                        checked={useCustomOllamaUrl}
                                        onCheckedChange={setUseCustomOllamaUrl}
                                    />
                                </div>
                                {useCustomOllamaUrl && (
                                    <>
                                        <div className="mb-4">
                                            <label className="block text-sm font-medium text-gray-700 mb-2">
                                                Ollama URL
                                            </label>
                                            <div className="relative">
                                                <input
                                                    type="text"
                                                    required
                                                    placeholder="Enter your Ollama URL"
                                                    className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                                    value={llmConfig.OLLAMA_URL || ''}
                                                    onChange={(e) => input_field_changed(e.target.value, 'ollama_url')}
                                                />
                                            </div>
                                            <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                                                <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                                                Change this if you are using a custom Ollama instance
                                            </p>
                                        </div>
                                    </>
                                )}
                            </div>
                            <div className="mb-8">
                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                    Pexels API Key (optional)
                                </label>
                                <div className="relative">
                                    <input
                                        type="text"
                                        required
                                        placeholder="Enter your Pexels API key"
                                        className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                        value={llmConfig.PEXELS_API_KEY || ''}
                                        onChange={(e) => input_field_changed(e.target.value, 'pexels_api_key')}
                                    />
                                </div>
                                <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                                    <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                                    Provide a Pexels API key to generate presentation images
                                </p>
                            </div>
                        </div>)
                    }
                    {
                        llmConfig.LLM === 'custom' && (
                            <>
                                <div className="mb-4">
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        OpenAI Compatible URL
                                    </label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            required
                                            placeholder="Enter your URL"
                                            className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            value={llmConfig.CUSTOM_LLM_URL || ''}
                                            onChange={(e) => input_field_changed(e.target.value, 'custom_llm_url')}
                                        />
                                    </div>
                                </div>
                                <div className="mb-4">
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        OpenAI Compatible API Key
                                    </label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            required
                                            placeholder="Enter your API Key"
                                            className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            value={llmConfig.CUSTOM_LLM_API_KEY || ''}
                                            onChange={(e) => input_field_changed(e.target.value, 'custom_llm_api_key')}
                                        />
                                    </div>
                                </div>

                                {/* Model selection dropdown - only show if models are available */}
                                {customModelsChecked && customModels.length > 0 && (
                                    <div className="mb-4">
                                        <label className="block text-sm font-medium text-gray-700 mb-2">
                                            Select Model
                                        </label>
                                        <div className="w-full">
                                            <Popover open={openModelSelect} onOpenChange={setOpenModelSelect}>
                                                <PopoverTrigger asChild>
                                                    <Button
                                                        variant="outline"
                                                        role="combobox"
                                                        aria-expanded={openModelSelect}
                                                        className="w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
                                                    >
                                                        <span className="text-sm font-medium text-gray-900">
                                                            {llmConfig.CUSTOM_MODEL || 'Select a model'}
                                                        </span>
                                                        <ChevronsUpDown className="w-4 h-4 text-gray-500" />
                                                    </Button>
                                                </PopoverTrigger>
                                                <PopoverContent className="p-0" align="start" style={{ width: 'var(--radix-popover-trigger-width)' }}>
                                                    <Command>
                                                        <CommandInput placeholder="Search model..." />
                                                        <CommandList>
                                                            <CommandEmpty>No model found.</CommandEmpty>
                                                            <CommandGroup>
                                                                {customModels.map((model, index) => (
                                                                    <CommandItem
                                                                        key={index}
                                                                        value={model}
                                                                        onSelect={(value) => {
                                                                            input_field_changed(value, 'custom_model');
                                                                            setOpenModelSelect(false);
                                                                        }}
                                                                    >
                                                                        <Check
                                                                            className={cn(
                                                                                "mr-2 h-4 w-4",
                                                                                llmConfig.CUSTOM_MODEL === model ? "opacity-100" : "opacity-0"
                                                                            )}
                                                                        />
                                                                        <span className="text-sm font-medium text-gray-900">
                                                                            {model}
                                                                        </span>
                                                                    </CommandItem>
                                                                ))}
                                                            </CommandGroup>
                                                        </CommandList>
                                                    </Command>
                                                </PopoverContent>
                                            </Popover>
                                        </div>
                                    </div>
                                )}

                                {/* Check for available models button - show when no models checked or no models found */}
                                {(!customModelsChecked || (customModelsChecked && customModels.length === 0)) && (
                                    <div className="mb-4">
                                        <button
                                            onClick={fetchCustomModels}
                                            disabled={customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY}
                                            className={`w-full py-2.5 px-4 rounded-lg transition-all duration-200 border-2 ${customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY
                                                ? 'bg-gray-100 border-gray-300 cursor-not-allowed text-gray-500'
                                                : 'bg-white border-blue-600 text-blue-600 hover:bg-blue-50 focus:ring-2 focus:ring-blue-500/20'
                                                }`}
                                        >
                                            {customModelsLoading ? (
                                                <div className="flex items-center justify-center gap-2">
                                                    <Loader2 className="w-4 h-4 animate-spin" />
                                                    Checking for models...
                                                </div>
                                            ) : (
                                                'Check for available models'
                                            )}
                                        </button>
                                    </div>
                                )}

                                {/* Show message if no models found */}
                                {customModelsChecked && customModels.length === 0 && (
                                    <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                                        <p className="text-sm text-yellow-800">
                                            No models found. Please check your URL and API key, or try again.
                                        </p>
                                    </div>
                                )}

                                <div className="mb-8">
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        Pexels API Key (optional)
                                    </label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            required
                                            placeholder="Enter your Pexels API key"
                                            className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            value={llmConfig.PEXELS_API_KEY || ''}
                                            onChange={(e) => input_field_changed(e.target.value, 'pexels_api_key')}
                                        />
                                    </div>
                                    <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                                        <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                                        Provide a Pexels API key to generate presentation images
                                    </p>
                                </div>
                            </>
                        )
                    }

                    {/* Model Information */}
                    <div className="mb-8 p-4 bg-blue-50 rounded-lg border border-blue-100">
                        <div className="flex items-start gap-3">
                            <Info className="w-5 h-5 text-blue-500 mt-0.5" />
                            <div>
                                <h3 className="text-sm font-medium text-blue-900 mb-1">
                                    Selected Models
                                </h3>
                                <p className="text-sm text-blue-700">
                                    Using {llmConfig.LLM === 'ollama' ? llmConfig.OLLAMA_MODEL ?? '_____' : llmConfig.LLM === 'custom' ? llmConfig.CUSTOM_MODEL ?? '_____' : PROVIDER_CONFIGS[llmConfig.LLM!].textModels[0].label} for text
                                    generation and {PROVIDER_CONFIGS[llmConfig.LLM!].imageModels[0].label} for
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
                    <Accordion type="single" collapsible className="mb-8 bg-gray-50 rounded-lg border border-gray-200">
                        <AccordionItem value="guide" className="border-none">
                            <AccordionTrigger className="px-6 py-4 hover:no-underline">
                                <div className="flex items-start gap-3">
                                    <Info className="w-5 h-5 text-blue-600 mt-1" />
                                    <h3 className="text-lg font-medium text-gray-900">
                                        {PROVIDER_CONFIGS[llmConfig.LLM!].apiGuide.title}
                                    </h3>
                                </div>
                            </AccordionTrigger>
                            <AccordionContent className="px-6 pb-6">
                                <div className="space-y-4">
                                    <ol className="list-decimal list-inside space-y-2 text-gray-600">
                                        {PROVIDER_CONFIGS[llmConfig.LLM!].apiGuide.steps.map((step, index) => (
                                            <li key={index} className="text-sm">
                                                {step}
                                            </li>
                                        ))}
                                    </ol>

                                    <div className="flex flex-col sm:flex-row gap-4 mt-6">
                                        {PROVIDER_CONFIGS[llmConfig.LLM!].apiGuide.videoUrl && (
                                            <Link
                                                href={PROVIDER_CONFIGS[llmConfig.LLM!].apiGuide.videoUrl!}
                                                target="_blank"
                                                className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 transition-colors"
                                            >
                                                <PlayCircle className="w-4 h-4" />
                                                Watch Video Tutorial
                                                <ExternalLink className="w-3 h-3" />
                                            </Link>
                                        )}
                                        <Link
                                            href={PROVIDER_CONFIGS[llmConfig.LLM!].apiGuide.docsUrl}
                                            target="_blank"
                                            className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-700 transition-colors"
                                        >
                                            <span>Official Documentation</span>
                                            <ExternalLink className="w-3 h-3" />
                                        </Link>
                                    </div>
                                </div>
                            </AccordionContent>
                        </AccordionItem>
                    </Accordion>

                    {/* Save Button */}
                    <button
                        onClick={handleSaveConfig}
                        disabled={isLoading || (llmConfig.LLM === 'ollama' && !llmConfig.OLLAMA_MODEL) || (llmConfig.LLM === 'custom' && !llmConfig.CUSTOM_MODEL)}
                        className={`mt-8 w-full font-semibold py-3 px-4 rounded-lg transition-all duration-500 ${isLoading || (llmConfig.LLM === 'ollama' && !llmConfig.OLLAMA_MODEL) || (llmConfig.LLM === 'custom' && !llmConfig.CUSTOM_MODEL)
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 focus:ring-4 focus:ring-blue-200'
                            } text-white`}
                    >
                        {isLoading ? (
                            <div className="flex items-center justify-center gap-2">
                                <Loader2 className="w-4 h-4 animate-spin" />
                                {llmConfig.LLM === 'ollama' && downloadingModel.downloaded || 0 > 0
                                    ? `Downloading Model (${(((downloadingModel.downloaded || 0) / (downloadingModel.size || 1)) * 100).toFixed(0)}%)`
                                    : 'Saving Configuration...'
                                }
                            </div>
                        ) : (
                            (llmConfig.LLM === 'ollama' && !llmConfig.OLLAMA_MODEL) || (llmConfig.LLM === 'custom' && !llmConfig.CUSTOM_MODEL)
                                ? 'Please Select a Model'
                                : 'Save Configuration'
                        )}
                    </button>

                    {
                        llmConfig.LLM === 'ollama' && downloadingModel.status && downloadingModel.status !== 'pulled' && (
                            <div className="mt-3 text-sm bg-green-100 rounded-lg p-2 font-semibold capitalize text-center text-gray-600">
                                {downloadingModel.status}
                            </div>
                        )
                    }
                </div>
            </main>
        </div>
    );
}
