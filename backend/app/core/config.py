from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite:///./piombo.db"

    # Neo4j
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USER: str = "neo4j"
    NEO4J_PASSWORD: str = "piombo-password"

    # Qdrant
    QDRANT_URL: str = "http://localhost:6333"

    # API
    API_TITLE: str = "Piombo Engine"
    API_VERSION: str = "0.1.0"
    SEED_LORE_DIR: str = "backend/seed_lore"

    # Embedding
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cuda"

    # LLM
    LLM_MODEL: str = "qwen3:8b"           # switch: "mistral-nemo:12b-instruct-2407-q4_K_M"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 1024
    LLM_NUM_CTX: int = 8192               # context window — max sicuro per 12GB VRAM
    OLLAMA_BASE_URL: str = "http://localhost:11434"

    class Config:
        env_file = ".env"

settings = Settings()
