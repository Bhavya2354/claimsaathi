"""Test settings: a throwaway database and vector store, and no reachable model provider."""

import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet

_TMP = Path(tempfile.mkdtemp(prefix="claimsaathi-tests-"))

_ENV = {
    "LOG_FORMAT": "console",
    "LOG_LEVEL": "WARNING",
    "DATABASE_URL": f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}",
    "OLLAMA_BASE_URL": "http://models.invalid/v1",
    "ROUTE_REASONING": "ollama:test-reasoning",
    "ROUTE_FAST": "ollama:test-fast",
    "ROUTE_VISION": "ollama:test-vision",
    "QDRANT_PATH": str(_TMP / "qdrant"),
    "QDRANT_COLLECTION": "test_clauses",
    "EMBEDDING_MODEL": "BAAI/bge-small-en-v1.5",
    "SPARSE_MODEL": "Qdrant/bm25",
    "RERANK_MODEL": "Xenova/ms-marco-MiniLM-L-6-v2",
    "MODEL_CACHE_DIR": str(_TMP / "models"),
    "WARM_UP_MODELS": "false",
    "PII_VAULT_KEY": Fernet.generate_key().decode(),
    "PII_SPACY_MODEL": "en_core_web_sm",
    "API_CORS_ORIGINS": '["http://localhost:3000"]',
}
for key, value in _ENV.items():
    os.environ[key] = value
# Never read the developer's .env, and never reach a hosted provider from tests.
os.environ["ENV_FILE"] = ""
for provider in ("GROQ", "GEMINI", "OPENROUTER", "CEREBRAS", "MISTRAL", "GITHUB_MODELS"):
    os.environ.pop(f"{provider}_API_KEY", None)
for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "QDRANT_URL"):
    os.environ.pop(key, None)
