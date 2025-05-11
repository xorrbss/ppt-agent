'use client';

import React, { useState } from "react";
import Header from "../dashboard/components/Header";
import Wrapper from "@/components/Wrapper";
import { Settings, Key, Palette, Bell } from 'lucide-react';
import { toast } from '@/hooks/use-toast';

interface APIConfig {
  provider: string;
  apiKey: string;
}

const SettingsPage = () => {
  const [apiConfigs, setApiConfigs] = useState<Record<string, APIConfig>>({
    openai: {
      provider: 'OpenAI',
      apiKey: '',
    },
    google: {
      provider: 'Google',
      apiKey: '',
    }
  });

  const handleApiKeyChange = (provider: string, value: string) => {
    setApiConfigs(prev => ({
      ...prev,
      [provider]: {
        ...prev[provider],
        apiKey: value
      }
    }));
  };

  const saveApiKey = (provider: string) => {
    const config = apiConfigs[provider];
    if (!config.apiKey) {
      toast({
        title: 'Error',
        description: `Please enter a valid ${config.provider} API key`,
      });
      return;
    }
    localStorage.setItem(`${provider}_api_key`, config.apiKey);
    toast({
      title: 'Success',
      description: `${config.provider} API key saved successfully`,
    });
  };

  return (
    <div className="min-h-screen bg-[#E9E8F8]">
      <Header />

      <Wrapper className="lg:w-[60%]">
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

            <div className="space-y-6">
              {Object.entries(apiConfigs).map(([key, config]) => (
                <div key={key} className="border-b border-gray-100 last:border-0 pb-6 last:pb-0">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    {config.provider} API Key
                  </label>
                  <div className="flex gap-3">
                    <input
                      type="text"
                      value={config.apiKey}
                      onChange={(e) => handleApiKeyChange(key, e.target.value)}
                      className="flex-1 px-4 py-2.5 border border-gray-300 outline-none rounded-lg focus:ring-2 focus:ring-blue-500/20 focus:border-blue-500 transition-colors"
                      placeholder={`Enter your ${config.provider} API key`}
                    />
                    <button
                      onClick={() => saveApiKey(key)}
                      className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
                    >
                      Save
                    </button>
                  </div>
                  <p className="mt-2 text-sm text-gray-500">
                    Required for using {config.provider} services
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>
      </Wrapper>
    </div>
  );
};

export default SettingsPage;
