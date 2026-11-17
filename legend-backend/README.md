# Legend Inc. - Novis AI Backend

FastAPI backend. Conversations are stored in PostgreSQL when `DATABASE_URL` is set, and in process memory when it is not.
Stack: Python, FastAPI, Pydantic, Uvicorn, asyncio, psycopg.

## Verified environment
- Python 3.15.0b4 (see `.python-version`): full suite passes (825 tests with the database and the browser test; 808 passed and 17 skipped without a database). 3.15 is a beta, so re-verify on the final release.
- Python 3.15.0a8 (superseded): the alpha could not load the compiled `pydantic-core` wheel. The beta above runs the whole
  stack, so the alpha is no longer a target.

## Setup (from project root)
    uv venv --python 3.14 .venv
    . .venv/bin/activate
    uv pip install -r backend/requirements-dev.txt

## Run
    uvicorn backend.main:app --port 8000 --no-server-header

`--no-server-header` stops Uvicorn from advertising its name and version. The application cannot
remove that header itself, so this flag is part of the deployment.

Without `DATABASE_URL`, conversations live in process memory: use a single worker. With `DATABASE_URL`, several
workers and several servers can share the conversations. Startup fails with a `ConfigError` that lists every
problem, including a route to a provider that can't run.

## Database
Set `DATABASE_URL` to a `postgresql://` connection string. Supabase requires TLS, so add `?sslmode=require`:

    export DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/postgres?sslmode=require'

The server creates its tables at startup (`backend/db/schema.sql`, idempotent). `DATABASE_POOL_MAX` sets the
number of connections, 1 to 100, default 10. The connection string is a credential. It is never printed, logged,
or returned, and the password is scrubbed from every log line. Put it in your environment or secret store, never
in a file in the repository.

What the database changes:
- Conversations and their messages survive a restart, a deploy, and a crash.
- Every worker and every server sees the same conversations. Leases, versions, capacity, and expiry are enforced
  by the database, using its clock, not the process's.
- `GET /health` reports `conversations: down` and returns 503 when the database cannot be reached.
- Startup stops with `conversation storage is unavailable` when the database cannot be reached. The message names
  no credential.

Tests: `TEST_DATABASE_URL` points the database tests at a database that tests may reset. Without it those tests
are skipped, and the skip is printed. Never point it at production.

## Models and routing
Four model ids are accepted: `brainstormer`, `mythical`, `sonet`, and `rapid`. A request that
omits the model gets `rapid`. An unknown id is a validation error (422) listing the allowed values.

Model routing and mode routing are separate:
- **Model routing** (`backend/services/model_router.py`) decides which provider and provider model
  serve a model id. It is configured with `MODEL_ROUTES`.
- **Mode routing** (`backend/prompts/system.py`, `backend/services/tasks.py`) decides the instructions
  the model receives. The mode is set by the request or by the task endpoint.

A model with no route is not generated for. The response is 503 with `MODEL_UNAVAILABLE`, and
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
| `MAX_REQUEST_BYTES` | `2097152` | Larger bodies get 413. Sized for the largest legal request, including attachments. |
| `CHAT_MODES` | all modes | Must include `general`, `research`, `coding`, and `writing`. |
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
| `mode` | no, default `general` | one of `general`, `research`, `coding`, `writing`. `POST /api/chat` only. Task endpoints fix their own mode and reject this field. |

Types are strict and unknown fields are rejected. Clients should load the options from
`GET /api/chat/options` rather than hard-coding them.

### Response: `ChatResponse`
| Field | Type | Notes |
|---|---|---|
| `conversation_id` | string or null | Use it for follow-up messages |
| `model` | string or null | The model used. A failed request shows the requested or default model, or null |
| `mode` | string or null | The mode used |
| `content` | string or null | The reply when `status` is `success` or `stopped`; always null on `error` |
| `status` | `success` \| `stopped` \| `error` | `stopped` keeps the partial reply |
| `error` | object or null | `{code, message, details}`; non-null only when `status` is `error`. Every field is always present, as a key |

Every chat endpoint returns this shape for every outcome, including refusals and validation failures.
The request id for those is in the `X-Request-ID` header and in the body's `request_id`. Every chat response also carries
`conversation_version`, the conversation's version after the turn, for optimistic updates.

