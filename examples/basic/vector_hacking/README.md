# Vector Hacking

A demonstration of **vector inversion attacks** using LLMs with MDB_ENGINE.

This example shows how an attacker can potentially reverse-engineer hidden text by measuring vector distances - highlighting the security implications of embedding-based systems.

## Quick Start

```bash
# 1. Set your Azure OpenAI credentials
export AZURE_OPENAI_API_KEY=your-key
export AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com/

# 2. Start everything
docker-compose up
```

**Access:**

| Service | URL |
|---------|-----|
| UI | http://localhost:3000 |
| API | http://localhost:8000 |
| Swagger Docs | http://localhost:8000/docs |

## Project Structure

```
vector_hacking/
├── docker-compose.yml    # Full stack orchestration
├── api/                  # FastAPI backend
│   ├── main.py          # API endpoints with Swagger
│   ├── service.py       # Vector hacking logic
│   ├── schemas.py       # Pydantic models
│   └── Dockerfile
└── ui/                   # Static frontend
    ├── index.html       # Cyberpunk UI
    ├── config.js        # API URL configuration
    └── Dockerfile
```

## Architecture

```
┌─────────────────┐
│       UI        │ ◄── Static HTML/CSS/JS (nginx)
│  localhost:3000 │
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│      API        │ ◄── FastAPI + Swagger
│  localhost:8000 │
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌───────┐ ┌────────────┐
│MongoDB│ │Azure OpenAI│
│ Atlas │ │ (LLM + Emb)│
│ Local │ └────────────┘
└───────┘
```

## How It Works

1. **Target Vector**: A secret phrase is embedded into a vector
2. **LLM Guessing**: An AI generates guesses for what the text might be
3. **Error Feedback**: Each guess is embedded and compared to the target
4. **Convergence**: The LLM uses error feedback to improve guesses
5. **Success**: When vector distance drops below threshold, the attack succeeds

## API Reference

See full interactive docs at http://localhost:8000/docs

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check |
| `/api/attack/start` | POST | Start attack with target |
| `/api/attack/stop` | POST | Stop current attack |
| `/api/attack/status` | GET | Get attack progress |

### Example: Start an Attack

```bash
curl -X POST http://localhost:8000/api/attack/start \
  -H "Content-Type: application/json" \
  -d '{"target": "Hello world"}'
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_API_KEY` | Yes | - | Azure OpenAI API key |
| `AZURE_OPENAI_ENDPOINT` | Yes | - | Azure OpenAI endpoint |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | No | `gpt-4o` | Chat model |
| `API_PORT` | No | `8000` | API port |
| `UI_PORT` | No | `3000` | UI port |

## Running Components Individually

**API only:**
```bash
docker-compose up api mongodb
```

**UI only (with external API):**
```bash
cd ui
# Edit config.js to point to your API
python -m http.server 3000
```

## Security Implications

This demo illustrates why you should:
- Protect embedding vectors from side-channel attacks
- Not expose error messages revealing vector distances
- Use proper access controls on vector databases
- Understand security risks of embedding-based systems

## See Also

- [MDB_ENGINE Documentation](../../../mdb_engine/README.md)
- [API README](./api/README.md) - Detailed API documentation
- [UI README](./ui/README.md) - Frontend customization

