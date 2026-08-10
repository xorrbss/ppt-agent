export const PRESENTATION_DRIVER_IDS = [
  "standard",
  "smart",
  "template-v2",
  "authored",
] as const;

export type PresentationDriverId = (typeof PRESENTATION_DRIVER_IDS)[number];
export type PresentationContentModel =
  | "legacy-elements"
  | "smart-html"
  | "template-v2"
  | "authored";
export type PresentationGenerationStrategy =
  | "standard"
  | "smart"
  | "template"
  | "authored";
export type PresentationStyleSource =
  | "builtin"
  | "community"
  | "imported"
  | "authored";
export type LegacyGenerationMode = "standard" | "smart";

export interface PresentationProfile {
  readonly schema_version: 1;
  readonly driver_id: PresentationDriverId;
  readonly content_model: PresentationContentModel;
  readonly generation_strategy: PresentationGenerationStrategy;
  readonly style_source: PresentationStyleSource;
  readonly generation_mode: LegacyGenerationMode;
}

export interface PresentationEditorCapabilities {
  readonly element_text_edit: boolean;
  readonly element_geometry_edit: boolean;
  readonly chart_edit: boolean;
  readonly theme_apply: boolean;
  readonly template_apply: boolean;
  readonly slide_image_regenerate: boolean;
  readonly smart_selection: boolean;
}

export interface PresentationCapabilities {
  readonly schema_version: 1;
  readonly driver_id: PresentationDriverId;
  readonly renderer_adapter_id: string;
  readonly editor_adapter_id: string;
  readonly editor: PresentationEditorCapabilities;
  readonly agent_tool_capability: PresentationDriverId;
  readonly export_adapter_id: string;
  readonly export_editable_pptx: boolean;
}

export class PresentationProfileResolutionError extends Error {
  readonly code: string;

  constructor(code: string) {
    super(code);
    this.name = "PresentationProfileResolutionError";
    this.code = code;
  }
}

export interface PresentationDriver {
  readonly driver_id: PresentationDriverId;
  readonly content_model: PresentationContentModel;
  readonly generation_strategy: PresentationGenerationStrategy;
  readonly style_sources: readonly PresentationStyleSource[];
  readonly renderer_adapter_id: string;
  readonly editor_adapter_id: string;
  readonly editor: PresentationEditorCapabilities;
  readonly agent_tool_capability: PresentationDriverId;
  readonly export_adapter_id: string;
  readonly export_editable_pptx: boolean;
  validate(
    profile: PresentationProfile,
    capabilities: PresentationCapabilities,
  ): void;
}

export type PresentationDriverRegistry = {
  readonly [DriverId in PresentationDriverId]: PresentationDriver & {
    readonly driver_id: DriverId;
  };
};

export const GENERATION_MODE_COMPATIBILITY = Object.freeze({
  field_name: "generation_mode" as const,
  source_of_truth: "derived-presentation-profile" as const,
  accepted_values: ["standard", "smart"] as const,
  removal_condition:
    "Remove only after every supported persisted row and public API consumer uses server-issued PresentationProfile.driver_id, no supported rolling deployment reads generation_mode, and a compatibility migration has been released and rollback-tested.",
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isDriverId(value: unknown): value is PresentationDriverId {
  return PRESENTATION_DRIVER_IDS.some((driverId) => driverId === value);
}

function requireBoolean(
  record: Record<string, unknown>,
  key: keyof PresentationEditorCapabilities,
): boolean {
  const value = record[key];
  if (typeof value !== "boolean") {
    throw new PresentationProfileResolutionError(
      "invalid_presentation_capabilities",
    );
  }
  return value;
}

function parseEditorCapabilities(
  value: unknown,
): PresentationEditorCapabilities {
  if (!isRecord(value)) {
    throw new PresentationProfileResolutionError(
      "invalid_presentation_capabilities",
    );
  }
  return Object.freeze({
    element_text_edit: requireBoolean(value, "element_text_edit"),
    element_geometry_edit: requireBoolean(value, "element_geometry_edit"),
    chart_edit: requireBoolean(value, "chart_edit"),
    theme_apply: requireBoolean(value, "theme_apply"),
    template_apply: requireBoolean(value, "template_apply"),
    slide_image_regenerate: requireBoolean(value, "slide_image_regenerate"),
    smart_selection: requireBoolean(value, "smart_selection"),
  });
}

function parseProfile(value: unknown): PresentationProfile {
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_profile_version",
    );
  }
  if (!isDriverId(value.driver_id)) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_driver",
    );
  }
  if (
    ![
      "legacy-elements",
      "smart-html",
      "template-v2",
      "authored",
    ].includes(String(value.content_model))
  ) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_content_model",
    );
  }
  if (
    !["standard", "smart", "template", "authored"].includes(
      String(value.generation_strategy),
    )
  ) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_generation_strategy",
    );
  }
  if (
    !["builtin", "community", "imported", "authored"].includes(
      String(value.style_source),
    )
  ) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_style_source",
    );
  }
  if (value.generation_mode !== "standard" && value.generation_mode !== "smart") {
    throw new PresentationProfileResolutionError("unsupported_generation_mode");
  }
  return Object.freeze({
    schema_version: 1,
    driver_id: value.driver_id,
    content_model: value.content_model as PresentationContentModel,
    generation_strategy:
      value.generation_strategy as PresentationGenerationStrategy,
    style_source: value.style_source as PresentationStyleSource,
    generation_mode: value.generation_mode,
  });
}

