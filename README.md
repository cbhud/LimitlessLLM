# LimitlessLLM Proxy — Integrate AI in Seconds, Completely Free

> **One endpoint. Every free-tier LLM. Zero code changes.**

A lightweight, high-performance proxy that exposes a single **OpenAI-compatible API** and intelligently routes your requests across multiple free-tier LLM providers — with automatic fallback, rate-limit tracking, and sticky sessions out of the box.

| Feature | Description |
|---|---|
|  **OpenAI-compatible API** | Drop-in replacement — works with any OpenAI SDK or tool |
|  **Automatic fallback** | Seamlessly moves to the next model when one is rate-limited or unavailable |
|  **Smart routing** | Priority-based chain with dynamic penalty scoring on `429` responses |
|  **Key rotation** | Round-robin across multiple API keys per provider |
|  **Sticky sessions** | Multi-turn conversations stay on the same model for coherent context |

---

## Quick Start

### 1. Start the server

**Windows:**
```bat
run.bat
```

**Linux / macOS:**
```bash
chmod +x run.sh && ./run.sh
```

The script creates a virtual environment, installs dependencies, copies `.env.example` → `.env`, and starts the server on port `3001`.

> **Manual / Docker setup?** See [Installation](#installation) below.

### 2. Add your API keys

Open `.env` and fill in whichever providers you have keys for. Leave the rest blank — they're silently skipped.

```env
UNIFIED_API_KEY=change-me      # the token your clients use to access the proxy

GOOGLE_API_KEY=
GROQ_API_KEY=
# ... add any others you have
```

### 3. Send your first request

**Python (OpenAI SDK):**
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:3001/v1",
    api_key="change-me",   # your UNIFIED_API_KEY
)

response = client.chat.completions.create(
    model="auto",   # proxy picks the best available model
    messages=[{"role": "user", "content": "Hello, world!"}]
)

print(response.choices[0].message.content)
```

**cURL:**
```bash
curl http://localhost:3001/v1/chat/completions \
  -H "Authorization: Bearer change-me" \
  -H "Content-Type: application/json" \
  -d '{"model": "auto", "messages": [{"role": "user", "content": "Hello!"}]}'
```

Use `model="auto"` to let the proxy pick the best available model, or pass a specific `display_name` from your config (e.g. `"Gemini 2.5 Flash"`).

---

## Configuration

Everything is controlled by two files: `.env` for secrets and `config.yaml` for everything else.

### Provider API Keys (`.env`)

```env
UNIFIED_API_KEY=change-me

GOOGLE_API_KEY=
GROQ_API_KEY=
GROQ_API_KEY_2=              # add multiple keys for round-robin rotation
CEREBRAS_API_KEY=
SAMBANOVA_API_KEY=
NVIDIA_API_KEY=
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
GITHUB_API_KEY=
COHERE_API_KEY=
CLOUDFLARE_API_KEY=          # Format: "account_id:api_token"
HUGGINGFACE_API_KEY=
OLLAMA_API_KEY=
```

You only need keys for providers you've added to `config.yaml`.

### Fallback Chain (`config.yaml`)

The `fallback_chain` list defines which models the proxy tries and in what order. Models higher in the list have higher priority.

```yaml
providers:
  google:
    keys:
      - "${GOOGLE_API_KEY}"
  groq:
    keys:
      - "${GROQ_API_KEY}"
      - "${GROQ_API_KEY_2}"   # multiple keys → round-robin rotation

fallback_chain:

  - platform: google
    model_id: gemini-2.5-flash
    display_name: Gemini 2.5 Flash
    enabled: true
    limits: { rpm: 10, rpd: 1500, tpm: 250000, tpd: null }

  - platform: groq
    model_id: meta-llama/llama-4-scout-17b-16e-instruct
    display_name: Llama 4 Scout
    enabled: true
    limits: { rpm: 30, rpd: 14400, tpm: 6000, tpd: null }
```

**To add a model**, append an entry to `fallback_chain`. Set `enabled: false` to temporarily disable one without deleting it.

#### Fallback Chain Fields

| Field | Required | Description |
|---|---|---|
| `platform` | ✅ | Provider name — must match a key under `providers:` |
| `model_id` | ✅ | The model identifier sent to the provider's API |
| `display_name` | ✅ | Human-readable name shown in `/v1/models` |
| `enabled` | ❌ | Set to `false` to temporarily disable without deleting |
| `limits.rpm` | ❌ | Max requests per minute (`null` = unlimited) |
| `limits.rpd` | ❌ | Max requests per day (`null` = unlimited) |
| `limits.tpm` | ❌ | Max tokens per minute (`null` = unlimited) |
| `limits.tpd` | ❌ | Max tokens per day (`null` = unlimited) |

### Server Settings (`config.yaml`)

```yaml
server:
  host: "0.0.0.0"
  port: 3001
  log_level: "info"    # debug | info | warning | error

