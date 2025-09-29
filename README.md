# Personal Health Expenses Management System

A comprehensive CRUD application for managing personal medical, dental, and vision expenses built with FastAPI and PostgreSQL.

## Features

- **Provider Management**: Track healthcare providers, doctors, and medical facilities
- **Charge Tracking**: Record medical charges, bills, and insurance claims
- **Payment Management**: Track payments made to healthcare providers
- **Premium Monitoring**: Monitor yearly insurance premiums and HSA/FSA contributions
- **Web Interface**: Easy-to-use web interface for data management
- **REST API**: Full REST API for programmatic access
- **Database Integration**: PostgreSQL database with configurable connections

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

- Python 3.13+
- PostgreSQL database
- pip package manager

### Setup

1. **Clone or download the project**

2. **Install dependencies**:
   ```bash
   pip install -e .
   ```

3. **Database Setup**:
   - Create a PostgreSQL database named `personal_expenses`
   - Update database credentials in `.env` file

4. **Configure Environment**:
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` file with your database credentials:
   ```
   DB_HOST=localhost
   DB_PORT=5432
   DB_NAME=personal_expenses
   DB_USER=your_username
   DB_PASSWORD=your_password
   ```

5. **Initialize Database**:
   ```bash
   python init_db.py
   ```
   
   This will create the database tables and optionally load sample data.

## Usage

### Starting the Application

```bash
# Using the entry point script (recommended)
python run_app.py

# Or using uv
uv run run_app.py
```

The application will be available at:
- Web Interface: http://localhost:8000
- API Documentation: http://localhost:8000/docs
- Alternative API Docs: http://localhost:8000/redoc

### Web Interface

Navigate to http://localhost:8000 to access the web interface:

- **Home**: Overview and quick navigation
- **Providers**: Manage healthcare providers
- **Charges**: Track medical charges and bills
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

### Data Model

The application uses the following enums for data consistency:

**Family Members**: Alex, Jordan, Sam, Riley

**Medical Types**: Medical, Dental, Vision, Mental Health, Other

**Expense Types**: Medical, Dental, Vision, HSA / FSA

## Development

### Project Structure

```
personal-expenses/
├── src/
│   ├── models/          # Database models
│   ├── crud/            # CRUD operations
│   ├── schemas/         # Pydantic schemas
│   ├── app.py          # FastAPI application
│   └── database_config.py # Database configuration
├── templates/           # HTML templates
├── init_db.py          # Database initialization
├── pyproject.toml      # Project configuration
└── README.md
```

### Adding New Features

1. **Models**: Add new SQLAlchemy models in `src/models/`
2. **CRUD**: Implement CRUD operations in `src/crud/`
3. **Schemas**: Define Pydantic schemas in `src/schemas/`
4. **API**: Add endpoints in `src/app.py`
5. **Templates**: Create HTML templates in `templates/`

## License

This is a personal project for managing health expenses.

## Contributing

This is a personal project, but suggestions and improvements are welcome.