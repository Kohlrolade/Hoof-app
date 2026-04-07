"""Application factory.

This keeps FastAPI setup, middleware, static mounting and router registration in
one predictable place so the rest of the project can stay focused on business
concerns.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import APP_TITLE, SESSION_SECRET, STATIC_DIR
from .db.seed import init_db
from .logging_config import configure_logging
from .routes.admin import router as admin_router
from .routes.core import router as core_router
from .routes.delivery_notes import router as delivery_note_router
from .routes.invoices import router as invoice_router
from .routes.master_data import router as master_data_router
from .routes.payments import router as payment_router


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize the database once when the FastAPI app starts."""
    init_db()
    yield


def create_app() -> FastAPI:
    configure_logging()
    app = FastAPI(title=APP_TITLE, lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)
    app.mount('/static', StaticFiles(directory=STATIC_DIR), name='static')

    for router in [
        core_router,
        master_data_router,
        delivery_note_router,
        invoice_router,
        payment_router,
        admin_router,
    ]:
        app.include_router(router)

    return app
