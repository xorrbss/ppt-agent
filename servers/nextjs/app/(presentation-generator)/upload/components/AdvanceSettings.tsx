import ToolTip from '@/components/ToolTip';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { Pencil, Save, SlidersHorizontal, X } from 'lucide-react';
import { useTranslations } from 'next-intl';
import React, { useEffect, useState } from 'react';
import CurrentConfig from "./CurrentConfig";
import { PresentationConfig, ToneType, VerbosityType } from '../type';
import { CODEX_GENERATION_PRESETS, type CodexGenerationPresetId } from '@/components/codex-config';

interface ConfigurationSelectsProps {
    config: PresentationConfig;
    onConfigChange: (key: keyof PresentationConfig, value: any) => void;
    sourceContentMode?: boolean;
}

const toggleClassName =
    'h-[22px] w-[36px] border-0 bg-[#D8D8DD] data-[state=checked]:bg-[#7A5AF8] ';

const toneLabelKeys = {
    [ToneType.Default]: 'tone.default',
    [ToneType.Casual]: 'tone.casual',
    [ToneType.Professional]: 'tone.professional',
    [ToneType.Funny]: 'tone.funny',
    [ToneType.Educational]: 'tone.educational',
    [ToneType.Sales_Pitch]: 'tone.salesPitch',
} as const;

const verbosityLabelKeys = {
    [VerbosityType.Concise]: 'verbosity.concise',
    [VerbosityType.Standard]: 'verbosity.standard',
    [VerbosityType.Text_Heavy]: 'verbosity.textHeavy',
} as const;

