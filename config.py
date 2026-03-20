from dotenv import load_dotenv
import os 
import sqlite3

load_dotenv()

BASE_DIR = os.path.dirname(__file__)
os.makedirs(os.path.join(BASE_DIR, "db"), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "logs"), exist_ok=True)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
DB_PATH = os.getenv("DATABASE_URL")
OLLAMA_BASE = "http://localhost:11434"

promptsDir = "prompts/"
classifyPrompt = "prompts/classification_prompt.md"


orchestrator = "gpt-oss:20B"
# classifier = "qwen3:30b"
classifier = "qwen2.5:7b-instruct"

temperature = 0.2
max_tokens = 32000



