import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_CHAT_MODEL = os.getenv("OLLAMA_CHAT_MODEL", "llama3.1")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

CHROMA_DB_DIR = str(ROOT_DIR / os.getenv("CHROMA_DB_DIR", "./chroma_db").lstrip("./"))
CHROMA_COLLECTION = os.getenv("CHROMA_COLLECTION", "incidents")

HISTORICAL_DATA_PATH = ROOT_DIR / "data" / "historical_incidents.json"
EVAL_EDGE_CASES_PATH = ROOT_DIR / "data" / "eval_edge_cases.json"