const AdvanceSettings = ({
    config,
    onConfigChange,
    sourceContentMode = false,
}: ConfigurationSelectsProps) => {
    const t = useTranslations('upload.advancedSettings');
    const [openAdvanced, setOpenAdvanced] = useState(false);

    const [advancedDraft, setAdvancedDraft] = useState({
        tone: config.tone,
        verbosity: config.verbosity,
        instructions: config.instructions,
        includeTableOfContents: config.includeTableOfContents,
        includeTitleSlide: config.includeTitleSlide,
        webSearch: config.webSearch,
        codexGenerationPreset: config.codexGenerationPreset,
    });

    const syncDraftFromConfig = () => {
        setAdvancedDraft({
            tone: config.tone,
            verbosity: config.verbosity,
            instructions: config.instructions,
            includeTableOfContents: config.includeTableOfContents,
            includeTitleSlide: config.includeTitleSlide,
            webSearch: config.webSearch,
            codexGenerationPreset: config.codexGenerationPreset,
        });
    };

    const handleOpenAdvanced = () => {
        syncDraftFromConfig();
        setOpenAdvanced(true);
    };

    const handleCloseAdvanced = () => {
        setOpenAdvanced(false);
    };

    const handleSaveAdvanced = () => {
        onConfigChange('tone', advancedDraft.tone);
        onConfigChange('verbosity', advancedDraft.verbosity);
        onConfigChange('instructions', advancedDraft.instructions);
        onConfigChange('includeTableOfContents', advancedDraft.includeTableOfContents);
        onConfigChange('includeTitleSlide', advancedDraft.includeTitleSlide);
        onConfigChange('webSearch', advancedDraft.webSearch);
        onConfigChange('codexGenerationPreset', advancedDraft.codexGenerationPreset);
        setOpenAdvanced(false);
    };

    useEffect(() => {
        if (!openAdvanced) {
            return;
        }

        const previousOverflow = document.body.style.overflow;

        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                handleCloseAdvanced();
            }
        };

        document.body.style.overflow = 'hidden';
        window.addEventListener('keydown', onKeyDown);

        return () => {
            document.body.style.overflow = previousOverflow;
            window.removeEventListener('keydown', onKeyDown);
        };
    }, [openAdvanced]);

    return (
        <>
            <div className="ml-auto">
                <ToolTip content={t('title')}>
                    <button
                        aria-label={t('title')}
                        title={t('title')}
                        type="button"
                        onClick={handleOpenAdvanced}
                        className="flex h-10 w-10 items-center justify-center rounded-full border border-[#E4E5E8] bg-white text-[#1C1C27] shadow-sm transition hover:bg-[#F7F7FA] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141E5]/25"
                        data-testid="advanced-settings-button"
                    >
                        <SlidersHorizontal className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                </ToolTip>
            </div>

            {openAdvanced && (
                <div
                    className="fixed inset-0 z-[70] bg-slate-950/35 backdrop-blur-[2px] flex items-center justify-center p-4"
                    onClick={handleCloseAdvanced}
                    role="presentation"
                >
                    <div
                        role="dialog"
                        aria-modal="true"
                        aria-label={t('title')}
                        className="relative mx-auto w-full max-w-[640px]"
                        onClick={(event) => event.stopPropagation()}
                    >
                        <button
                            type="button"
                            onClick={handleCloseAdvanced}
                            aria-label={t('close')}
                            className="absolute right-4 top-4 inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#5141e5]/40 disabled:pointer-events-none z-10"
                        >
                            <X className="h-3.5 w-3.5" />
                        </button>

                        <div className="max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl border border-gray-200/80 bg-white shadow-[0_12px_40px_-16px_rgba(15,23,42,0.18)]">
                            <div className="flex flex-wrap items-start justify-between gap-4 border-b border-gray-100 px-6 py-6 pr-16">
                                <div>
                                    <h2 className="break-keep text-xl font-semibold leading-7 tracking-tight text-gray-900">
                                        {t('title')}
                                    </h2>
                                    <p className="mt-2 break-keep text-sm leading-6 text-gray-500">{t('description')}</p>
                                    {sourceContentMode ? <p className="mt-3 text-sm font-semibold text-[#1F1D2A]" data-testid="source-content-advanced-notice">
                                        {t('sourceContentNotice')}
                                    </p> : null}
                                </div>

                                <Button
                                    type="button"
                                    onClick={handleSaveAdvanced}
                                    variant="toolbar"
                                    size="toolbar"
                                    className="px-5 font-syne"
                                >
                                    <Save className="h-4 w-4" aria-hidden="true" />
                                    {t('save')}
                                </Button>
                            </div>

                            <div className="border-b border-slate-100 bg-white px-6 py-4">
                                <CurrentConfig />
                            </div>

                            <div className="border-b border-gray-100 bg-gray-50/70 px-6 py-5">
                                <div className="flex items-start gap-2">
                                    <Pencil className="mt-[3px] h-3.5 w-3.5 text-[#1C1B24]" />
                                    <div className="w-full">
                                        <label
                                            htmlFor="advanced-instructions"
                                            className="block font-syne text-sm font-semibold leading-none text-[#1F1D2A]"
                                        >
                                            {sourceContentMode ? t('sourceContentInstructionsLabel') : t('instructionsLabel')}
                                        </label>
                                        <Textarea
                                            id="advanced-instructions"
                                            value={advancedDraft.instructions}
                                            autoFocus={true}
                                            rows={2}
                                            onChange={(event) =>
                                                setAdvancedDraft((prev) => ({ ...prev, instructions: event.target.value }))
                                            }
                                            placeholder={t('instructionsPlaceholder')}
                                            className="mt-1 min-h-[64px] resize-none border-0 bg-transparent p-0 text-sm leading-[1.3] text-[#242430] shadow-none placeholder:text-[#7C7B87] focus-visible:ring-0 focus-visible:ring-offset-0"
                                        />
                                    </div>
                                </div>
                            </div>

                            {!sourceContentMode ? <div className="space-y-4 px-6 pb-5 pt-3.5 ">
                                <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">{t('toneLabel')}</label>
                                    <Select
                                        value={advancedDraft.tone}
                                        onValueChange={(value) =>
                                            setAdvancedDraft((prev) => ({ ...prev, tone: value as ToneType }))
                                        }

                                    >
                                        <SelectTrigger aria-label={t('toneLabel')} className="p-2.5 w-[120px] rounded-xl border-[#DBDBE1] bg-white font-syne text-sm font-medium text-[#2C2B37] shadow-none focus:ring-0 focus-visible:ring-0">
                                            <SelectValue placeholder={t('tonePlaceholder')} />
                                        </SelectTrigger>
                                        <SelectContent className="z-[120] font-syne">
                                            {Object.values(ToneType).map((tone) => (
                                                <SelectItem key={tone} value={tone} className="text-sm font-medium capitalize">
                                                    {t(toneLabelKeys[tone])}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">{t('verbosityLabel')}</label>
                                    <Select
                                        value={advancedDraft.verbosity}
                                        onValueChange={(value) =>
                                            setAdvancedDraft((prev) => ({ ...prev, verbosity: value as VerbosityType }))
                                        }
                                    >
                                        <SelectTrigger aria-label={t('verbosityLabel')} className="p-2.5 w-[120px] rounded-xl border-[#DBDBE1] bg-white font-syne text-sm font-medium text-[#2C2B37] shadow-none focus:ring-0 focus-visible:ring-0">
                                            <SelectValue placeholder={t('verbosityPlaceholder')} />
                                        </SelectTrigger>
                                        <SelectContent className="z-[120] font-syne">
                                            {Object.values(VerbosityType).map((verbosity) => (
                                                <SelectItem key={verbosity} value={verbosity} className="text-sm font-medium capitalize">
                                                    {t(verbosityLabelKeys[verbosity])}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>

                                {advancedDraft.codexGenerationPreset ? <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">{t('generationModelLabel')}</label>
                                    <Select
                                        value={advancedDraft.codexGenerationPreset}
                                        onValueChange={(value) =>
                                            setAdvancedDraft((prev) => ({ ...prev, codexGenerationPreset: value as CodexGenerationPresetId }))
                                        }
                                    >
                                        <SelectTrigger aria-label={t('generationModelLabel')} className="p-2.5 w-[160px] rounded-xl border-[#DBDBE1] bg-white font-syne text-sm font-medium text-[#2C2B37] shadow-none focus:ring-0 focus-visible:ring-0">
                                            <SelectValue placeholder={t('generationModelPlaceholder')} />
                                        </SelectTrigger>
                                        <SelectContent className="z-[120] font-syne">
                                            {CODEX_GENERATION_PRESETS.map((preset) => (
                                                <SelectItem key={preset.id} value={preset.id} className="text-sm font-medium">
                                                    {preset.name}
                                                </SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div> : null}

                                <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">
                                        {t('includeTableOfContents')}
                                    </label>
                                    <Switch
                                        aria-label={t('includeTableOfContents')}
                                        checked={advancedDraft.includeTableOfContents}
                                        onCheckedChange={(checked) =>
                                            setAdvancedDraft((prev) => ({ ...prev, includeTableOfContents: checked }))
                                        }
                                        className={toggleClassName}
                                    />
                                </div>

                                <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">{t('includeTitleSlide')}</label>
                                    <Switch
                                        aria-label={t('includeTitleSlide')}
                                        checked={advancedDraft.includeTitleSlide}
                                        onCheckedChange={(checked) =>
                                            setAdvancedDraft((prev) => ({ ...prev, includeTitleSlide: checked }))
                                        }
                                        className={toggleClassName}
                                    />
                                </div>

                                <div className="flex items-center justify-between gap-3">
                                    <label className="font-syne text-sm font-semibold leading-none text-[#1F1D2A]">{t('webSearch')}</label>
                                    <Switch
                                        aria-label={t('webSearch')}
                                        checked={advancedDraft.webSearch}
                                        onCheckedChange={(checked) =>
                                            setAdvancedDraft((prev) => ({ ...prev, webSearch: checked }))
                                        }
                                        className={toggleClassName}
                                    />
                                </div>
                            </div> : <p className="px-6 pb-5 pt-3.5 text-sm text-[#5E626A]" data-testid="source-content-fixed-options">
                                {t('sourceContentFixedOptions')}
                            </p>}
                        </div>
                    </div>
                </div>
            )}
        </>
    );
};

export default AdvanceSettings;
