from constants.settings_registry import get_setting_default


OPENAI_URL = "https://api.openai.com/v1"

# Default models
DEFAULT_OPENAI_MODEL = get_setting_default("OPENAI_MODEL")
DEFAULT_GOOGLE_MODEL = get_setting_default("GOOGLE_MODEL")
DEFAULT_VERTEX_MODEL = get_setting_default("VERTEX_MODEL")
DEFAULT_AZURE_MODEL = get_setting_default("AZURE_OPENAI_MODEL")
DEFAULT_BEDROCK_MODEL = get_setting_default("BEDROCK_MODEL")
DEFAULT_OPENROUTER_MODEL = get_setting_default("OPENROUTER_MODEL")
DEFAULT_FIREWORKS_MODEL = get_setting_default("FIREWORKS_MODEL")
DEFAULT_TOGETHER_MODEL = get_setting_default("TOGETHER_MODEL")
DEFAULT_CEREBRAS_MODEL = get_setting_default("CEREBRAS_MODEL")
DEFAULT_ANTHROPIC_MODEL = get_setting_default("ANTHROPIC_MODEL")
DEFAULT_LITELLM_MODEL = get_setting_default("LITELLM_MODEL")
DEFAULT_LMSTUDIO_MODEL = get_setting_default("LMSTUDIO_MODEL")
DEFAULT_CODEX_MODEL = get_setting_default("CODEX_MODEL")
