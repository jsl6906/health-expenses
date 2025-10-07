# Personal Health Expenses Management System

A modular FastAPI application for managing personal medical, dental, and vision expenses backed by PostgreSQL.

## Features

- **Provider Management**: Track healthcare providers, doctors, and medical facilities
- **Charge Tracking**: Record medical charges, bills, and insurance claims
- **Payment Management**: Track payments made to healthcare providers
- **Premium Monitoring**: Monitor yearly insurance premiums and HSA/FSA contributions
- **Web Interface**: HTML/Jinja + Bootstrap UI (separate pages router)
- **REST API**: Versioned JSON endpoints via modular routers
- **Database Integration**: PostgreSQL database with configurable connections
- **Bulk Manual Charge Entry**: Add multiple charges at once with shared header fields and optional attachment (applied to highest charge)
- **FHIR JSON Import**: Upload a FHIR Bundle (ExplanationOfBenefit / Claim) to create charges while skipping duplicates

## Technology Stack

- **Backend**: FastAPI (Python)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Frontend**: HTML/CSS/JavaScript with Bootstrap
- **Configuration**: Environment-based configuration with python-dotenv

## Database Schema

The application manages the following entities:

- **Providers**: Healthcare providers and facilities
- **Charges**: Medical charges and bills
- **Payments**: Payments made for medical services
- **Premiums**: Insurance premiums and HSA/FSA contributions
- **Charge-Payment Cross-Reference**: Links charges to payments

## Installation

### Prerequisites

