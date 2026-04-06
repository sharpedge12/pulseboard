# PulseBoard — Real-Time Discussion Forum

A monorepo internship project for a real-time discussion forum with threaded conversations, direct and group chat, notifications, moderation, OAuth2 login, an AI bot (@pulse), and a polished React frontend.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy 2, PostgreSQL 16, Redis 7
- **Frontend**: React 18, Vite 6, JavaScript, plain CSS
- **Auth**: JWT, Google OAuth2, GitHub OAuth2, email verification
- **AI Bot**: Groq Compound Mini (built-in web search) + Tavily search
- **Infra**: Docker Compose (microservice topology), MailHog for local email testing
- **Testing**: pytest (pytest-asyncio, httpx)

## Architecture

3 backend services + API gateway (consolidated from 7 services — see [ADR-0001](docs/adr/0001-consolidate-microservices.md)):

```
services/
  shared/           # Common library (pip package): models, schemas, auth, DB, Redis, config
  gateway/          # API Gateway (port 8000): reverse proxy + WebSocket hub, CORS
  core/             # Core Service (port 8001): auth, users, uploads, notifications
  community/        # Community Service (port 8002): forum, moderation, search, admin, chat
frontend/           # React SPA (talks to gateway at :8000)
docs/               # Architecture, HLD, LLD, ADR, deployment, API reference, flow diagrams
```

## Local Development

1. Copy `.env.example` to `.env` and fill in secrets (OAuth credentials, Groq API key, etc.)

2. Start the full stack:

```bash
docker compose up --build
```

3. Open:
   - Frontend: `http://localhost:5173`
   - API Gateway docs: `http://localhost:8000/docs`
   - MailHog (email UI): `http://localhost:8025`

## Running Tests

Tests use SQLite and don't require Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e services/shared
pip install -r services/core/requirements.txt -r services/community/requirements.txt
timeout 300 python -m pytest services/tests/test_auth.py services/tests/test_forum.py -x -v --tb=short -k "not subscribe"
rm -f test_services.db
```

## Frontend Commands

```bash
cd frontend
npm install
npm run dev -- --host 0.0.0.0 --port 5173
npm run build
```

## Documentation

- [Architecture Overview](docs/architecture.md)
- [High-Level Design](docs/high-level-design.md)
- [Low-Level Design](docs/low-level-design.md)
- [ADR-0001: Service Consolidation](docs/adr/0001-consolidate-microservices.md)
- [Database Design](docs/database-design.md)
- [API Reference](docs/api-reference.md)
- [Frontend Architecture](docs/frontend-architecture.md)
- [Flow Diagrams](docs/flow-diagrams.md)
- [Role & Permissions](docs/role-permissions.md)
- [Deployment](docs/deployment.md)
