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
    },
    custom: {
        title: "Custom Model Configuration",
        description: "Configure your own OpenAI-compatible model",
        placeholder: "Enter your custom model details",
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
    const [useCustomOllamaUrl, setUseCustomOllamaUrl] = useState<boolean>(userConfigState.llm_config.USE_CUSTOM_URL || false);
    const [customModelsLoading, setCustomModelsLoading] = useState<boolean>(false);
    const [customModelsChecked, setCustomModelsChecked] = useState<boolean>(false);

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

            // Check if currently selected model is still available
            if (llmConfig.CUSTOM_MODEL && data.length > 0) {
                const isModelAvailable = data.includes(llmConfig.CUSTOM_MODEL);
                if (!isModelAvailable) {
                    setLlmConfig({ ...llmConfig, CUSTOM_MODEL: '' });
                    toast({
                        title: 'Model Unavailable',
                        description: `The selected model "${llmConfig.CUSTOM_MODEL}" is no longer available. Please select a different model.`,
                        variant: 'destructive',
                    });
                }
            }
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

    const onCustomModelInfoChange = (value: string, field: string) => {
        setCustomModels([]);
        setCustomModelsChecked(false);
        setLlmConfig({ ...llmConfig, CUSTOM_MODEL: '', CUSTOM_LLM_URL: field === 'custom_llm_url' ? value : llmConfig.CUSTOM_LLM_URL, CUSTOM_LLM_API_KEY: field === 'custom_llm_api_key' ? value : llmConfig.CUSTOM_LLM_API_KEY });
    }

    useEffect(() => {

        if (!canChangeKeys) {
            router.push("/dashboard");
        }
        if (userConfigState.llm_config.LLM === 'ollama') {
            fetchOllamaModels();
        } else if (userConfigState.llm_config.LLM === 'custom' &&
            userConfigState.llm_config.CUSTOM_MODEL &&
            userConfigState.llm_config.CUSTOM_LLM_URL &&
            userConfigState.llm_config.CUSTOM_LLM_API_KEY) {
            fetchCustomModels();
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
                        {llmConfig.LLM !== 'ollama' && llmConfig.LLM !== 'custom' && (
                            <div className="space-y-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        {PROVIDER_CONFIGS[llmConfig.LLM!].title}
                                    </label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            value={llmConfig.LLM === 'openai' ? llmConfig.OPENAI_API_KEY || '' : llmConfig.GOOGLE_API_KEY || ''}
                                            onChange={(e) => input_field_changed(e.target.value, llmConfig.LLM === 'openai' ? 'openai_api_key' : 'google_api_key')}
                                            className="w-full px-4 py-2.5 border border-gray-300 outline-none rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            placeholder={PROVIDER_CONFIGS[llmConfig.LLM!].placeholder}
                                        />
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
                                                                            setLlmConfig({ ...llmConfig, OLLAMA_MODEL: value });
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

                                <div>
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
                                    <p className="mt-2 text-sm text-gray-500">Provide a Pexels API key to generate presentation images</p>
                                </div>
                                {downloadingModel.status && downloadingModel.status !== 'pulled' && (
                                    <div className="text-sm text-center bg-green-100 rounded-lg p-2 font-semibold capitalize text-gray-600">
                                        {downloadingModel.status}
                                    </div>
                                )}
                            </div>
                        )}

                        {/* Custom Model Configuration */}
                        {llmConfig.LLM === 'custom' && (
                            <div className="space-y-6">
                                <div>
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
                                            onChange={(e) => onCustomModelInfoChange(e.target.value, 'custom_llm_url')}
                                        />
                                    </div>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 mb-2">
                                        OpenAI Compatible API Key
                                    </label>
                                    <div className="relative">
                                        <input
                                            type="text"
                                            placeholder="Enter your API key"
                                            className="w-full px-4 py-2.5 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                            value={llmConfig.CUSTOM_LLM_API_KEY || ''}
                                            onChange={(e) => onCustomModelInfoChange(e.target.value, 'custom_llm_api_key')}
                                        />
                                    </div>
                                </div>

                                {/* Model selection dropdown - show if models are available or if there's a selected model */}
                                {((customModelsChecked && customModels.length > 0) || llmConfig.CUSTOM_MODEL) && (
                                    <div>
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
                                                                            setLlmConfig({ ...llmConfig, CUSTOM_MODEL: value });
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

                                {/* Check for available models button - show when no models checked or no models found, and no model is selected */}
                                {(!customModelsChecked || (customModelsChecked && customModels.length === 0)) && !llmConfig.CUSTOM_MODEL && (
                                    <div>
                                        <button
                                            onClick={fetchCustomModels}
                                            disabled={customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY}
                                            className={`w-full py-2.5 px-4 rounded-lg transition-all duration-200 border-2 font-semibold ${customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY
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
                                    <div className="p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                                        <p className="text-sm text-yellow-800">
                                            No models found. Please check your URL and API key, or try again.
                                        </p>
                                    </div>
                                )}

                                {/* Refresh models button - show when there's a selected model but we want to refresh */}
                                {llmConfig.CUSTOM_MODEL && customModelsChecked && (
                                    <div>
                                        <button
                                            onClick={fetchCustomModels}
                                            disabled={customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY}
                                            className={`w-full py-2.5 px-4 rounded-lg transition-all duration-200 border-2 font-semibold ${customModelsLoading || !llmConfig.CUSTOM_LLM_URL || !llmConfig.CUSTOM_LLM_API_KEY
                                                ? 'bg-gray-100 border-gray-300 cursor-not-allowed text-gray-500'
                                                : 'bg-white border-gray-600 text-gray-600 hover:bg-gray-50 focus:ring-2 focus:ring-gray-500/20'
                                                }`}
                                        >
                                            {customModelsLoading ? (
                                                <div className="flex items-center justify-center gap-2">
                                                    <Loader2 className="w-4 h-4 animate-spin" />
                                                    Refreshing models...
                                                </div>
                                            ) : (
                                                'Refresh Available Models'
                                            )}
                                        </button>
                                    </div>
                                )}

                                <div>
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
                                    <p className="mt-2 text-sm text-gray-500">Provide a Pexels API key to generate presentation images</p>
                                </div>
                            </div>
                        )}

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
                </div>
            </Wrapper>
        </div>
    );
};

export default SettingsPage;
