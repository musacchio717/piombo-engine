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
    EMBEDDING_MODEL: str = "BAAI/bge-m3"
    EMBEDDING_DEVICE: str = "cuda"

    class Config:
        env_file = ".env"

settings = Settings()