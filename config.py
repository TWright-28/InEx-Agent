import os

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
os.makedirs(os.path.join(BASE_DIR, "db"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DB_PATH = os.getenv("DATABASE_URL")
# change db in .env

OLLAMA_BASE = "http://localhost:11434"

promptsDir = "prompts/"
classifyPrompt = "prompts/classification_prompt.md"
systemPrompt = "prompts/system_prompt.md"

# Format: "ollama:model-name" for local, or "provider:model" for cloud
# Provider strings: "openai", "anthropic", "google_genai", "google_vertexai"
# Add matching API key to .env: OPENAI_API_KEY, ANTHROPIC_API_KEY, GOOGLE_API_KEY
# Packages: pip install langchain-openai / langchain-anthropic / langchain-google-genai

# orchestrator = "ollama:gpt-oss:20B"
# orchestrator = "openai:gpt-4o"
# orchestrator = "anthropic:claude-sonnet-4-6"
orchestrator = "google_genai:gemini-2.5-flash"

# classifier: bare name (e.g. "qwen3:30b") runs locally via Ollama;
# "provider:model" (e.g. "google_genai:gemini-2.5-flash") runs in the cloud.
classifier = "google_genai:gemini-2.5-pro"
# classifier = "qwen3:30b"
# classifier = "qwen2.5:7b-instruct"

temperature = 0.2
num_ctx = 131072  # only applied when orchestrator uses Ollama

# How many issues to classify in parallel. The bottleneck is per-request model
# latency (gemini-2.5-pro spends ~1 min/issue on thinking), not rate limits, so
# parallelism is the lever that matters. Raise if you have quota headroom; drop
# to 1 for a local Ollama classifier, which serialises on the GPU anyway.
classifyWorkers = 8

# Attempts per issue before it is recorded as a failure. Gemini returns transient
# 503 UNAVAILABLE under load - 5 of 30 issues hit one in a single ground-truth run -
# and those are retried with exponential backoff rather than lost.
classifyRetries = 5
