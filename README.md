# LimitlessLLM Proxy — Integrate AI in Seconds, Completely Free

> **One endpoint. Every free-tier LLM. Zero code changes.**

A lightweight, high-performance proxy that exposes a single **OpenAI-compatible API** and intelligently routes your requests across multiple free-tier LLM providers — with automatic fallback, rate-limit tracking, and sticky sessions out of the box.

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔌 **OpenAI-compatible API** | Drop-in replacement — works with any OpenAI SDK or tool |
| 🔄 **Automatic fallback** | Seamlessly moves to the next model when one is rate-limited or unavailable |
| ⚖️ **Smart routing** | Priority-based chain with dynamic penalty scoring on `429` responses |
| 🔑 **Key rotation** | Round-robin across multiple API keys per provider |
| 🧠 **Sticky sessions** | Multi-turn conversations stay on the same model for coherent context |
| ⚙️ **Zero-code config** | Add or remove any model by editing `config.yaml` — no code changes |
| 🐳 **Docker ready** | Single `docker compose up` command to run everything |
| 📡 **Streaming** | Full SSE streaming support for real-time token output |
| 🌐 **CORS enabled** | Works directly from browser-based frontends |

---

## 🏗️ Architecture

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

The proxy is **stateless at the request level** — all routing state (penalties, round-robin counters, sticky sessions) is held in-memory and resets on restart.

---

## ⚙️ How `config.yaml` Works

`config.yaml` is the **single source of truth** for the proxy. No database, no UI — just YAML.

### Provider API Keys

```yaml
providers:
  google:
    keys:
      - "${GOOGLE_API_KEY}"   # resolved from .env at startup
  groq:
    keys:
      - "${GROQ_API_KEY}"
      - "${GROQ_API_KEY_2}"   # add multiple keys for round-robin rotation
```

- Values written as `${VAR_NAME}` are automatically resolved from the environment (`.env` file or shell).
- Providers with **empty or missing keys are silently skipped** — you never get startup errors for unused providers.
- You can supply multiple keys per provider; the router will distribute load across them.

### Fallback Chain — Adding Any Model

The `fallback_chain` list defines **which models the proxy will try, and in what order**:

```yaml
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

**To add a new model**, append an entry to `fallback_chain`:

```yaml
  - platform: openrouter          # must match a key under `providers:`
    model_id: meta-llama/llama-3-70b-instruct:free
    display_name: Llama 3 70B (OpenRouter)
    enabled: true
    limits: { rpm: 20, rpd: 200, tpm: null, tpd: null }
```

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

> **Tip:** Models higher in the list have higher priority. Move your most capable or highest-limit models to the top.

### Server Settings

```yaml
server:
  host: "0.0.0.0"
  port: 3001
  log_level: "info"    # debug | info | warning | error
```

### Unified API Key

```yaml
unified_api_key: "freellmapi-change-me"
```

This is the **single bearer token** your clients use to authenticate against the proxy. It can be overridden by the `UNIFIED_API_KEY` environment variable (recommended for production).

---

## 🚀 Quick Start

### Python (OpenAI SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:3001/v1",
    api_key="freellmapi-change-me",   # your UNIFIED_API_KEY
)

response = client.chat.completions.create(
    model="auto",   # proxy picks the best available model
    messages=[{"role": "user", "content": "Hello, world!"}]
)

print(response.choices[0].message.content)
```

Using `model="auto"` lets the proxy pick the best available model. You can also **request a specific model by its `display_name`** (e.g., `"Gemini 2.5 Flash"`).

### Streaming

```python
stream = client.chat.completions.create(
    model="auto",
    messages=[{"role": "user", "content": "Tell me a joke"}],
    stream=True,
)

for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="", flush=True)
```

### cURL

```bash
# List all configured models
curl http://localhost:3001/v1/models \
  -H "Authorization: Bearer freellmapi-change-me"

# Chat completion
curl http://localhost:3001/v1/chat/completions \
  -H "Authorization: Bearer freellmapi-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain quantum entanglement"}]
  }'

# Health check
curl http://localhost:3001/health
```

---

## 📦 Installation

### Option 1: Run Script (Recommended for getting started)

**Windows:**
```bat
run.bat
```

**Linux / macOS:**
```bash
chmod +x run.sh
./run.sh
```

The script automatically creates a virtual environment, installs dependencies, copies `.env.example` → `.env`, and starts the server.