### Endpoints
- Root aliases for the chat page: `POST /chat`, `/research`, `/code`, `/write`, and `GET /models`. Same behaviour as the `/api` routes; left out of `/docs`.
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
| 200 | `success` or `stopped` |
| 422 | the request failed validation |
| 502 | `error` with `GENERATION_FAILED` (provider error after the retries, empty reply, or a reply over the size limit) |
| 404 | `CONVERSATION_NOT_FOUND`: the conversation id is unknown, or was dropped |
| 409 | `CONVERSATION_BUSY`: an earlier turn is still running on this conversation. Or `VERSION_CONFLICT`: the version you sent is stale |
| 503 | `error` with `MODEL_UNAVAILABLE` (no route for the model). Or `CAPACITY_EXCEEDED`: the server is full of busy conversations |
| 400 | `BAD_REQUEST`: the Host header is not in `ALLOWED_HOSTS` |
| 401 | `UNAUTHORIZED`: `API_ACCESS_KEY` is set and the bearer key is missing or wrong. The response carries `WWW-Authenticate: Bearer` |
| 429 | `RATE_LIMITED`: the client is over `RATE_LIMIT_PER_MINUTE`, or the model provider stayed rate-limited after the retries. The response carries `Retry-After` in seconds |
| 504 | `error` with `GENERATION_TIMEOUT` |

On the streaming endpoint the HTTP status is sent before generation starts, so failures arrive in the
final `done` event with `status: "error"`.

### Errors
Invalid requests get **422** with `VALIDATION_ERROR`, a sentence for the first problem, and one entry per problem:

    {"conversation_id": null, "model": "rapid", "mode": "general", "content": null, "status": "error",
     "error": {"code": "VALIDATION_ERROR", "message": "The model field is invalid: Unknown model. Allowed values: brainstormer, mythical, sonet, rapid.",
               "details": [{"field": "body.model", "type": "unknown_model", "message": "Unknown model. Allowed values: brainstormer, mythical, sonet, rapid."}]}}

On chat endpoints that envelope is the ChatResponse shape. Every other endpoint uses the ErrorEnvelope
`{"error": {...}, "request_id": "..."}`, including `NOT_FOUND`, `PAYLOAD_TOO_LARGE` (413), and
`INTERNAL_ERROR` (500). Provider details never reach the response. They are logged with the request ID
and the response is generic. Every response carries an `X-Request-ID` header. The server generates it; a client's
`X-Request-ID` is not echoed, so a client-chosen id cannot collide with another turn's id.

## Chat flow and outcomes
Each request is validated, then routed to a model. The flow takes the conversation's lease, builds the
prompt from the conversation and the mode, and generates the reply under the timeout. The question and
answer are saved together only when a reply exists.

- `success`: text was generated and saved.
- `stopped`: the client stopped the stream. The partial reply is kept and saved.
- `error`: a provider error, an empty reply, a timeout, an unrouted model, a conversation that vanished
  or was taken over mid-turn, or an unexpected failure. Its `content` is null and nothing is saved.

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

**Restart and scaling.** Without `DATABASE_URL`, a restart, crash, or deploy erases every conversation and every
in-flight turn, and each worker has its own state: run a single worker. With `DATABASE_URL`, conversations and their
history persist and are shared by every worker. An in-flight turn is still lost on a crash, because its reply is
saved only when it completes. Clients must still handle a 404 for a conversation that has expired.

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

Measured on a 1-vCPU sandbox with the load client on the same CPU, so the numbers are relative, not absolute. Measure
on the target hardware before relying on a figure.

- Store, PostgreSQL (local, one chat turn in an existing conversation: check the id, take the lease and read the newest
  20 messages, commit, release): about 2.5 ms. Before, the same turn read all 200 messages just to get the id, and took
  about 4.6 ms. A full snapshot of a conversation with 200 messages takes about 2 ms, most of it the database returning
  the rows.
- Store, in memory: a chat turn takes well under a millisecond of store time.
- Running server, chat, 16 keep-alive connections, one worker, each connection chatting in its own conversation:
  - in memory: about 800 requests/s, about 1.1 ms of server CPU per request;
  - PostgreSQL: about 270 requests/s, about 2.4 ms of server CPU per request. The cost is the database round trips,
    which share the CPU with the server here. A chat turn makes four round trips: the id check, the lease with the
    context, the commit, and the release.
- With a new conversation on every request, PostgreSQL manages about 200 requests/s, because creation adds steps.
- In-process, the reply guards cost about 0.1 ms per reply. The policy-fragment check was about 1.6 ms per reply and now
  caches its windows per policy and makes one pass over the reply. Its output is unchanged, and a test compares it
  with the original algorithm.
- The optional `uvloop` and `httptools` were measured and not adopted: server CPU per chat request was no lower.
- The uvicorn access log is silenced in favour of the application's access log, so each request is logged once.