- Python 3.11+ (project uses uv for dependency & venv management)
- PostgreSQL (or compatible) database
- [uv](https://github.com/astral-sh/uv) installed (preferred over pip as per `AGENTS.md`)

### Setup

1. **Clone or download the project**

2. **Install dependencies (managed by uv)**:

   ```bash
   uv sync
   ```

3. **Database Setup**:
   - Create a PostgreSQL database named `personal_expenses`
   - Update database credentials in `.env` file

4. **Configure Environment**:

   ```bash
   cp .env.example .env
   ```

   Edit `.env` file with your database credentials:

   ```bash
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=personal_expenses
   DB_USER=your_username
   DB_PASSWORD=your_password
   ```

5. **Initialize Database**:

   ```bash
   uv run init_db.py
   ```

   This creates tables and optionally loads seed/sample data.

## Usage

### Starting the Application

```bash
# Recommended (uv ensures correct environment)
uv run run_app.py

# Or (if you have an active venv)
python run_app.py
```

The application will be available at:

- Web Interface: <http://localhost:8000>
- API Documentation: <http://localhost:8000/docs>
- Alternative API Docs: <http://localhost:8000/redoc>

### Web Interface

Navigate to <http://localhost:8000> to access the web interface:

- **Home**: Overview and quick navigation
- **Providers**: Manage healthcare providers
- **Charges**: Track medical charges and bills
  - Use "Add Multiple Charges" to input several line items sharing Service Date, Family Member, Type, Provider, and optional Doctor. An optional attachment (e.g., invoice PDF) is automatically linked to the highest Charged Amount line.
- **Payments**: Record payments
- **Premiums**: Monitor insurance premiums

### API Usage

The REST API provides full CRUD operations for all entities:

#### Providers

- `GET /api/providers/` - List all providers
- `POST /api/providers/` - Create new provider
- `GET /api/providers/{id}` - Get provider by ID
- `PUT /api/providers/{id}` - Update provider
- `DELETE /api/providers/{id}` - Delete provider

#### Charges

- `GET /api/charges/` - List all charges
- `POST /api/charges/` - Create new charge
- `GET /api/charges/{id}` - Get charge by ID
- `PUT /api/charges/{id}` - Update charge
- `DELETE /api/charges/{id}` - Delete charge
- `POST /api/charges/bulk-create` - Create multiple charges (multipart/form-data). Required fields: `service_date`, `family_member`, `medical_type`, `provider_id`, `rows_json` (JSON array of objects with keys like description, cpt_code, charged_amount, owed_amount, base_fee_amount, insurance_paid_amount, insurance_discount_amount, reimbursement_amount). Optional: `doctor_name`, `eob_claim_num`, `attachment` (file applied to highest charged_amount).
- `POST /api/charges/import-json` - Import charges from a FHIR Bundle JSON file (multipart). Form fields: `file` (+ optional `provider_id`, `family_member`, `medical_type`). Returns counts and row-level skipped/duplicate reasons.

#### Payments

- `GET /api/payments/` - List all payments
- `POST /api/payments/` - Create new payment
- `GET /api/payments/{id}` - Get payment by ID
- `PUT /api/payments/{id}` - Update payment
- `DELETE /api/payments/{id}` - Delete payment

#### Premiums

- `GET /api/premiums/` - List all premiums
- `POST /api/premiums/` - Create new premium
- `GET /api/premiums/{id}` - Get premium by ID
- `PUT /api/premiums/{id}` - Update premium
- `DELETE /api/premiums/{id}` - Delete premium

## Configuration

### Environment Variables

- `DB_HOST`: Database host (default: localhost)
- `DB_PORT`: Database port (default: 5432)
- `DB_NAME`: Database name (default: personal_expenses)
- `DB_USER`: Database username
- `DB_PASSWORD`: Database password
- `DB_POOL_SIZE`: Connection pool size (default: 5)
- `DB_MAX_OVERFLOW`: Max connection overflow (default: 10)
- `DB_ECHO`: Enable SQL logging (default: false)
- `ENV_MODE`: Runtime environment mode (`PROD` or `TEST`, default `PROD`).

When `ENV_MODE=TEST`, the application attempts to use the following prefixed variables first:

- `TEST_DB_HOST`
- `TEST_DB_PORT`
- `TEST_DB_NAME`
- `TEST_DB_USER`
- `TEST_DB_PASSWORD`

When `ENV_MODE=PROD` (or unset), the application prefers:

- `PROD_DB_HOST`
- `PROD_DB_PORT`
- `PROD_DB_NAME`
- `PROD_DB_USER`
- `PROD_DB_PASSWORD`

If any of the prefixed variables are missing, it falls back to the generic `DB_*` keys.

UI Indicator: In `TEST` mode a yellow banner appears at the top of every page: "TEST ENVIRONMENT - DATA IS NON-PRODUCTION".

Safety: If `ENV_MODE=TEST` but the resolved host is not `localhost` or `127.0.0.1`, a warning is printed to stdout to help prevent accidental use of production hosts.

### Data Model

The application uses the following enums for data consistency:

**Family Members**: Alex, Jordan, Sam, Riley

**Medical Types**: Medical, Dental, Vision, Mental Health, Other

**Expense Types**: Medical, Dental, Vision, HSA / FSA

## Development

### Modular Project Structure (post-refactor)

```text
personal-expenses/
├── src/
│   ├── app.py                  # Thin bootstrap: mounts static, includes routers, diagnostics
│   ├── api/                    # Modular API & page routers
│   │   ├── attachments.py      # /api/attachments* endpoints
│   │   ├── providers.py        # /api/providers* endpoints
│   │   ├── charges.py          # /api/charges* endpoints (CRUD, bulk, apply-payment)
│   │   ├── payments.py         # /api/payments* endpoints
│   │   ├── premiums.py         # /api/premiums* endpoints
│   │   ├── imports.py          # Import preview/commit (FHIR + JSON + EOB sessions)
│   │   └── pages.py            # Server-rendered HTML routes (/, providers, charges, etc.)
│   ├── services/
│   │   ├── import_parsing.py   # FHIR & heuristic JSON parsing helpers
│   │   ├── import_session.py   # In-memory JSON import session store & Pydantic models
│   │   └── allocation.py       # Even payment allocation & open charge helpers
│   ├── crud/                   # CRUD abstractions (provider_crud, charge_crud, etc.)
│   ├── models/                 # SQLAlchemy ORM models
│   ├── schemas/                # Pydantic schemas (API models)
│   ├── database_config.py      # DB engine & session + ENV_MODE logic
│   └── llm/                    # LLM-based EOB parsing components
├── templates/                  # Jinja2 templates
├── static/                     # CSS/JS/static assets
├── init_db.py                  # Database initialization script
├── migrate_simple.py           # Simple migration/refresh helper (if used)
├── run_app.py                  # Entrypoint script (uvicorn launcher)
└── pyproject.toml              # Dependencies & project metadata
```

Key architectural themes:

- Separation of concerns: routers vs services vs CRUD vs models.
- `app.py` intentionally minimal: no business logic or HTML handlers.
- Reusable allocation & import parsing logic centralized in `services/`.
- Session-based import preview workflow isolates transient state from DB commits.

### Adding New Features

1. **Models**: Add new SQLAlchemy models in `src/models/`
2. **CRUD**: Implement CRUD operations in `src/crud/`
3. **Schemas**: Define Pydantic schemas in `src/schemas/`
4. **Routers**: Add or modify endpoints in a new/existing module under `src/api/` (register via `app.include_router` in `app.py`).
5. **Services**: Place cross-cutting or complex logic in `src/services/` for reuse & testability.
6. **Templates**: Add HTML templates in `templates/`; expose via `pages.py` or a new pages router.
7. **Static Assets**: Add CSS/JS in `static/`; they are served at `/static/*`.

### LLM-Based EOB Parsing (Experimental)

The EOB import workflow uses a Gemini LLM to extract structured line items from uploaded EOB documents.

#### Enable

1. Install new dependencies (already declared in `pyproject.toml`):
   - `google-generativeai`
   - `pypdf`
2. Copy `.env.example` to `.env` if you haven't already and set:

   ```bash
   GEMINI_API_KEY=your_key_here
   ```

3. Restart the application.

If the key is missing or the call fails, the session will load with zero rows (no legacy mock data is generated).

#### How It Works (High Level)

- File bytes are inspected; if PDF, text is extracted with `pypdf` (limited pages for cost control).
- A concise prompt instructs Gemini to return ONLY a JSON array of charge objects.
- The response is sanitized and normalized (dates parsed, numeric coercion, length limiting) before populating the session.

#### Limitations / Notes

- Output quality depends on document clarity; ambiguous lines may produce null fields.
- Current model defaults to `gemini-1.5-flash`. Override with `GEMINI_MODEL_NAME` in `.env`.
- Max ~20KB of extracted text is sent to control token usage.
- No PHI redaction is performed—only use with documents you are comfortable processing via the Gemini API.

#### Extending

- Add heuristic post-processing (e.g., CPT validation against existing DB codes).
- Implement duplicate detection by hashing (date, amount, CPT).
- Capture model latency & token metrics for observability.

## Housekeeping & Maintenance

### Code Style & Tools

- Dependency & environment management via `uv` (avoid raw `pip install`).
- Multi-line SQL strings should be prefixed with a `--sql` comment inside triple quotes (see `AGENTS.md`).

### Allocation Logic

- Central even-split allocation of payments across charges lives in `services/allocation.py`.
- When adding new reporting endpoints, import and reuse helpers (avoid duplicating split logic).

### Import / Preview Workflow

- Preview JSON/FHIR uploads → session (in-memory) → selective commit of rows & provider candidates.
- Extensible: add new parsers in `import_parsing.py` and register heuristics.

### ENV Modes

- `ENV_MODE=TEST` surfaces a banner (see template globals) and uses prefixed DB credentials if provided. Helpful for local destructive testing.

## License & Contributions

Personal project; suggestions and improvements welcome via issues or PRs.
