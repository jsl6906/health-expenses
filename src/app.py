"""FastAPI application for Personal Health Expenses Management System.

Modularized root containing only:
 - App/templating setup
 - Router includes (API lives in api/* modules)
 - HTML page routes (until moved to a pages router)
 - Diagnostic endpoints

All CRUD JSON endpoints now reside in dedicated routers under api/.
"""

import os
import zlib
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy.orm import Session  # retained for diagnostic endpoint dependency

from .api.attachments import router as attachments_router
from .api.charges import router as charges_router
from .api.claims import router as claims_router
from .api.dependencies import (
    AGENT_ID_HEADER_NAME,
    REQUEST_ID_HEADER_NAME,
    UI_SESSION_COOKIE,
    api_key_auth,
    get_cors_origins,
    get_ui_session_token,
)
from .api.family_members import router as family_members_router
from .api.imports import router as imports_router
from .api.insurance import router as insurance_router
from .api.pages import router as pages_router
from .api.payments import router as payments_router
from .api.premiums import router as premiums_router
from .api.providers import router as providers_router

try:  # Prefer absolute imports when running as a module
    from database_config import db_config, get_db
except ImportError:  # pragma: no cover - fallback to relative when executed differently
    from .database_config import db_config, get_db  # type: ignore

app = FastAPI(title="Personal Health Expenses Management System", version="1.0.0")

cors_origins = get_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

project_root = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(project_root / "templates"))
templates.env.globals["ENV_MODE"] = getattr(db_config, "env_mode", "TEST")
app.mount("/static", StaticFiles(directory=str(project_root / "static")), name="static")


def format_currency(value):
    try:
        if value is None:
            return "$0.00"
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


templates.env.filters["currency"] = format_currency


def fam_avatar(name: str) -> Markup:
    """Return a small square-letter avatar (HTML Markup) for the given family member name.

    The avatar displays the uppercase first letter inside a visually distinct square. Returns
    an empty string for falsy names. The result is returned as Markup so templates can
    render it as HTML safely.
    """
    try:
        if not name:
            return Markup("")
        raw = str(name).strip()
        ch = raw[0:1].upper()
        # Fallback for non-printable characters
        if not ch or ch.isspace():
            ch = "?"
        # crc32 is stable across processes, unlike hash()
        mod = f"fam-avatar--c{zlib.crc32(raw.lower().encode()) % 4}"
        html = f'<span class="fam-avatar {mod}" title="{escape(raw)}">{escape(ch)}</span>'
        return Markup(html)
    except Exception:
        return Markup("")


templates.env.filters["fam_avatar"] = fam_avatar

app.include_router(attachments_router, dependencies=[Depends(api_key_auth)])
app.include_router(providers_router, dependencies=[Depends(api_key_auth)])
app.include_router(charges_router, dependencies=[Depends(api_key_auth)])
app.include_router(payments_router, dependencies=[Depends(api_key_auth)])
app.include_router(premiums_router, dependencies=[Depends(api_key_auth)])
app.include_router(imports_router, dependencies=[Depends(api_key_auth)])
app.include_router(pages_router)
app.include_router(family_members_router, dependencies=[Depends(api_key_auth)])
app.include_router(insurance_router, dependencies=[Depends(api_key_auth)])
app.include_router(claims_router, dependencies=[Depends(api_key_auth)])


# Configure basic logging for startup diagnostics
import logging

logger = logging.getLogger("personal_expenses")
if not logger.handlers:
    # Configure a simple handler if none configured by the process
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
# Allow enabling debug logs via APP_DEBUG env var (useful for troubleshooting)
if str(os.getenv("APP_DEBUG", "False")).lower() in ("1", "true", "yes"):
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def request_attribution_middleware(request: Request, call_next):
    """Attach request correlation metadata and log API mutations.

    This middleware keeps endpoint code unchanged while ensuring agent-driven
    writes are auditable.
    """
    request_id = (request.headers.get(REQUEST_ID_HEADER_NAME) or "").strip() or str(
        uuid4()
    )
    agent_id = (request.headers.get(AGENT_ID_HEADER_NAME) or "").strip() or "anonymous"

    request.state.request_id = request_id
    request.state.agent_id = agent_id

    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER_NAME] = request_id

    path = request.url.path
    # Issue the UI session cookie on server-rendered pages (not API/static)
    if (
        request.method == "GET"
        and not path.startswith(("/api", "/static", "/admin"))
        and response.status_code == 200
        and request.cookies.get(UI_SESSION_COOKIE) != get_ui_session_token()
    ):
        response.set_cookie(
            UI_SESSION_COOKIE,
            get_ui_session_token(),
            httponly=True,
            samesite="strict",
            path="/",
        )
    if path.startswith("/api") and request.method in MUTATING_METHODS:
        logger.info(
            "API mutation request_id=%s agent_id=%s method=%s path=%s status=%s",
            request_id,
            agent_id,
            request.method,
            path,
            response.status_code,
        )

    return response