## Layout
    backend/
      main.py              app factory, lifespan, provider and router wiring
      config.py            environment loading, validation, secret handling, route parsing
      error_handlers.py    central error responses (ChatResponse on chat endpoints, ErrorEnvelope elsewhere)
      middleware.py        request IDs, request logging, security headers, body size limit
      schemas/             the contract: request.py (inputs), response.py (outputs), common.py,
                           attachments.py, errors.py, events.py (SSE), options.py (allowlists),
                           contract.py (JSON export)
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
    CONTRACT.md                    the API contract: types, defaults, constraints, error formats
    contracts/chat.contract.json   generated contract for the frontend
    backend/requirements.lock.txt  full transitive dependency tree that was tested (3.15.0b4)
    requirements.txt       runtime dependencies (pinned)
    requirements-dev.txt   adds pytest and httpx for testing

## Chat page
`frontend/chat.html` is the page as supplied. It is served at `/app/chat.html` on the same origin as the API, and
it has not been modified. It calls the root paths (`POST /chat`, `/research`, `/code`, `/write`, `GET /models`,
`GET /health`), sends JSON, and reads the reply's `content`. The error text comes from the top-level `detail`.

What works with this page:
- Messages, replies, follow-up messages in the same conversation, and retry of a failed message.
- Error states: a validation message names the field, a dropped connection and a timeout offer Retry, and a
  reply is never shown before the server returns it.

What does not work with this page:
- Attachments. The page sends each file's name, size, and type, but not its text, so the server refuses the
  message with a 422 that names `attachments.N.content`. Sending file text needs a change to the page.
- For a 503 the page shows its own text ("temporarily unavailable"), not the server's reason.

The browser test drives this page in jsdom against live servers. It needs Node 18 or later:

    cd frontend
    npm install

The Python suite runs it when the packages are installed. `npm test` is not useful here: the test needs the
servers that the Python suite starts.

## Test (from project root)
    python -m pytest -q

## Response quality

The instructions the model receives live in `backend/prompts/system.py`: one shared policy, a short addendum per mode, and a depth hint chosen per request (`brief`, `standard`, or `detailed`) from the size and content of the message and whether attachments are present. The current date (UTC) is included so the model can reason about time-relative questions.

The policy states only what this server does. The model has no web access, code execution, or file or account access in a conversation. Replies are Markdown, and math is written as plain text because the page does not render LaTeX.

Checks that run without a model:
- `backend/prompts/quality.py`: `lint_all_prompts()` fails on forbidden phrases (for example, a request for hidden reasoning or a claim of web access) and on missing rules (uncertainty, continuity, the injection guard, the date). `check_reply()` reports leaked reasoning markers, LaTeX, raw HTML, unclosed code fences, code outside a fence, filler openers, closing offers, repeated sentences, and over-long brief answers.
- The same checks run in `backend/tests/test_prompt_quality.py` on every test run.

Checks that need a running server and a model:
```bash
python scripts/eval_prompts.py --base-url http://127.0.0.1:8000 --model rapid
```
The script sends each case in `scripts/eval_cases.json` (single and multi-turn, including an injected instruction in an attachment and a request for a capability the server lacks), applies the reply checks, and exits with status 1 on any failure. It has no quality threshold of its own: the cases are a starting set, to extend with real questions from your users.

## Reliability and security

The reliability layer is in `backend/reliability/`. Every check is a pure function. None of them fetches a page, runs a tool, or claims to verify a fact.

- **Traceable and unverified claims.** Each sentence of a reply is classed as traceable (every specific detail, such as a link, year, percentage, currency amount, or attribution, appears in the user's own messages or attachments), inference (hedged, or a general statement), or unverified (specific details that appear nowhere in the conversation). Nothing is labelled verified. A reply with unverified details gets a visible note.
- **Contradictions.** A heuristic finds simple "subject is value" statements that conflict with the user's own statements. It finds only simple cases.
- **Credentials.** Registered credentials and common key formats are withheld from replies. Inputs are checked for credential formats, and the kinds are reported, not the values.
- **Prompt injection.** Known instruction-override phrases in messages and attachments are flagged, not blocked. Any run of 40 or more characters of the system policy in a reply is withheld.
- **Tool gate.** `backend/reliability/tools.py` decides whether a model-requested call may run. No tools are registered, so every call is refused. Nothing executes tools.
- **Fail closed.** If reply analysis raises an exception, the reply is not delivered and the request returns `GENERATION_FAILED`.
- **Error categories.** Each provider failure has one category (configuration, capacity, transient, rejected, timeout, client input, internal). Only the transient categories are retryable. The category is written to the logs.

Limits: the server has no web or source access, so nothing is checked against an external source, and `sources_checked` is always false. The contradiction, claim, and injection checks are heuristics. They miss cases and can misfire. They are tested on the cases in `backend/tests/test_reliability.py`, not on real traffic.
