# Agentic System Skeleton

Starter app for a local multi-agent chat backend + web UI.


## Quick Start

1. Create and activate virtual environment (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

Optional (advanced agent planning loop with LangGraph backend):

```powershell
pip install langgraph
```

3. Configure `.env` (project root):

```env
WEB_APP_URL=http://127.0.0.1
WEB_APP_PORT=8000
APP_LOG_LEVEL=INFO
LOG_RETENTION=7
LAST_INTERACTION_THRESHOLD=7
SQL_PROVIDER=sqlite
LOCAL_API_URL=http://127.0.0.1:8081/answer
LOCAL_API_TIMEOUT_SECONDS=20
```

4. Run API server:

```powershell
uvicorn src.app.api.main:app --reload
```

Or production-style multi-worker run:

```powershell
python run.py
```

5. Open the web app:

- `http://127.0.0.1:8000/`
- Register user, then log in.

6. Verify app endpoints:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/agents`

7. Optional API test:

```powershell
curl -X POST "http://127.0.0.1:8000/chat" -H "Content-Type: application/json" -d "{\"message\":\"Find customer CUST-100\",\"session_id\":\"demo\"}"
```
