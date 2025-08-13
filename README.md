# README

## Kudwa Test - Hasan

### Overview

The Kudwa Chatbot integrates multiple financial data sources (QuickBooks-style and Rootfi-style JSON) into a unified SQLite-backed backend. It offers a REST API for structured data access and an LLM-powered Natural Language Query (NLQ) interface for financial insights.

### Features

- **Data Integration:** Parse and normalize multiple financial data formats.
- **Database Storage:** Store processed data in SQLite for fast queries.
- **API Access:** FastAPI endpoints for direct and filtered data retrieval.
- **AI Querying:** Ask financial questions in natural language, get data-backed answers.
- **Containerized Deployment:** Run locally or deploy via Docker and Render.

### Technology Stack

- **Backend:** FastAPI (Python)
- **Database:** SQLite
- **AI/LLM:** OpenAI API (`gpt-4o-mini` by default)
- **Containerization:** Docker, docker-compose
- **Deployment:** Render

### Setup & Installation

#### Prerequisites

- Python 3.10+
- OpenAI API Key
- Docker (optional, for containerized deployment)

#### Environment Variables

- `OPENAI_API_KEY` (required)
- `OPENAI_MODEL` (default: `gpt-4o-mini`)
- `LOG_LLM` (optional, 0/1)

#### Local Development

You can run the project manually or by using the provided Makefile.
#### Manual Run
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill with your config
uvicorn app.main:app --reload
```

#### Makefile
The makefile wraps common docker compose operations. Key variables:
- COMPOSE (default: docker compose)
- COMPOSE_FILE (default: docker-compose.yml)
- SERVICE (default: chatbot) – service name in compose
- CONTAINER (default: chatbot) – container_name in compose
- ENV_FILE (default: .env)

Common commands:
```bash
make help          # List all targets
make env           # Create .env from .env.example if missing
make build         # Build images (checks .env exists)
make up            # Start stack in background
make logs          # Follow application logs
make ps            # Show running containers
make shell         # Open a shell inside the app container
make restart       # Recreate containers
make rebuild       # Rebuild without cache + recreate
make prune         # Prune unused Docker data (safe-ish)
make nuke          # Down + remove volumes 
make image-id      # Print image id for the service
make port          # Show published ports for the container
make raw-tree      # Inspect raw image filesystem (no host mounts)
```

Visit [http://localhost:8000/docs](http://localhost:8000/docs) for the API docs.

#### Docker Deployment

```bash
docker compose up --build
```

### API Endpoints

- `GET /health` – Health check
- `POST /chat` – Send a natural language query and receive results

### POST /chat Request Body:
```bash
{
  "session_id": "<client-generated conversation id>",
  "message": "Your question here",
  "context": {"optional_key": "optional_value"} # This is automatically filled with date information or whatever extra context we may need
}
```


### AI/ML Workflow

- LLM uses tool functions (`tool_list_tables`, `tool_describe_table`, `tool_run_sql`, etc.) to inspect the schema and generate safe SQL queries.
- SQL results are combined with narrative explanations for end users.
- The current date is injected into prompts via the context variable to avoid stale interpretations.

### Logging

- LLM logs can be enabled via `LOG_LLM=1` to trace tool usage, SQL queries, and token counts.

