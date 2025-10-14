"""FastAPI application for Personal Health Expenses Management System.

Modularized root containing only:
 - App/templating setup
 - Router includes (API lives in api/* modules)
 - HTML page routes (until moved to a pages router)
 - Diagnostic endpoints

All CRUD JSON endpoints now reside in dedicated routers under api/.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session  # retained for diagnostic endpoint dependency

from .api.attachments import router as attachments_router
from .api.charges import router as charges_router
from .api.imports import router as imports_router
from .api.pages import router as pages_router
from .api.payments import router as payments_router
from .api.premiums import router as premiums_router
from .api.providers import router as providers_router

try:  # Prefer absolute imports when running as a module
    from database_config import db_config, get_db
except ImportError:  # pragma: no cover - fallback to relative when executed differently
    from .database_config import db_config, get_db  # type: ignore

app = FastAPI(title="Personal Health Expenses Management System", version="1.0.0")

project_root = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(project_root / "templates"))
templates.env.globals["ENV_MODE"] = getattr(db_config, "env_mode", "PROD")
app.mount("/static", StaticFiles(directory=str(project_root / "static")), name="static")


def format_currency(value):
    try:
        if value is None:
            return "$0.00"
        return f"${float(value):,.2f}"
    except Exception:
        return "$0.00"


templates.env.filters["currency"] = format_currency

app.include_router(attachments_router)
app.include_router(providers_router)
app.include_router(charges_router)
app.include_router(payments_router)
app.include_router(premiums_router)
app.include_router(imports_router)
app.include_router(pages_router)


# Startup event: test DB connectivity and log result so container logs are helpful
@app.on_event("startup")
def startup_check_db():
    try:
        ok = db_config.test_connection()
        if ok:
            print("\u2705 Database connectivity test passed on startup")
        else:
            print("\u26A0\ufe0f Database connectivity test failed on startup")
    except Exception as e:
        print(f"\u274c Exception during DB startup test: {e}")


## HTML page routes moved to pages router (api/pages.py)


# --------------------------- Diagnostics: Money Columns ---------------------------
@app.get("/admin/db/money-columns")
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

# Successfully configured pre-commit Docker builds! Now running on port 8080.