function parseCapabilities(value: unknown): PresentationCapabilities {
  if (!isRecord(value) || value.schema_version !== 1) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_capability_version",
    );
  }
  if (!isDriverId(value.driver_id)) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_driver",
    );
  }
  if (
    typeof value.renderer_adapter_id !== "string" ||
    typeof value.editor_adapter_id !== "string" ||
    typeof value.export_adapter_id !== "string" ||
    !isDriverId(value.agent_tool_capability) ||
    typeof value.export_editable_pptx !== "boolean"
  ) {
    throw new PresentationProfileResolutionError(
      "invalid_presentation_capabilities",
    );
  }
  return Object.freeze({
    schema_version: 1,
    driver_id: value.driver_id,
    renderer_adapter_id: value.renderer_adapter_id,
    editor_adapter_id: value.editor_adapter_id,
    editor: parseEditorCapabilities(value.editor),
    agent_tool_capability: value.agent_tool_capability,
    export_adapter_id: value.export_adapter_id,
    export_editable_pptx: value.export_editable_pptx,
  });
}

function sameEditorCapabilities(
  actual: PresentationEditorCapabilities,
  expected: PresentationEditorCapabilities,
): boolean {
  return (
    actual.element_text_edit === expected.element_text_edit &&
    actual.element_geometry_edit === expected.element_geometry_edit &&
    actual.chart_edit === expected.chart_edit &&
    actual.theme_apply === expected.theme_apply &&
    actual.template_apply === expected.template_apply &&
    actual.slide_image_regenerate === expected.slide_image_regenerate &&
    actual.smart_selection === expected.smart_selection
  );
}

function validateDriverContract(
  driver: Omit<PresentationDriver, "validate">,
  profile: PresentationProfile,
  capabilities: PresentationCapabilities,
): void {
  const expectedGenerationMode: LegacyGenerationMode =
    driver.driver_id === "smart" ? "smart" : "standard";
  if (
    profile.driver_id !== driver.driver_id ||
    profile.content_model !== driver.content_model ||
    profile.generation_strategy !== driver.generation_strategy ||
    profile.generation_mode !== expectedGenerationMode ||
    !driver.style_sources.includes(profile.style_source)
  ) {
    throw new PresentationProfileResolutionError(
      "presentation_profile_driver_conflict",
    );
  }
  if (
    capabilities.driver_id !== driver.driver_id ||
    capabilities.renderer_adapter_id !== driver.renderer_adapter_id ||
    capabilities.editor_adapter_id !== driver.editor_adapter_id ||
    capabilities.agent_tool_capability !== driver.agent_tool_capability ||
    capabilities.export_adapter_id !== driver.export_adapter_id ||
    capabilities.export_editable_pptx !== driver.export_editable_pptx ||
    !sameEditorCapabilities(capabilities.editor, driver.editor)
  ) {
    throw new PresentationProfileResolutionError(
      "presentation_capability_driver_conflict",
    );
  }
}

function driver<const Driver extends Omit<PresentationDriver, "validate">>(
  definition: Driver,
): Driver & Pick<PresentationDriver, "validate"> {
  return Object.freeze({
    ...definition,
    editor: Object.freeze({ ...definition.editor }),
    style_sources: Object.freeze([...definition.style_sources]),
    validate(profile: PresentationProfile, capabilities: PresentationCapabilities) {
      validateDriverContract(definition, profile, capabilities);
    },
  });
}