# Startup event: test DB connectivity and log result so container logs are helpful
@app.on_event("startup")
def startup_check_db():
    try:
        # call the module-level helper which executes a simple query
        try:
            from database_config import test_connection
        except Exception:
            from .database_config import test_connection  # type: ignore
        ok = test_connection()
        if ok:
            print("\u2705 Database connectivity test passed on startup")
        else:
            print("\u26A0\ufe0f Database connectivity test failed on startup")
    except Exception as e:
        print(f"\u274c Exception during DB startup test: {e}")
    # Log environment and non-sensitive DB configuration for diagnostics
    try:
        from database_config import db_config
    except Exception:
        from .database_config import db_config  # type: ignore

    logger.info("Application ENV_MODE=%s", getattr(db_config, "env_mode", "UNKNOWN"))
    # Log DB connection details without sensitive info (no password)
    logger.info(
        "Database config: host=%s port=%s db=%s user=%s",
        getattr(db_config, "db_host", "<unknown>"),
        getattr(db_config, "db_port", "<unknown>"),
        getattr(db_config, "db_name", "<unknown>"),
        getattr(db_config, "db_user", "<unknown>"),
    )
    if os.getenv("APP_API_KEY") or os.getenv("APP_API_KEYS"):
        logger.info("API key auth is enabled for /api routes")
    else:
        logger.warning(
            "API key auth is disabled (set APP_API_KEY or APP_API_KEYS to enable)"
        )
    logger.info("CORS allowlist: %s", ", ".join(cors_origins))


@app.get("/api/schema.json", include_in_schema=False)
def api_schema():
    """Expose OpenAPI schema at a stable API path for agent tool discovery."""
    return app.openapi()


## HTML page routes moved to pages router (api/pages.py)


# --------------------------- Diagnostics: Money Columns ---------------------------
@app.get("/admin/db/money-columns", dependencies=[Depends(api_key_auth)])
def list_money_columns(db: Session = Depends(get_db)):
    """Diagnostic endpoint: list columns still using PostgreSQL 'money' type (OID 790) or domains wrapping it.

    Helps explain recurring 'Unknown PG numeric type: 790' errors when SQLAlchemy cannot map legacy money columns.
    """
    from sqlalchemy import text

    # Query pg_catalog to find columns whose atttypid resolves to 'money'
    sql = text(
        """--sql
        SELECT
            n.nspname AS schema,
            c.relname AS table,
            a.attname AS column,
            t.typname AS type_name,
            a.atttypid AS type_oid
        FROM pg_attribute a
        JOIN pg_class c ON a.attrelid = c.oid
        JOIN pg_namespace n ON c.relnamespace = n.oid
        JOIN pg_type t ON a.atttypid = t.oid
        WHERE a.attnum > 0
          AND NOT a.attisdropped
          AND t.oid = 790 -- OID for money
        ORDER BY n.nspname, c.relname, a.attname
        """
    )
    try:
        rows = db.execute(sql).fetchall()
    except Exception as ex:
        raise HTTPException(status_code=500, detail=f"Failed to query catalog: {ex}")
    results = [
        {
            "schema": r.schema,
            "table": r.table,
            "column": r.column,
            "type_name": r.type_name,
            "type_oid": int(r.type_oid),
        }
        for r in rows
    ]
    return {"money_columns": results, "count": len(results)}


## HTML payment/detail/premiums pages moved to pages router


if __name__ == "__main__":
    import os

    import uvicorn

    port = int(os.getenv("APP_PORT", "8080"))
    uvicorn.run(app, host="0.0.0.0", port=port)
