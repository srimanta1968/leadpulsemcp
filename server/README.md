# Leadpulse MCP

## Technology Stack

- **Language**: python
- **Framework**: fastapi
- **Database**: mongodb

## Getting Started

### Prerequisites

- Python 3.9+
- pip or pipenv

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Copy environment file
cp .env.example .env

# Start database (Docker)
docker-compose up -d

# Run migrations
alembic upgrade head

# Start development server
uvicorn app.main:app --reload
```

The server will be running at http://localhost:8000

## Available Commands

| Command | Description |
|---------|-------------|
| `pip install -r requirements.txt` | Install dependencies |
| `uvicorn app.main:app --reload` | Start development server |
| `echo "No build step required"` | Build for production |
| `uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4` | Start production server |
| `pytest` | Run tests |
| `alembic upgrade head` | Run database migrations |
| `ruff check .` | Lint code |

## Project Structure

See the generated folder structure for detailed organization.

## Environment Variables

Copy `.env.example` to `.env` and configure:

- Database connection settings
- JWT secrets
- API keys
- Other configuration

## License

MIT
