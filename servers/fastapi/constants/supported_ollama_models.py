from models.ollama_model_metadata import OllamaModelMetadata


SUPPORTED_OLLAMA_MODELS = {
    "llama3:8b": OllamaModelMetadata(
        label="Llama 3:8b",
        value="llama3:8b",
        size="4.7GB",
        icon="/static/icons/meta.png",
    ),
    "llama3:70b": OllamaModelMetadata(
        label="Llama 3:70b",
        value="llama3:70b",
        size="40GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.1:8b": OllamaModelMetadata(
        label="Llama 3.1:8b",
        value="llama3.1:8b",
        size="4.9GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.1:70b": OllamaModelMetadata(
        label="Llama 3.1:70b",
        value="llama3.1:70b",
        size="43GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.1:405b": OllamaModelMetadata(
        label="Llama 3.1:405b",
        value="llama3.1:405b",
        size="243GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.2:1b": OllamaModelMetadata(
        label="Llama 3.2:1b",
        value="llama3.2:1b",
        size="1.3GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.2:3b": OllamaModelMetadata(
        label="Llama 3.2:3b",
        value="llama3.2:3b",
        size="2GB",
        icon="/static/icons/meta.png",
    ),
    "llama3.3:70b": OllamaModelMetadata(
        label="Llama 3.3:70b",
        value="llama3.3:70b",
        size="43GB",
        icon="/static/icons/meta.png",
    ),
    "llama4:16x17b": OllamaModelMetadata(
        label="Llama 4:16x17b",
        value="llama4:16x17b",
        size="67GB",
        icon="/static/icons/meta.png",
    ),
    "llama4:128x17b": OllamaModelMetadata(
        label="Llama 4:128x17b",
        value="llama4:128x17b",
        size="245GB",
        icon="/static/icons/meta.png",
    ),
}

SUPPORTED_GEMMA_MODELS = {
    "gemma3:1b": OllamaModelMetadata(
        label="Gemma 3:1b",
        value="gemma3:1b",
        size="815MB",
        icon="/static/icons/gemma.png",
    ),
    "gemma3:4b": OllamaModelMetadata(
        label="Gemma 3:4b",
        value="gemma3:4b",
        size="3.3GB",
        icon="/static/icons/gemma.png",
    ),
    "gemma3:12b": OllamaModelMetadata(
        label="Gemma 3:12b",
        value="gemma3:12b",
        size="8.1GB",
        icon="/static/icons/gemma.png",
    ),
    "gemma3:27b": OllamaModelMetadata(
        label="Gemma 3:27b",
        value="gemma3:27b",
        size="17GB",
        icon="/static/icons/gemma.png",
    ),
}

SUPPORTED_DEEPSEEK_MODELS = {
    "deepseek-r1:1.5b": OllamaModelMetadata(
        label="DeepSeek R1:1.5b",
        value="deepseek-r1:1.5b",
        size="1.1GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:7b": OllamaModelMetadata(
        label="DeepSeek R1:7b",
        value="deepseek-r1:7b",
        size="4.7GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:8b": OllamaModelMetadata(
        label="DeepSeek R1:8b",
        value="deepseek-r1:8b",
        size="5.2GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:14b": OllamaModelMetadata(
        label="DeepSeek R1:14b",
        value="deepseek-r1:14b",
        size="9GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:32b": OllamaModelMetadata(
        label="DeepSeek R1:32b",
        value="deepseek-r1:32b",
        size="20GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:70b": OllamaModelMetadata(
        label="DeepSeek R1:70b",
        value="deepseek-r1:70b",
        size="43GB",
        icon="/static/icons/deepseek.png",
    ),
    "deepseek-r1:671b": OllamaModelMetadata(
        label="DeepSeek R1:671b",
        value="deepseek-r1:671b",
        size="404GB",
        icon="/static/icons/deepseek.png",
    ),
}

SUPPORTED_QWEN_MODELS = {
    "qwen3:0.6b": OllamaModelMetadata(
        label="Qwen 3:0.6b",
        value="qwen3:0.6b",
        size="523MB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:1.7b": OllamaModelMetadata(
        label="Qwen 3:1.7b",
        value="qwen3:1.7b",
        size="1.4GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:4b": OllamaModelMetadata(
        label="Qwen 3:4b",
        value="qwen3:4b",
        size="2.6GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:8b": OllamaModelMetadata(
        label="Qwen 3:8b",
        value="qwen3:8b",
        size="5.2GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:14b": OllamaModelMetadata(
        label="Qwen 3:14b",
        value="qwen3:14b",
        size="9.3GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:30b": OllamaModelMetadata(
        label="Qwen 3:30b",
        value="qwen3:30b",
        size="19GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:32b": OllamaModelMetadata(
        label="Qwen 3:32b",
        value="qwen3:32b",
        size="20GB",
        icon="/static/icons/qwen.png",
    ),
    "qwen3:235b": OllamaModelMetadata(
        label="Qwen 3:235b",
        value="qwen3:235b",
        size="142GB",
        icon="/static/icons/qwen.png",
    ),
}

SUPPORTED_OLLAMA_MODELS = {
    **SUPPORTED_OLLAMA_MODELS,
    **SUPPORTED_GEMMA_MODELS,
    **SUPPORTED_DEEPSEEK_MODELS,
    **SUPPORTED_QWEN_MODELS,
}
