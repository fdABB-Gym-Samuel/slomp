from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import cache, db, http as http_client
from .config import settings
from .routers import auth as auth_router
from .routers import game as game_router
from .routers import rooms as rooms_router
from .routers import ws as ws_router

# Starlette refuses to attach `Access-Control-Allow-Credentials: true`
# alongside a wildcard origin, but the misconfiguration is silent: browsers
# would reject every authenticated request without a clear error. Fail at
# boot so a misconfigured deploy is visible immediately rather than after
# the first user reports "my cookies aren't working".
if any(o == "*" for o in settings.cors_origins):
    raise RuntimeError(
        "cors_origins cannot contain '*' — credentialed CORS requires an "
        "explicit origin allowlist. Set CORS_ORIGINS to your prod hostnames."
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    await cache.init_cache()
    await http_client.init_http()
    try:
        yield
    finally:
        await http_client.close_http()
        await cache.close_cache()
        await db.close_pool()


app = FastAPI(title="Slomp", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router.router_me)
app.include_router(rooms_router.router)
app.include_router(game_router.router)
app.include_router(ws_router.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok"}