export const PRESENTATION_DRIVER_REGISTRY = Object.freeze({
  standard: driver({
    driver_id: "standard",
    content_model: "legacy-elements",
    generation_strategy: "standard",
    style_sources: ["builtin", "community", "imported"],
    renderer_adapter_id: "legacy-elements",
    editor_adapter_id: "legacy-elements",
    editor: {
      element_text_edit: true,
      element_geometry_edit: true,
      chart_edit: true,
      theme_apply: true,
      template_apply: true,
      slide_image_regenerate: false,
      smart_selection: false,
    },
    agent_tool_capability: "standard",
    export_adapter_id: "legacy-general",
    export_editable_pptx: true,
  }),
  smart: driver({
    driver_id: "smart",
    content_model: "smart-html",
    generation_strategy: "smart",
    style_sources: ["builtin", "community", "imported"],
    renderer_adapter_id: "smart-html",
    editor_adapter_id: "smart-html",
    editor: {
      element_text_edit: true,
      element_geometry_edit: true,
      chart_edit: false,
      theme_apply: false,
      template_apply: false,
      slide_image_regenerate: false,
      smart_selection: true,
    },
    agent_tool_capability: "smart",
    export_adapter_id: "legacy-general",
    export_editable_pptx: true,
  }),
  "template-v2": driver({
    driver_id: "template-v2",
    content_model: "template-v2",
    generation_strategy: "template",
    style_sources: ["imported"],
    renderer_adapter_id: "template-v2",
    editor_adapter_id: "template-v2",
    editor: {
      element_text_edit: true,
      element_geometry_edit: true,
      chart_edit: true,
      theme_apply: true,
      template_apply: true,
      slide_image_regenerate: false,
      smart_selection: false,
    },
    agent_tool_capability: "template-v2",
    export_adapter_id: "template-v2-general",
    export_editable_pptx: true,
  }),
  authored: driver({
    driver_id: "authored",
    content_model: "authored",
    generation_strategy: "authored",
    style_sources: ["authored"],
    renderer_adapter_id: "authored",
    editor_adapter_id: "authored-readonly",
    editor: {
      element_text_edit: false,
      element_geometry_edit: false,
      chart_edit: false,
      theme_apply: false,
      template_apply: true,
      slide_image_regenerate: true,
      smart_selection: false,
    },
    agent_tool_capability: "authored",
    export_adapter_id: "authored-hybrid",
    export_editable_pptx: true,
  }),
} satisfies PresentationDriverRegistry);

export function getPresentationDriver(
  driverId: unknown,
): PresentationDriver {
  if (!isDriverId(driverId)) {
    throw new PresentationProfileResolutionError(
      "unsupported_presentation_driver",
    );
  }
  return PRESENTATION_DRIVER_REGISTRY[driverId];
}

export interface ServerIssuedPresentationProfile {
  readonly profile: PresentationProfile;
  readonly capabilities: PresentationCapabilities;
  readonly driver: PresentationDriver;
}

/**
 * Resolve only server-issued profile/capability fields. Root-level legacy or
 * client-computed hints are deliberately ignored and can never choose a driver.
 */
export function resolveServerIssuedPresentationProfile(
  response: unknown,
): ServerIssuedPresentationProfile {
  if (!isRecord(response)) {
    throw new PresentationProfileResolutionError(
      "presentation_profile_required",
    );
  }
  const profile = parseProfile(response.profile);
  const capabilities = parseCapabilities(response.capabilities);
  const resolvedDriver = getPresentationDriver(profile.driver_id);
  resolvedDriver.validate(profile, capabilities);
  return Object.freeze({
    profile,
    capabilities,
    driver: resolvedDriver,
  });
}

/** UI-safe fail-closed facade: malformed or future profiles grant no capability. */
export function tryResolveServerIssuedPresentationProfile(
  response: unknown,
): ServerIssuedPresentationProfile | null {
  try {
    return resolveServerIssuedPresentationProfile(response);
  } catch (error) {
    if (error instanceof PresentationProfileResolutionError) return null;
    throw error;
  }
}

export const presentationCapabilitySelectors = Object.freeze({
  canEditElements(capabilities: PresentationCapabilities): boolean {
    return (
      capabilities.editor.element_text_edit ||
      capabilities.editor.element_geometry_edit
    );
  },
  canEditCharts(capabilities: PresentationCapabilities): boolean {
    return capabilities.editor.chart_edit;
  },
  canApplyTheme(capabilities: PresentationCapabilities): boolean {
    return capabilities.editor.theme_apply;
  },
  canApplyTemplate(capabilities: PresentationCapabilities): boolean {
    return capabilities.editor.template_apply;
  },
  canRegenerateSlideImage(capabilities: PresentationCapabilities): boolean {
    return capabilities.editor.slide_image_regenerate;
  },
  canUseSmartSelection(capabilities: PresentationCapabilities): boolean {
    return capabilities.editor.smart_selection;
  },
  canExportEditablePptx(capabilities: PresentationCapabilities): boolean {
    return capabilities.export_editable_pptx;
  },
});

export function encodeLegacyGenerationMode(
  profile: PresentationProfile,
): LegacyGenerationMode {
  const expected = profile.driver_id === "smart" ? "smart" : "standard";
  if (profile.generation_mode !== expected) {
    throw new PresentationProfileResolutionError(
      "generation_mode_profile_conflict",
    );
  }
  return expected;
}
