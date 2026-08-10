# Runtime provider settings

This file is generated from `servers/fastapi/constants/settings_registry.json`.
Do not edit the table by hand; run `npm run generate:settings-registry`.
Secret values are never read or rendered by the generator.

| Key | Type | Default | Secret | Description |
| --- | --- | --- | --- | --- |
| `CAN_CHANGE_KEYS` | boolean | `true` | no | Allow provider settings to be changed from the application. |
| `LLM` | enum | — | no | Text generation provider id. |
| `OPENAI_API_KEY` | string | — | yes | OpenAI API key. |
| `OPENAI_MODEL` | string | `gpt-4.1` | no | OpenAI model id. |
| `GOOGLE_API_KEY` | string | — | yes | Google Generative AI API key. |
| `GOOGLE_MODEL` | string | `models/gemini-2.5-flash` | no | Google model id. |
| `VERTEX_API_KEY` | string | — | yes | Vertex AI API key authentication value. |
| `VERTEX_MODEL` | string | `gemini-2.5-flash` | no | Vertex AI model id. |
| `VERTEX_PROJECT` | string | — | no | Google Cloud project for ADC-based Vertex authentication. |
| `VERTEX_LOCATION` | string | — | no | Vertex AI region. |
| `VERTEX_BASE_URL` | string | — | no | Optional Vertex AI base URL override. |
| `AZURE_OPENAI_API_KEY` | string | — | yes | Azure OpenAI API key. |
| `AZURE_OPENAI_MODEL` | string | `gpt-4.1` | no | Azure OpenAI model id. |
| `AZURE_OPENAI_ENDPOINT` | string | — | no | Azure OpenAI resource endpoint. |
| `AZURE_OPENAI_BASE_URL` | string | — | no | Optional Azure OpenAI base URL. |
| `AZURE_OPENAI_API_VERSION` | string | — | no | Azure OpenAI API version. |
| `AZURE_OPENAI_DEPLOYMENT` | string | — | no | Azure OpenAI deployment name. |
| `BEDROCK_REGION` | string | `us-east-1` | no | AWS Bedrock region. |
| `BEDROCK_API_KEY` | string | — | yes | AWS Bedrock API key. |
| `BEDROCK_AWS_ACCESS_KEY_ID` | string | — | yes | AWS access key id for Bedrock. |
| `BEDROCK_AWS_SECRET_ACCESS_KEY` | string | — | yes | AWS secret access key for Bedrock. |
| `BEDROCK_AWS_SESSION_TOKEN` | string | — | yes | Optional AWS session token for Bedrock. |
| `BEDROCK_PROFILE_NAME` | string | — | no | Optional AWS profile name for Bedrock. |
| `BEDROCK_MODEL` | string | `us.anthropic.claude-3-5-haiku-20241022-v1:0` | no | AWS Bedrock model or inference profile id. |
| `OPENROUTER_API_KEY` | string | — | yes | OpenRouter API key. |
| `OPENROUTER_MODEL` | string | `openai/gpt-4o` | no | OpenRouter model id. |
| `OPENROUTER_BASE_URL` | string | — | no | Optional OpenRouter base URL. |
| `FIREWORKS_API_KEY` | string | — | yes | Fireworks API key. |
| `FIREWORKS_MODEL` | string | `accounts/fireworks/models/llama-v3p1-8b-instruct` | no | Fireworks model id. |
| `FIREWORKS_BASE_URL` | string | — | no | Optional Fireworks base URL. |
| `TOGETHER_API_KEY` | string | — | yes | Together AI API key. |
| `TOGETHER_MODEL` | string | `openai/gpt-oss-20b` | no | Together AI model id. |
| `TOGETHER_BASE_URL` | string | — | no | Optional Together AI base URL. |
| `CEREBRAS_API_KEY` | string | — | yes | Cerebras API key. |
| `CEREBRAS_MODEL` | string | `llama-3.3-70b` | no | Cerebras model id. |
| `CEREBRAS_BASE_URL` | string | — | no | Optional Cerebras base URL. |
| `ANTHROPIC_API_KEY` | string | — | yes | Anthropic API key. |
| `ANTHROPIC_MODEL` | string | `claude-sonnet-4-20250514` | no | Anthropic model id. |
| `OLLAMA_URL` | string | `http://localhost:11434` | no | Ollama HTTP API base URL. |
| `OLLAMA_MODEL` | string | — | no | Ollama model name. |
| `CUSTOM_LLM_URL` | string | — | no | Custom OpenAI-compatible LLM base URL. |
| `CUSTOM_LLM_API_KEY` | string | — | yes | Custom LLM API key. |
| `CUSTOM_MODEL` | string | — | no | Custom LLM model id. |
| `LITELLM_BASE_URL` | string | — | no | LiteLLM gateway base URL. |
| `LITELLM_API_KEY` | string | — | yes | Optional LiteLLM gateway API key. |
| `LITELLM_MODEL` | string | `gpt-4.1` | no | LiteLLM model id. |
| `LMSTUDIO_BASE_URL` | string | — | no | LM Studio OpenAI-compatible base URL. |
| `LMSTUDIO_API_KEY` | string | — | yes | Optional LM Studio API key. |
| `LMSTUDIO_MODEL` | string | `openai/gpt-oss-20b` | no | LM Studio model id. |
| `CODEX_MODEL` | string | `gpt-5.2` | no | Codex OAuth model id. |
| `CODEX_REASONING_EFFORT` | enum | — | no | Optional Codex reasoning effort. |
| `DISABLE_THINKING` | boolean | `false` | no | Disable custom-provider thinking mode. |
| `WEB_GROUNDING` | boolean | `false` | no | Enable provider web grounding where supported. |
| `DISABLE_IMAGE_GENERATION` | boolean | `false` | no | Disable slide image generation. |
| `ENABLE_PARALLEL_IMAGE_GENERATION` | boolean | `true` | no | Allow image provider requests to run concurrently. |
| `IMAGE_PROVIDER` | enum | — | no | Image generation provider id. |
| `PEXELS_API_KEY` | string | — | yes | Pexels API key. |
| `PIXABAY_API_KEY` | string | — | yes | Pixabay API key. |
| `COMFYUI_URL` | string | — | no | ComfyUI base URL. |
| `COMFYUI_WORKFLOW` | json | — | no | ComfyUI workflow JSON. |
| `OPEN_WEBUI_IMAGE_URL` | string | — | no | Open WebUI-compatible image endpoint. |
| `OPEN_WEBUI_IMAGE_API_KEY` | string | — | yes | Open WebUI image API key. |
| `OPENAI_COMPAT_IMAGE_BASE_URL` | string | — | no | OpenAI-compatible image API base URL. |
| `OPENAI_COMPAT_IMAGE_API_KEY` | string | — | yes | OpenAI-compatible image API key. |
| `OPENAI_COMPAT_IMAGE_MODEL` | string | — | no | OpenAI-compatible image model id. |
| `DALL_E_3_QUALITY` | enum | `standard` | no | DALL-E 3 output quality. |
| `GPT_IMAGE_1_5_QUALITY` | enum | `medium` | no | GPT Image 1.5 output quality. |
| `GPT_IMAGE_2_QUALITY` | enum | `medium` | no | GPT Image 2 output quality. |
| `WEB_SEARCH_PROVIDER` | enum | `auto` | no | Web-search provider selected from the canonical provider registry. |
| `SEARXNG_BASE_URL` | string | — | no | SearXNG base URL. |
| `TAVILY_API_KEY` | string | — | yes | Tavily API key. |
| `EXA_API_KEY` | string | — | yes | Exa API key. |
| `BRAVE_SEARCH_API_KEY` | string | — | yes | Brave Search API key. |

