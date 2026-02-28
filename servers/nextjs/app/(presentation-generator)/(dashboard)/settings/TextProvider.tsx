import { Button } from '@/components/ui/button';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Switch } from '@/components/ui/switch';
import { cn } from '@/lib/utils';
import { LLMConfig } from '@/types/llm_config';
import { Check, ChevronsUpDown, Loader2 } from 'lucide-react';
import React, { useEffect, useState } from 'react'
import { toast } from 'sonner';


interface OpenAIConfigProps {

    onInputChange: (value: string | boolean, field: string) => void;
    llmConfig: LLMConfig;
}
const TextProvider = ({

    onInputChange,
    llmConfig
}: OpenAIConfigProps

) => {
    const [openModelSelect, setOpenModelSelect] = useState(false);
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [modelsLoading, setModelsLoading] = useState(false);
    const [modelsChecked, setModelsChecked] = useState(false);
    const [apiKey, setApiKey] = useState('');

    const openaiUrl = "https://api.openai.com/v1";

    useEffect(() => {
        setAvailableModels([]);
        setModelsChecked(false);
        onInputChange("", "openai_model");
    }, [apiKey]);

    const onApiKeyChange = (value: string) => {
        setApiKey(value);
        onInputChange(value, "openai_api_key");
    };

    const fetchAvailableModels = async () => {
        // if (!'openaiApiKey') return;

        setModelsLoading(true);
        try {
            const response = await fetch('/api/v1/ppt/openai/models/available', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    url: openaiUrl,
                    api_key: 'openaiApiKey'
                }),
            });

            if (response.ok) {
                const data = await response.json();
                setAvailableModels(data);
                setModelsChecked(true);
                onInputChange("gpt-4.1", "openai_model");
            } else {
                console.error('Failed to fetch models');
                setAvailableModels([]);
                setModelsChecked(true);
            }
        } catch (error) {
            console.error('Error fetching models:', error);
            toast.error('Error fetching models');
            setAvailableModels([]);
            setModelsChecked(true);
        } finally {
            setModelsLoading(false);
        }
    };
    return (
        <div className="space-y-6 bg-[#F9F8F8] p-7 rounded-[20px] ">
            {/* API Key Input */}
            <div className="mb-4 flex items-center justify-between bg-white p-10">
                <div className=" max-w-[290px]">
                    <div className='w-[60px] h-[60px] rounded-[4px] flex items-center justify-center'
                        style={{ backgroundColor: '#4C55541A' }}
                    >
                        <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32" fill="none">
                            <path d="M15.9459 5.31543V26.5767" stroke="#4C5554" strokeWidth="1.59459" strokeLinecap="round" strokeLinejoin="round" />
                            <path d="M5.31531 9.30192V6.64426C5.31531 6.29183 5.45531 5.95384 5.70451 5.70463C5.95372 5.45543 6.29171 5.31543 6.64414 5.31543H25.2477C25.6002 5.31543 25.9382 5.45543 26.1874 5.70463C26.4366 5.95384 26.5766 6.29183 26.5766 6.64426V9.30192" stroke="#4C5554" strokeWidth="1.59459" strokeLinecap="round" strokeLinejoin="round" />
                            <path d="M11.9594 26.5762H19.9324" stroke="#4C5554" strokeWidth="1.59459" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
                    </div>
                    <h3 className="text-xl font-normal text-[#191919] py-2.5">Text Generation Settings</h3>
                    <p className=" text-sm  text-gray-500">
                        Choosing where text contets come from
                    </p>
                </div>
                <div className="flex items-center gap-4">
                    <div className="relative  w-[275px] ">
                        <div className="flex flex-col justify-start gap-2">

                            <label className="block text-sm font-medium text-gray-700 mb-2">
                                OpenAI API Key
                            </label>
                            <input
                                type="text"
                                value={'openaiApiKey'}
                                onChange={(e) => onApiKeyChange(e.target.value)}
                                className="w-full px-2 py-3 outline-none border  border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                                placeholder="Enter your API key"
                            />
                        </div>

                        {/* Check for available models button - show when no models checked or no models found */}

                        {(!modelsChecked || (modelsChecked && availableModels.length === 0)) && (

                            <button
                                onClick={fetchAvailableModels}
                                // disabled={modelsLoading || !'openaiApiKey'}
                                className={` mt-7 py-2.5 bg-[#F7F6F9] px-3.5 rounded-[48px] text-xs font-semibold text-[#101323] transition-all duration-200 border ${modelsLoading
                                    ? " border-gray-300 cursor-not-allowed text-gray-500"
                                    : " border-[#EDEEEF] text-blue-600 hover:bg-[#E8F0FF]/90 focus:ring-2 focus:ring-blue-500/20"
                                    }`}
                            >
                                {modelsLoading ? (
                                    <span className="flex items-center justify-center gap-2">
                                        <Loader2 className="w-4 h-4 animate-spin" />
                                        Checking for models...
                                    </span>
                                ) : (
                                    "Check for available models"
                                )}
                            </button>

                        )}
                    </div>
                    <div className="w-[295px]">
                        {/* Show message if no models found */}
                        {modelsChecked && availableModels.length === 0 && (
                            <div className="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                                <p className="text-sm text-yellow-800">
                                    No models found. Please make sure your API key is valid and has access to OpenAI models.
                                </p>
                            </div>
                        )}

                        {/* Model Selection - only show if models are available */}
                        {modelsChecked && availableModels.length > 0 ? (
                            <div>
                                <label className="block text-sm font-medium text-gray-700 mb-3">
                                    Select OpenAI Model
                                </label>
                                <div className="w-full">
                                    <Popover
                                        open={openModelSelect}
                                        onOpenChange={setOpenModelSelect}
                                    >
                                        <PopoverTrigger asChild>
                                            <Button
                                                variant="outline"
                                                role="combobox"
                                                aria-expanded={openModelSelect}
                                                className="w-full h-12 px-4 py-4 outline-none border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors hover:border-gray-400 justify-between"
                                            >
                                                <div className="flex gap-3 items-center">
                                                    <span className="text-sm font-medium text-gray-900">
                                                        {/* {'openaiModel'
                                                            ? availableModels.find(model => model === 'openaiModel') || 'openaiModel'
                                                            : "Select a model"} */}
                                                    </span>
                                                </div>
                                                <ChevronsUpDown className="w-4 h-4 text-gray-500" />
                                            </Button>
                                        </PopoverTrigger>
                                        <PopoverContent
                                            className="p-0"
                                            align="start"
                                            style={{ width: "var(--radix-popover-trigger-width)" }}
                                        >
                                            <Command>
                                                <CommandInput placeholder="Search models..." />
                                                <CommandList>
                                                    <CommandEmpty>No model found.</CommandEmpty>
                                                    <CommandGroup>
                                                        {availableModels.map((model, index) => (
                                                            <CommandItem
                                                                key={index}
                                                                value={model}
                                                                onSelect={(value) => {
                                                                    onInputChange(value, "openai_model");
                                                                    setOpenModelSelect(false);
                                                                }}
                                                            >
                                                                <Check
                                                                    className={cn(
                                                                        "mr-2 h-4 w-4",
                                                                        'openaiModel' === model
                                                                            ? "opacity-100"
                                                                            : "opacity-0"
                                                                    )}
                                                                />
                                                                <div className="flex gap-3 items-center">
                                                                    <div className="flex flex-col space-y-1 flex-1">
                                                                        <div className="flex items-center justify-between gap-2">
                                                                            <span className="text-sm font-medium text-gray-900">
                                                                                {model}
                                                                            </span>
                                                                        </div>
                                                                    </div>
                                                                </div>
                                                            </CommandItem>
                                                        ))}
                                                    </CommandGroup>
                                                </CommandList>
                                            </Command>
                                        </PopoverContent>
                                    </Popover>
                                </div>
                            </div>
                        ) : null}
                    </div>
                </div>
            </div>


            {/* Web Grounding Toggle - show at the end, below models dropdown */}
            <div className="bg-white flex justify-between items-center p-10 rounded-[12px]">
                <div className=' max-w-[290px]'>

                    <h4 className="text-xl font-normal text-[#191919]">Advanced</h4>
                    <p className="mt-2.5 text-sm  text-gray-500">
                        Configure advanced AI features.
                    </p>
                </div>
                <div className="flex items-center gap-4">

                    <div className="w-[275px]">
                        <div className="flex items-center  mb-4 gap-2.5 ">
                            <Switch
                                checked={true}
                                onCheckedChange={(checked) => onInputChange(checked, "")}
                            />
                            <label className="text-sm font-medium text-gray-700">
                                Enable Web Grounding
                            </label>
                        </div>


                    </div>
                    <div className="w-[295px]"></div>
                </div>

            </div>


        </div>
    )
}

export default TextProvider