---

### Option 2: Manual Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
cp .env.example .env
# Edit .env and add your API keys

# 4. Start the server
uvicorn app.main:app --host 0.0.0.0 --port 3001 --reload
```

---

### Option 3: Docker

```bash
cp .env.example .env
# Edit .env and add your API keys

docker compose up -d --build
```

The container listens on port `3001` by default (configurable in `config.yaml`).

---

## 🔐 Environment Variables (`.env`)

Copy `.env.example` to `.env` and fill in only the providers you have keys for. Empty keys are automatically ignored.

```env
# The bearer token clients use to access the proxy
UNIFIED_API_KEY=freellmapi-change-me

# Provider API keys — leave blank to skip that provider
GOOGLE_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=
SAMBANOVA_API_KEY=
NVIDIA_API_KEY=
MISTRAL_API_KEY=
OPENROUTER_API_KEY=
GITHUB_API_KEY=
COHERE_API_KEY=
CLOUDFLARE_API_KEY=     # Format: "account_id:api_token"
HUGGINGFACE_API_KEY=
OLLAMA_API_KEY=
```

You only need keys for providers you've added to `config.yaml`.

---

## 🤖 Supported Providers (Out of the Box)

The proxy ships with config adapters for the following platforms. Each can be enabled by adding the matching key to `.env`:

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
| `zhipu` | Zhipu AI | OpenAI-compatible |
| `pollinations` | Pollinations | OpenAI-compatible |
| `llm7` | LLM7 | OpenAI-compatible |
### Adding a provider from this list
For any provider in the table above, enabling it is just two steps — **no code changes needed**:
1. Add the API key to `.env` (e.g. `GROQ_API_KEY=your-key`)
2. Add the provider under `providers:` and your models under `fallback_chain:` in `config.yaml`

### Adding a brand-new OpenAI-compatible provider
If you want to add a provider **not listed above** (e.g. a new service with an OpenAI-compatible API), you need one extra step — add it to the `_OPENAI_COMPAT_PLATFORMS` registry in `app/providers/__init__.py`:

```python
"myprovider": {"name": "My Provider", "base_url": "https://api.myprovider.com/v1"},
```

Then follow the same two steps above (`.env` key + `config.yaml` entry). No other code changes are needed.

---

## 🔀 Routing Logic

The router uses a multi-step strategy to pick the best model for each request:

1. **Sticky session check** — If the conversation has prior assistant turns, the router first tries to keep it on the same model (30-minute TTL, keyed on the first user message).
2. **Priority sort** — Models are sorted by their position in `fallback_chain`, adjusted by a dynamic **penalty score** that increases on `429` responses and decays over time (1 point per 2 minutes).
3. **Rate-limit check** — For each candidate model, the router checks all four limits (`rpm`, `rpd`, `tpm`, `tpd`) using in-memory counters.
4. **Round-robin key rotation** — When a model has multiple API keys, they are distributed across requests cyclically.
5. **Skip-list** — On retry, previously failed `(platform, model, key)` combinations are excluded.

If all models are exhausted, the proxy returns a `503` with the message: *"All models exhausted. Add more API keys or wait for rate limits to reset."*

---

## 📁 Project Structure

```
llmapi-python-recode/
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

---

## 📝 Dependencies

| Package | Version | Purpose |
|---|---|---|
| `fastapi` | ≥ 0.115 | Web framework and API layer |
| `uvicorn[standard]` | ≥ 0.30 | ASGI server with HTTP/2 support |
| `httpx[http2]` | ≥ 0.28 | Async HTTP client with connection pooling |
| `pyyaml` | ≥ 6.0 | YAML configuration parsing |
| `pydantic` | ≥ 2.9 | Config validation and request schemas |
| `python-dotenv` | ≥ 1.0 | `.env` file loading |

---

## ⚠️ Limitations

- **Free-tier rate limits** — Providers may change their limits or terms at any time without notice.
- **In-memory state** — Rate-limit counters and routing state reset on server restart. Not suitable for multi-instance deployments without a shared cache.
- **No persistent logging** — Request history is not persisted; use your reverse proxy or logging middleware for that.
- **Not production-SLA-backed** — This is designed for personal use, prototyping, and development. Do not use it for services requiring guaranteed uptime.

---

## 📄 License

MIT — free to use, modify, and distribute.
