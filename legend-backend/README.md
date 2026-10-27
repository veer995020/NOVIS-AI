# Legend Inc. - Novis AI Backend

FastAPI backend. In-memory only (no database). Stack: Python, FastAPI, Pydantic, Uvicorn, asyncio.

## Verified environment
- Python 3.14.4 (see `.python-version`): full suite passes (298 tests).
- Python 3.15.0a8: `config.py` and the logging module pass their tests (19 tests, with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`) and the
  package compiles. FastAPI and Pydantic can't run yet: the alpha can't load the compiled
  `pydantic-core` wheel (`undefined symbol: PyType_FromSlots`). Retest on a 3.15 beta or
  final, then update `.python-version`.

## Setup (from project root)
    uv venv --python 3.14 .venv
    . .venv/bin/activate
    uv pip install -r backend/requirements-dev.txt

## Run
    uvicorn backend.main:app --port 8000 --no-server-header

`--no-server-header` stops Uvicorn from advertising its name and version. The application cannot
remove that header itself, so this flag is part of the deployment.

Use a single worker. Conversations live in process memory. Startup fails with a
`ConfigError` that lists every problem, including a route to a provider that can't run.

## Models and routing
Four model ids are accepted: `brainstormer`, `mythical`, `sonet`, and `rapid`. A request that
omits the model gets `rapid`. An unknown id is a validation error (422) listing the allowed values.

Model routing and mode routing are separate:
- **Model routing** (`backend/services/model_router.py`) decides which provider and provider model
  serve a model id. It is configured with `MODEL_ROUTES`.
- **Mode routing** (`backend/prompts/system.py`, `backend/services/tasks.py`) decides the instructions
  the model receives. The mode is set by the request or by the task endpoint.

A model with no route is not generated for. The response is 503 with `model_unavailable`, and
`GET /api/chat/options` lists it as not available. No placeholder text is ever returned in its place.

Providers live in `backend/services/providers/`:
- No production provider is registered yet. A new provider implements the `Provider` protocol in
  `backend/services/providers/base.py` and is registered in `providers/__init__.py`.
- No provider is registered in this version. Every model is unavailable until a provider is added.

## Configuration (environment variables)
| Variable | Default | Notes |
|---|---|---|
| `APP_ENV` | `development` | `development`, `test`, or `production`. Production requires https CORS origins, `API_ACCESS_KEY`, `ALLOWED_HOSTS`, and no `ENABLE_DOCS`. |
| `MODEL_ROUTES` | none | Comma-separated `model=provider:provider_model`, e.g. `rapid=<provider>:<provider model id>`. Model ids: `brainstormer`, `mythical`, `sonet`, `rapid`. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `LOG_FORMAT` | `json` | `json` or `text`. |
| `ENABLE_DOCS` | `false` | Serves `/docs` and `/openapi.json`. |
| `CORS_ORIGINS` | none | Comma-separated exact origins. `*` is rejected. |
| `MAX_REQUEST_BYTES` | `65536` | Larger bodies get 413. |
| `CHAT_MODES` | all modes | Must include `standard`, `research`, `code`, and `write`. |
| `MAX_HISTORY_MESSAGES` / `MAX_CONTEXT_MESSAGES` / `MAX_CONVERSATIONS` | `200` / `20` / `1000` | Memory and context limits. |
| `GENERATION_TIMEOUT_SECONDS` | `60` | Longest allowed reply generation, 1 to 600. |
| `CONVERSATION_IDLE_SECONDS` | `3600` | Idle time before a conversation is dropped, 60 to 604800. |
| `MAX_CONCURRENT_TURNS` | `32` | Replies generating at once, across all clients. Extra turns get 503 `capacity_exceeded`. |
| `API_ACCESS_KEY` | none | Shared bearer key. Every API call except `/health*` and `/docs` must send `Authorization: Bearer <key>`. Required in production. Stored as a `Secret`, never logged. |
| `ALLOWED_HOSTS` | none | Comma-separated host names the API accepts in the Host header. Other hosts get 400. Health probes are exempt. Required in production. |
| `RATE_LIMIT_PER_MINUTE` | `120` | Requests per client per minute, 0 disables. Over the limit gets 429 with `Retry-After`. Health probes are exempt. |
| `TRUST_FORWARDED_FOR` | `false` | Use the first `X-Forwarded-For` address as the client identity. Enable only behind a proxy that sets that header. |

Secrets go in the environment or a secret manager, never in the source tree. The `Secret` wrapper in `config.py` keeps a credential out of `repr()`, `str()`, and logs. The next provider will read its credential through it.

## API contract
The request and response shapes are Pydantic models in `backend/schemas/`. They are exported to
`contracts/chat.contract.json`, which the frontend should read. A test fails if the file drifts
from the code. After any schema change, regenerate it:

    python -m backend.schemas.contract

### Request: `ChatRequest` (and `TaskRequest` for the task endpoints)
| Field | Required | Rules |
|---|---|---|
| `message` | yes | string, 1 to 8000 characters after trimming, no control characters except newline, tab, and carriage return |
| `model` | no, default `rapid` | one of `brainstormer`, `mythical`, `sonet`, `rapid` |
| `conversation_id` | no | An id from `POST /api/conversations`, or from an earlier response. Omit to start a new conversation. An unknown id is a 404. |
| `mode` | no, default `standard` | `POST /api/chat` only. Task endpoints fix their own mode and reject this field. |

Types are strict and unknown fields are rejected. Clients should load the options from
`GET /api/chat/options` rather than hard-coding them.

### Response: `ChatResponse`
| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string | Use it for follow-up messages |
| `model` | string | The resolved model id, so a missing model shows up as `rapid` |
| `mode` | string | The mode that was used |
| `content` | string | The reply; empty when `status` is `error` |
| `status` | `completed` \| `stopped` \| `error` | `stopped` keeps the partial reply |
| `error` | object or null | `{code, message, details}`; present only when `status` is `error` |

### Endpoints
- `GET  /api/models`: every model id, whether it is available, and which is the default. Provider names are never shown.
- `GET  /health`: `ok`, `degraded`, or `down`, with checks for `server`, `models`, and `conversations`. Returns 503 only when `down`.
- `GET  /health/live`: the process is running
- `GET  /health/ready`: 200 once started, 503 during startup and shutdown
- `GET  /api/chat/options`: `{models, available_models, default_model, modes, default_mode}`
- `POST /api/chat`: `ChatRequest` in, JSON `ChatResponse` out
- `POST /api/research`, `POST /api/code`, `POST /api/write`: `TaskRequest` in, JSON `ChatResponse` out.
  Each runs the shared chat flow with a fixed mode: `research` organizes findings and comparisons,
  `code` generates, explains, and debugs code, and `write` drafts, rewrites, and refines text.
- `POST /api/chat/stream`: `ChatRequest` in, Server-Sent Events out. Event order is `start`
  (with `request_id`), zero or more `delta` (with `text`), then one `done` whose `response` is a `ChatResponse`.
- `POST /api/chat/{request_id}/stop`: stops an in-flight stream; the partial reply is kept
- `POST /api/conversations`: create a conversation. Optional `title`. Returns 201 with the conversation.
- `GET  /api/conversations/{conversation_id}`: read a conversation. `?limit=N` returns the most recent N messages (1 to 200, default 200). `message_count` gives the total.
- `PATCH /api/conversations/{conversation_id}`: change the title. Body: `{"title": str | null, "expected_version": int}`.
- `POST /api/conversations/{conversation_id}/clear`: remove all messages and keep the conversation. Body: `{"expected_version": int}`.

The JSON endpoints return these status codes, and a failure is never reported as a success:

| Status | Meaning |
|---|---|
| 200 | `completed` or `stopped` |
| 422 | the request failed validation |
| 502 | `error` with `generation_failed` (provider error, empty reply, or unexpected failure) |
| 404 | `conversation_not_found`: the conversation id is unknown, or was dropped |
| 409 | `conversation_busy`: an earlier turn is still running on this conversation. Or `version_conflict`: the version you sent is stale |
| 503 | `error` with `model_unavailable` (no route for the model). Or `capacity_exceeded`: the server is full of busy conversations |
| 400 | `bad_request`: the Host header is not in `ALLOWED_HOSTS` |
| 401 | `unauthorized`: `API_ACCESS_KEY` is set and the bearer key is missing or wrong. The response carries `WWW-Authenticate: Bearer` |
| 429 | `rate_limited`: the client is over `RATE_LIMIT_PER_MINUTE`. The response carries `Retry-After` in seconds |
| 504 | `error` with `generation_timeout` |

On the streaming endpoint the HTTP status is sent before generation starts, so failures arrive in the
final `done` event with `status: "error"`.

### Errors
Invalid requests get **422** with a `validation_error` and one entry per problem:

    {"error": {"code": "validation_error", "message": "Request validation failed.",
               "details": [{"field": "body.model", "message": "Unknown model. Allowed values: brainstormer, mythical, sonet, rapid."}]},
     "request_id": "..."}

Request-level errors use that envelope, including `not_found`, `payload_too_large` (413), and
`internal_error` (500). Provider details never reach the response. They are logged with the request ID
and the response is generic. Every response carries an `X-Request-ID` header.

## Chat flow and outcomes
Each request is validated, then routed to a model. The flow takes the conversation's lease, builds the
prompt from the conversation and the mode, and generates the reply under the timeout. The question and
answer are saved together only when a reply exists.

- `completed`: text was generated and saved.
- `stopped`: the client stopped the stream. The partial reply is kept and saved.
- `error`: a provider error, an empty reply, a timeout, an unrouted model, a conversation that vanished
  or was taken over mid-turn, or an unexpected failure. Its `content` is empty and nothing is saved.

## Conversation state (temporary)
Conversations are the only context the server keeps. They exist in process memory only.

**Lifecycle.** Create with `POST /api/conversations`, read with `GET`, change the title with `PATCH`, and
empty it with `POST .../clear`. Each change increments `version`. `PATCH` and `clear` must send the
version they read. A stale version returns 409, so an edit can't silently overwrite newer data.
Chat requests take a `conversation_id`. If you omit it, the server creates a conversation and returns its id.

**Identity.** The conversation id is the only access control. It is 128 random bits, URL-safe, and
unguessable. There is no authentication, so anyone holding an id can read and change that conversation.
Keep ids private. They appear in URL paths, so proxies and access logs may record them.

**Turns and safety.** A turn holds the conversation's lease from start to finish, so only one turn runs at
a time. A second turn gets 409 `conversation_busy`. Clearing a busy conversation is refused. A turn's
lease lasts the generation timeout plus 30 seconds. A turn whose lease expired cannot save into a
conversation that someone else now holds. A turn is saved only when a reply exists, so an abandoned,
failed, or disconnected turn leaves nothing behind.

**Memory limits.** Defaults and what they mean:
- `MAX_CONVERSATIONS` (1000): the most conversations held at once. At the limit, the least recently used
  idle conversation is dropped. If every conversation is busy, creation returns 503 `capacity_exceeded`.
- `MAX_HISTORY_MESSAGES` (200): the most messages kept per conversation. The oldest are dropped first.
- `CONVERSATION_IDLE_SECONDS` (3600): a conversation untouched for this long is dropped. Busy conversations
  are never dropped.
- Message size is capped at 8000 characters, and replies at 100000.

Worst-case memory is roughly `MAX_CONVERSATIONS x MAX_HISTORY_MESSAGES x 8 KB`. That is about 1.6 GB at the
defaults, so lower those limits for any deployment with real traffic. A `GET` of a full conversation returns
up to 200 messages in one response.

**Restart and scaling.** A restart, crash, or deploy erases every conversation and every in-flight turn.
Their ids return 404 afterwards, and clients must create new conversations. Nothing is written to disk, so
there are no backups. Each worker process has its own state, so run a single worker. Behind a load balancer
with several workers, a conversation created in one worker is unknown to the others. Clients should treat
conversations as disposable and must be able to start a new one.

## Logging
One JSON object per line on stdout. Each request logs its method, redacted path, status, duration, and
request id. Conversation and request ids are replaced by `{id}` and `{request_id}` before logging, because
they are bearer-style values. Credentials, response bodies, and message content are never logged.
Unexpected errors are logged with a traceback for operators. Clients never receive it.

## Security
- **Access control.** When `API_ACCESS_KEY` is set, every API call needs `Authorization: Bearer <key>`. The key is compared in constant time and is never logged or returned. Health probes and `/docs` stay open. Production requires a key.
- **Host allowlist.** When `ALLOWED_HOSTS` is set, requests with another Host header get 400. Production requires it.
- **Rate limit.** Each client gets `RATE_LIMIT_PER_MINUTE` requests in a sliding minute. Failed authentication counts too, which slows key guessing. The limit is per process; with several workers the effective limit is multiplied by the number of workers.
- **CORS** is off unless `CORS_ORIGINS` lists exact origins. Wildcards are rejected. Allowed methods are `GET`, `POST`, and `PATCH`. Allowed headers are `Content-Type`, `Authorization`, and `X-Request-ID`. Credentials are never allowed.
- **Body size** is enforced on the bytes actually received, so chunked requests cannot bypass it.
- **Headers** on every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`. Run with `--no-server-header`.
- **Docs** are off unless `ENABLE_DOCS=true`, and that setting is refused in production.
- **Errors** never include tracebacks, provider details, or submitted values.
- **Secrets** are never in the source tree. The `Secret` wrapper keeps them out of `repr()`, `str()`, and logs.

The full audit, with each finding, its fix, the test that pins it, and the residual risks, is in
`AUDIT.md`. Read the residual risks before exposing the service to users.

## Performance
Measured with `python scripts/benchmark.py` on the test machine. Compare runs on the same machine.

- Conversation store, cost of one `get()`: 1.33 µs with 100 conversations, 1.33 µs with 1,000, and 1.41 µs with 5,000.
  Before the optimisation the same operation took 9.3 µs, 81 µs, and 462 µs at those sizes, because every
  operation scanned every conversation for expiry. Expiry now stops at the first fresh conversation.
- Request path, `GET /api/models` over the ASGI transport: about 3,800 requests/s, with and without the rate limiter.
  The limiter costs under 1%.
- Reads return at most 200 messages. Use `?limit=` to send less.

## Layout
    backend/
      main.py              app factory, lifespan, provider and router wiring
      config.py            environment loading, validation, secret handling, route parsing
      error_handlers.py    central error responses (ErrorEnvelope)
      middleware.py        request IDs, request logging, security headers, body size limit
      schemas/             the contract: chat.py (requests and responses), errors.py, events.py (SSE),
                           options.py (allowlists), contract.py (JSON export)
      routes/              HTTP layer: health.py, models.py, chat.py, tasks.py, conversations.py, responses.py
      services/
        chat.py            the chat flow: validate-to-respond, timeouts, rollback
        model_router.py    model id to provider route (model routing)
        providers/         provider adapters: base.py (the Provider protocol); none registered yet
        tasks.py           fixed-mode task endpoints
        conversation_store.py  temporary conversation state: lifecycle, versions, leases, limits
      prompts/             mode instructions and message assembly (mode routing)
      utils/               ids, clock, logging setup, log redaction, SSE framing
      tests/               unit and HTTP tests; fakes.py holds the scripted test provider
    contracts/chat.contract.json   generated contract for the frontend
    backend/requirements.lock.txt  full transitive dependency tree that was tested (3.14.4)
    requirements.txt       runtime dependencies (pinned)
    requirements-dev.txt   adds pytest and httpx for testing

## Test (from project root)
    python -m pytest -q
