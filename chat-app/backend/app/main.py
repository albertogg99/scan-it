from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import admin, attachments, config, conversations, health, public, runs
from app.agent.manager import reconciliate
from app.persistence.session import purge_unattached, session_factory
from app.registry.defaults import seed_default_servers
from app.settings import get_settings


@asynccontextmanager
async def lifespan(application: FastAPI):
    async with session_factory() as session:
        await purge_unattached(session)
        await seed_default_servers(session)
    await reconciliate(session_factory)
    yield


app = FastAPI(lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exception: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": {"code": "request_invalid", "message": "Request fields are invalid."}})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in get_settings().cors_origins.split(",") if origin.strip()],
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
for router in (health.router, config.router, public.router, conversations.router, attachments.router, runs.router, admin.router):
    app.include_router(router)