from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api import game, session, character, admin

app = FastAPI(
    title=settings.API_TITLE,
    version=settings.API_VERSION,
    description="Dystopian AI-narrated text adventure game"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(session.router)
app.include_router(game.router)
app.include_router(character.router)
app.include_router(admin.router)

@app.get("/health")
def health():
    return {"status": "ok", "version": settings.API_VERSION}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)