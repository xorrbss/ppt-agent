'use client';
import React, { useState, useEffect } from "react";
import Header from "../dashboard/components/Header";
import Wrapper from "@/components/Wrapper";
import { Settings, Key, Loader2, Check, ChevronsUpDown } from 'lucide-react';
import { toast } from '@/hooks/use-toast';
import { RootState } from "@/store/store";
import { useSelector } from "react-redux";
import { handleSaveLLMConfig } from "@/utils/storeHelpers";
import { useRouter } from "next/navigation";
import { Select, SelectContent, SelectItem, SelectTrigger } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import {
    Command,
    CommandEmpty,
    CommandGroup,
    CommandInput,
    CommandItem,
    CommandList,
} from "@/components/ui/command";
import {
    Popover,
    PopoverContent,
    PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { Switch } from "@/components/ui/switch";

const PROVIDER_CONFIGS: Record<string, ProviderConfig> = {
    openai: {
        title: "OpenAI API Key",
        description: "Required for using OpenAI services",
        placeholder: "Enter your OpenAI API key",
    },
    google: {
        title: "Google API Key",
        description: "Required for using Google services",
        placeholder: "Enter your Google API key",
    },
    ollama: {
        title: "Ollama API Key",
        description: "Required for using Ollama services",
        placeholder: "Choose a model",
    }
};

interface ProviderConfig {
    title: string;
    description: string;
    placeholder: string;
}

const SettingsPage = () => {
    const router = useRouter();

    const userConfigState = useSelector((state: RootState) => state.userConfig);
    const [llmConfig, setLlmConfig] = useState(userConfigState.llm_config);
    const canChangeKeys = userConfigState.can_change_keys;
    const [ollamaModels, setOllamaModels] = useState<{
        label: string;
        value: string;
        description: string;
        size: string;
        icon: string;
    }[]>([]);
    const [downloadingModel, setDownloadingModel] = useState({
        name: '',
        size: null,
        downloaded: null,
        status: '',
        done: false,
    });
    const [isLoading, setIsLoading] = useState<boolean>(false);
    const [openModelSelect, setOpenModelSelect] = useState(false);
    const [useCustomOllamaUrl, setUseCustomOllamaUrl] = useState<boolean>(userConfigState.llm_config.USE_CUSTOM_URL || false);

    const api_key_changed = (apiKey: string, field?: string) => {
        if (llmConfig.LLM === 'openai') {
            setLlmConfig({ ...llmConfig, OPENAI_API_KEY: apiKey });
        } else if (llmConfig.LLM === 'google') {
            setLlmConfig({ ...llmConfig, GOOGLE_API_KEY: apiKey });
        } else if (llmConfig.LLM === 'ollama') {
            if (field === 'pexels') {
                setLlmConfig({ ...llmConfig, PEXELS_API_KEY: apiKey });
            } else if (field === 'ollama_url') {
                setLlmConfig({ ...llmConfig, LLM_PROVIDER_URL: apiKey });
            } else if (field === 'ollama_api_key') {
                setLlmConfig({ ...llmConfig, LLM_API_KEY: apiKey });
            }
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
            router.back();
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

    const changeProvider = (provider: string) => {
        setLlmConfig({ ...llmConfig, LLM: provider });
        if (provider === 'ollama') {
            fetchOllamaModels();
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
                    const response = await fetch(`/api/v1/ppt/ollama/pull-model?name=${llmConfig.MODEL}`);
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
        try {
            const response = await fetch('/api/v1/ppt/ollama/list-supported-models');
            const data = await response.json();
            setOllamaModels(data.models);
        } catch (error) {
            console.error('Error fetching ollama models:', error);
        }
    }

    const setOllamaConfig = () => {
        if (!useCustomOllamaUrl) {
            setLlmConfig({ ...llmConfig, LLM_PROVIDER_URL: 'http://localhost:11434', LLM_API_KEY: undefined, USE_CUSTOM_URL: false });
        } else {
            setLlmConfig({ ...llmConfig, USE_CUSTOM_URL: true });
        }
    }

    useEffect(() => {

        if (!canChangeKeys) {
            router.push("/dashboard");
        }
        if (userConfigState.llm_config.LLM === 'ollama') {
            fetchOllamaModels();
        }
    }, [userConfigState.llm_config.LLM]);

    useEffect(() => {
        setOllamaConfig();
    }, [useCustomOllamaUrl]);

    if (!canChangeKeys) {
        return null;
    }

    return (
        <div className="min-h-screen bg-[#E9E8F8] font-instrument_sans">
            <Header />
            <Wrapper className="lg:w-[80%]">
                <div className="py-8 space-y-6">
                    {/* Settings Header */}
                    <div className="flex items-center gap-3 mb-6">
                        <Settings className="w-8 h-8 text-blue-600" />
                        <h1 className="text-2xl font-semibold text-gray-900">Settings</h1>
                    </div>

                    {/* API Configuration Section */}
                    <div className="bg-white rounded-xl shadow-sm p-6">
                        <div className="flex items-center gap-3 mb-6">
                            <Key className="w-5 h-5 text-blue-600" />
                            <h2 className="text-lg font-medium text-gray-900">API Configuration</h2>
                        </div>

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
                        {llmConfig.LLM !== 'ollama' && (
                            <div className="space-y-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        {PROVIDER_CONFIGS[llmConfig.LLM!].title}
                                    </label>
                                    <div className="flex gap-3">
                                        <input
                                            type="text"
                                            value={llmConfig.LLM === 'openai' ? llmConfig.OPENAI_API_KEY || '' : llmConfig.GOOGLE_API_KEY || ''}
                                            onChange={(e) => api_key_changed(e.target.value)}
                                            className="flex-1 px-4 py-2.5 border border-gray-300 outline-none rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            placeholder={PROVIDER_CONFIGS[llmConfig.LLM!].placeholder}
                                        />
                                        <button
                                            onClick={handleSaveConfig}
                                            disabled={isLoading}
                                            className={`px-4 py-2 rounded-lg transition-colors ${isLoading
                                                ? 'bg-gray-400 cursor-not-allowed'
                                                : 'bg-blue-600 hover:bg-blue-700'
                                                } text-white`}
                                        >
                                            {isLoading ? (
                                                <div className="flex items-center gap-2">
                                                    <Loader2 className="w-4 h-4 animate-spin" />
                                                    Saving...
                                                </div>
                                            ) : (
                                                'Save'
                                            )}
                                        </button>
                                    </div>
                                    <p className="mt-2 text-sm text-gray-500">{PROVIDER_CONFIGS[llmConfig.LLM!].description}</p>
                                </div>
                            </div>
                        )}

                        {/* Ollama Configuration */}
                        {llmConfig.LLM === 'ollama' && (
                            <div className="space-y-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
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
                                                            {llmConfig.MODEL && (
                                                                <div className="w-6 h-6 rounded-lg flex items-center justify-center flex-shrink-0">
                                                                    <img
                                                                        src={ollamaModels.find(m => m.value === llmConfig.MODEL)?.icon}
                                                                        alt={`${llmConfig.MODEL} icon`}
                                                                        className="rounded-sm"
                                                                    />
                                                                </div>
                                                            )}
                                                            <span className="text-sm font-medium text-gray-900">
                                                                {llmConfig.MODEL ? (
                                                                    ollamaModels.find(m => m.value === llmConfig.MODEL)?.label || llmConfig.MODEL
                                                                ) : (
                                                                    'Select a model'
                                                                )}
                                                            </span>
                                                            {llmConfig.MODEL && (
                                                                <span className="text-xs text-gray-500 bg-gray-100 rounded-full px-2 py-1">
                                                                    {ollamaModels.find(m => m.value === llmConfig.MODEL)?.size}
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
                                                                            setLlmConfig({ ...llmConfig, MODEL: value });
                                                                            setOpenModelSelect(false);
                                                                        }}
                                                                    >
                                                                        <Check
                                                                            className={cn(
                                                                                "mr-2 h-4 w-4",
                                                                                llmConfig.MODEL === model.value ? "opacity-100" : "opacity-0"
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

                                {/* Custom Ollama URL Configuration */}
                                <div>
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
                                                        value={llmConfig.LLM_PROVIDER_URL || ''}
                                                        onChange={(e) => api_key_changed(e.target.value, 'ollama_url')}
                                                    />
                                                </div>
                                                <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                                                    <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                                                    Change this if you are using a custom Ollama instance
                                                </p>
                                            </div>
                                            <div className="mb-4">
                                                <label className="block text-sm font-medium text-gray-700 mb-2">
                                                    Ollama API Key
                                                </label>
                                                <div className="relative">
                                                    <input
                                                        type="text"
                                                        required
                                                        placeholder="Enter your Ollama API key"
                                                        className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                                        value={llmConfig.LLM_API_KEY || ''}
                                                        onChange={(e) => api_key_changed(e.target.value, 'ollama_api_key')}
                                                    />
                                                </div>
                                                <p className="mt-2 text-sm text-gray-500 flex items-center gap-2">
                                                    <span className="block w-1 h-1 rounded-full bg-gray-400"></span>
                                                    Provide this if you are using a custom Ollama instance
                                                </p>
                                            </div>
                                        </>
                                    )}
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        Pexels API Key (required for images)
                                    </label>
                                    <div className="flex gap-3">
                                        <input
                                            type="text"
                                            required
                                            placeholder="Enter your Pexels API key"
                                            className="flex-1 px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            value={llmConfig.PEXELS_API_KEY || ''}
                                            onChange={(e) => api_key_changed(e.target.value, 'pexels')}
                                        />
                                        <button
                                            onClick={handleSaveConfig}
                                            disabled={isLoading || !llmConfig.MODEL}
                                            className={`px-4 py-2 rounded-lg transition-colors ${isLoading || !llmConfig.MODEL
                                                ? 'bg-gray-400 cursor-not-allowed'
                                                : 'bg-blue-600 hover:bg-blue-700'
                                                } text-white`}
                                        >
                                            {isLoading ? (
                                                <div className="flex items-center gap-2">
                                                    <Loader2 className="w-4 h-4 animate-spin" />
                                                    {downloadingModel.downloaded || 0 > 0
                                                        ? `Downloading (${(((downloadingModel.downloaded || 0) / (downloadingModel.size || 1)) * 100).toFixed(0)}%)`
                                                        : 'Saving...'
                                                    }
                                                </div>
                                            ) : (
                                                !llmConfig.MODEL ? 'Select Model' : 'Save'
                                            )}
                                        </button>
                                    </div>
                                    <p className="mt-2 text-sm text-gray-500">Required for using Ollama services with image generation</p>
                                </div>
                                {downloadingModel.status && downloadingModel.status !== 'pulled' && (
                                    <div className="text-sm text-center bg-green-100 rounded-lg p-2 font-semibold capitalize text-gray-600">
                                        {downloadingModel.status}
                                    </div>
                                )}
                            </div>
                        )}
                    </div>
                </div>
            </Wrapper>
        </div>
    );
};

export default SettingsPage;
