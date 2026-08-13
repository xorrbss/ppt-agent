/**
 * Provider configuration that is safe to copy into the desktop compatibility
 * file and the FastAPI launch environment. Credentials are deliberately absent:
 * existing plaintext files are migrated by FastAPI into the desktop vault, and
 * all new credential updates travel over the authenticated runtime endpoint.
 */
export function buildProviderNonSecretStartupConfig(
  env: NodeJS.ProcessEnv,
): UserConfig {
  return {
    CAN_CHANGE_KEYS: env.CAN_CHANGE_KEYS,
    LLM: env.LLM,
    OPENAI_MODEL: env.OPENAI_MODEL,
    GOOGLE_MODEL: env.GOOGLE_MODEL,
    ANTHROPIC_MODEL: env.ANTHROPIC_MODEL,
    OLLAMA_URL: env.OLLAMA_URL,
    OLLAMA_MODEL: env.OLLAMA_MODEL,
    CUSTOM_LLM_URL: env.CUSTOM_LLM_URL,
    CUSTOM_MODEL: env.CUSTOM_MODEL,
    BEDROCK_REGION: env.BEDROCK_REGION,
    BEDROCK_PROFILE_NAME: env.BEDROCK_PROFILE_NAME,
    BEDROCK_MODEL: env.BEDROCK_MODEL,
    FIREWORKS_MODEL: env.FIREWORKS_MODEL,
    FIREWORKS_BASE_URL: env.FIREWORKS_BASE_URL,
    TOGETHER_MODEL: env.TOGETHER_MODEL,
    TOGETHER_BASE_URL: env.TOGETHER_BASE_URL,
    LMSTUDIO_BASE_URL: env.LMSTUDIO_BASE_URL,
    LMSTUDIO_MODEL: env.LMSTUDIO_MODEL,
    IMAGE_PROVIDER: env.IMAGE_PROVIDER,
    DISABLE_IMAGE_GENERATION: env.DISABLE_IMAGE_GENERATION,
    TOOL_CALLS: env.TOOL_CALLS,
    DISABLE_THINKING: env.DISABLE_THINKING,
    WEB_GROUNDING: env.WEB_GROUNDING,
    DISABLE_ANONYMOUS_TRACKING: env.DISABLE_ANONYMOUS_TRACKING,
    COMFYUI_URL: env.COMFYUI_URL,
    COMFYUI_WORKFLOW: env.COMFYUI_WORKFLOW,
    DALL_E_3_QUALITY: env.DALL_E_3_QUALITY,
    GPT_IMAGE_1_5_QUALITY: env.GPT_IMAGE_1_5_QUALITY,
    GPT_IMAGE_2_QUALITY: env.GPT_IMAGE_2_QUALITY,
  };
}