unified_api_key: "change-me"   # overridden by UNIFIED_API_KEY env var
```

### Supported Providers

The following platforms have built-in adapters. Enable any of them by adding the matching key to `.env` and entries to `config.yaml` — **no code changes needed**.

| Platform | Provider | Adapter Type |
|---|---|---|
| `google` | Google Gemini | Native Gemini API |
| `groq` | Groq | OpenAI-compatible |
| `cerebras` | Cerebras | OpenAI-compatible |
| `sambanova` | SambaNova | OpenAI-compatible |
| `nvidia` | NVIDIA NIM | OpenAI-compatible |
| `mistral` | Mistral AI | OpenAI-compatible |
| `openrouter` | OpenRouter | OpenAI-compatible |
| `github` | GitHub Models | OpenAI-compatible |
| `cohere` | Cohere | Native Cohere API |
| `cloudflare` | Cloudflare Workers AI | Custom URL structure |
| `huggingface` | HuggingFace Router | OpenAI-compatible |
| `ollama` | Ollama Cloud | OpenAI-compatible |

#### Adding a brand-new OpenAI-compatible provider

For any provider **not listed above**, add a one-liner to the `_OPENAI_COMPAT_PLATFORMS` registry in `app/providers/__init__.py`:

```python
"myprovider": {"name": "My Provider", "base_url": "https://api.myprovider.com/v1"},
```

Then add your key to `.env` and an entry to `config.yaml` — no other code changes needed.

---

## Installation

### Option 1: Run Script (Recommended)

**Windows:**
```bat
run.bat
```

**Linux / macOS:**
```bash
chmod +x run.sh
./run.sh
```

### Option 2: Manual Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install -r requirements.txt
cp .env.example .env             # then edit .env and add your API keys

uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload
```

### Option 3: Docker

```bash
cp .env.example .env   # edit .env and add your API keys
docker compose up -d --build
```

The container listens on port `3001` by default (configurable in `config.yaml`).

---

## Internals

### Architecture

```
Your App / cURL / OpenAI SDK
        │
        ▼  POST /v1/chat/completions
┌─────────────────────────────┐
│       FastAPI (port 3001)   │
├─────────────────────────────┤
│  Router (services/router)   │
│  ┌──────────────────────┐   │
│  │ 1. Check sticky sess │   │
│  │ 2. Apply penalties   │   │
│  │ 3. Walk fallback chain│  │
│  │ 4. Rate-limit check  │   │
│  │ 5. Round-robin keys  │   │
│  └──────────────────────┘   │
├─────────────────────────────┤
│  Provider Adapters          │
│  Google │ Groq │ Mistral …  │
└────────────┬────────────────┘
             │
             ▼
    External LLM Providers
```

All routing state (penalties, round-robin counters, sticky sessions) is held in-memory and resets on restart.

### Routing Logic

The router uses a multi-step strategy to pick the best model for each request:

1. **Sticky session check** — If the conversation has prior assistant turns, the router first tries to keep it on the same model (30-minute TTL, keyed on the first user message).
2. **Priority sort** — Models are sorted by their position in `fallback_chain`, adjusted by a dynamic **penalty score** that increases on `429` responses and decays over time (1 point per 2 minutes).
3. **Rate-limit check** — For each candidate model, the router checks all four limits (`rpm`, `rpd`, `tpm`, `tpd`) using in-memory counters.
4. **Round-robin key rotation** — When a model has multiple API keys, they are distributed across requests cyclically.
5. **Skip-list** — On retry, previously failed `(platform, model, key)` combinations are excluded.

If all models are exhausted, the proxy returns a `503`: *"All models exhausted. Add more API keys or wait for rate limits to reset."*

### Project Structure

```
LimitlessLLM/
├── config.yaml           # ← Main configuration (models, limits, routing)
├── .env                  # API keys (not committed to git)
├── .env.example          # Template — copy to .env
├── requirements.txt      # Python dependencies
├── Dockerfile
├── docker-compose.yml
├── run.sh / run.bat      # One-command startup scripts
└── app/
    ├── main.py           # FastAPI app, lifespan setup
    ├── config.py         # Config loader (YAML + env-var resolution)
    ├── models.py         # Pydantic request/response schemas
    ├── utils.py          # Shared utilities
    ├── providers/
    │   ├── base.py           # BaseProvider abstract class
    │   ├── google.py         # Google Gemini adapter
    │   ├── cohere.py         # Cohere adapter
    │   ├── cloudflare.py     # Cloudflare Workers AI adapter
    │   ├── openai_compat.py  # Generic OpenAI-compatible adapter
    │   └── __init__.py       # Provider registry + init_providers()
    ├── routes/
    │   └── proxy.py          # /v1/chat/completions, /v1/models endpoints
    └── services/
        ├── router.py         # Routing logic, penalties, sticky sessions
        └── ratelimit.py      # In-memory rate-limit counters
```

### Dependencies

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | ≥ 0.115 | Web framework and API layer |
| `uvicorn[standard]` | ≥ 0.30 | ASGI server with HTTP/2 support |
| `httpx[http2]` | ≥ 0.28 | Async HTTP client with connection pooling |
| `pyyaml` | ≥ 6.0 | YAML configuration parsing |
| `pydantic` | ≥ 2.9 | Config validation and request schemas |
| `python-dotenv` | ≥ 1.0 | `.env` file loading |

---

## Limitations

- **Free-tier rate limits** — Providers may change their limits or terms at any time without notice.
- **In-memory state** — Rate-limit counters and routing state reset on server restart. Not suitable for multi-instance deployments without a shared cache.
- **No persistent logging** — Request history is not persisted; use your reverse proxy or logging middleware for that.
- **Not production-SLA-backed** — Designed for personal use, prototyping, and development. Do not use for services requiring guaranteed uptime.

---

## 📄 License

MIT — free to use, modify, and distribute.
