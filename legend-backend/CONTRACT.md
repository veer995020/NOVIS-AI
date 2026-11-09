# Chatify AI API contract

The machine-readable source is `contracts/chat.contract.json`, generated from the Pydantic models in
`backend/schemas/`. A test fails if the file is out of date. Regenerate with
`python -m backend.schemas.contract` after any change.

Built with Python 3.15, FastAPI, and Pydantic. Verified on Python 3.14.4: the FastAPI and Pydantic
stack does not yet install on the 3.15 alpha.

## Endpoints

| Endpoint | Served at | Response |
|---|---|---|
| `POST /chat` | `POST /api/chat` | ChatResponse |
| `POST /research` | `POST /api/research` | ChatResponse |
| `POST /code` | `POST /api/code` | ChatResponse |
| `POST /write` | `POST /api/write` | ChatResponse |
| `GET /models` | `GET /api/models` | ModelList |
| `GET /health` | `GET /health` | HealthReport |

The `/api` prefix is kept for the documented routes. The chat page calls the root paths `POST /chat`, `/research`, `/code`, `/write`, and `GET /models`; those reach the same handlers and are left out of the OpenAPI schema. `GET /health` is at the
root so that load-balancer probes do not depend on the prefix.

Also served: `GET /api/chat/options`, `POST /api/chat/stream`, `POST /api/chat/{request_id}/stop`,
`GET /api/contract`, and the conversation routes under `/api/conversations`.

The task endpoints fix their mode: `/api/research` is `research`, `/api/code` is `coding`, and
`/api/write` is `writing`. They reject a `mode` field.

## Versioning

`contract_version` in the contract file (currently **5**). A removed or renamed field, a stricter
constraint, a new required field, a changed status code, or a changed error code is breaking. Adding
an optional field with a default is not.

### Changelog
- **5**: the chat page as shipped. The chat endpoints are also served at the root (`POST /chat`,
  `/research`, `/code`, `/write`, and `GET /models`), with the same behaviour as the `/api` routes.
  An unknown `conversation_id` is created on first use, so the page can name its own conversations
  (previously 404). A chat response carries `detail`, a plain-text copy of the error message, because
  the page reads its error text from a top-level string. Task endpoints accept `mode` only when it is
  their own mode. `tools` is accepted; web search is refused. Attachments accept the page's field names
  (`type`, `size`, `kind`); content is still required.
- **4**: the reference contract. Statuses are `success`, `stopped`, and `error` (were `completed`,
  `stopped`, `error`). Error codes are uppercase (`VALIDATION_ERROR`, were `validation_error`). Modes
  are `general`, `research`, `coding`, and `writing` (were `standard`, `research`, `code`, `write`, and
  `creative`, which is removed). A chat error has `content: null` (was `""`). Every outcome of a chat
  endpoint is a ChatResponse, including refusals and validation failures. The request id for those is in
  the `X-Request-ID` header. Validation messages read as sentences, such as "The message field is required."
- **3**: security hardening. Model and mode names are exact. Bidirectional overrides and invisible format
  characters are refused. Request bodies must be strict UTF-8 JSON. A disallowed browser origin gets a 403.
  A reply over `reply_max_chars` is a `GENERATION_FAILED` error, not a 500.
- **2**: `FieldIssue` gained a required `type`. `ChatResponse.error` became always present.
- **1**: first published contract.

## Requests

### ChatRequest: `POST /api/chat`, `POST /api/chat/stream`
`TaskRequest` is the same without `mode`.

| Field | Type | Required | Default | Constraints |
|---|---|---|---|---|
| `conversation_id` | string or null | no | `null` | 1 to 64 characters of `A-Za-z0-9_-`. Omit to start a new conversation. A name not yet in use creates that conversation; a name in use continues it. Names are not secrets: access is controlled by the access key. |
| `message` | string | yes | — | 1 to 8000 characters after trimming. Not blank. No control characters except newline, tab, and carriage return. |
| `model` | string or null | no | `"rapid"` | One of `brainstormer`, `mythical`, `sonet`, `rapid`. A model with no route returns 503 in the response. |
| `mode` | string | no | `"general"` | One of `general`, `research`, `coding`, `writing` (`GET /api/chat/options`). On the task endpoints it is optional, and if sent it must be that endpoint's own mode (`/api/research` is `research`, `/api/code` is `coding`, `/api/write` is `writing`). |
| `tools` | object or null | no | `null` | The page's tool switches: `webSearch`, `files`, `images`, `voice` (booleans). Web search is not available: `webSearch: true` is refused with 422. The others do not change the answer and are not acted on. |
| `attachments` | Attachment[] | no | `[]` | 0 to 3 items. Together at most 128000 characters. |

Unknown fields are rejected with 422. Types are strict: a number is never accepted where a string is
expected. `model` and `mode` are matched exactly. Surrounding spaces, other case, and look-alike characters
are refused. The retired names `standard`, `code`, `write`, and `creative` are refused.

### Attachment

| Field | Type | Required | Constraints |
|---|---|---|---|
| `name` | string | yes | 1 to 255 characters after trimming. No `/ \ < > : " | ? *`, no control characters, not `.` or `..`. |
| `media_type` | enum | yes | `text/plain`, `text/markdown`, `text/csv`, or `application/json`. Accepted as `type` too (the page's name). Images and other types are refused. |
| `content` | string | yes | 1 to 64000 characters. Newline, tab, and carriage return allowed. For `application/json`, must parse. |

`size` and `kind` may be sent by the page; they are informational and not trusted. An attachment with no `content` is refused with 422 `attachments.N.content`: the page sends metadata only, so such a file cannot be read. The content goes to the model for the turn that carries it. History keeps only the name, media type, and size.

## Responses

### ChatResponse
Every chat endpoint returns this shape for every outcome, including refusals and validation failures.
Every field is always present. A field with no value is `null`, never omitted.

| Field | Type | Constraints |
|---|---|---|
| `conversation_id` | string or null | The id to send with follow-up messages. `null` when the request did not identify a conversation. |
| `model` | string or null | The model used. For a failed request, the requested model if valid, the default if omitted, otherwise `null`. |
| `mode` | string or null | The mode used. Same rule as `model`. The task endpoints report their fixed mode. |
| `content` | string or null | The reply when `status` is `success` or `stopped`. Always `null` when `status` is `error`. |
| `detail` | string or null | The error message as plain text, equal to `error.message`. `null` unless `status` is `error`. Added in version 5 for the page, which reads its error text from a top-level string. |
| `status` | enum | `success` (a complete reply), `stopped` (the user stopped it; `content` is the partial reply), or `error`. |
| `error` | ErrorDetail or null | `null` unless `status` is `error`. |

Example, success:
```json
{"conversation_id": "conv_001", "model": "rapid", "mode": "general",
 "content": "Machine learning is a branch of AI...", "status": "success", "error": null}
```

Example, error:
```json
{"conversation_id": "conv_001", "model": "rapid", "mode": "general", "content": null,
 "status": "error", "error": {"code": "VALIDATION_ERROR", "message": "The message field is required.", "details": [...]}}
```

### ErrorDetail

| Field | Type | Constraints |
|---|---|---|
| `code` | enum | One of the codes below. |
| `message` | string | 1 to 500 characters. A sentence a person can act on. Never contains the submitted value. |
| `details` | FieldIssue[] or null | Field-level problems for validation errors. Otherwise `null`. Always present as a key. |

Codes: `VALIDATION_ERROR`, `NOT_FOUND`, `METHOD_NOT_ALLOWED`, `PAYLOAD_TOO_LARGE`,
`UNSUPPORTED_MEDIA_TYPE`, `BAD_REQUEST`, `FORBIDDEN`, `UNAUTHORIZED`, `RATE_LIMITED`,
`GENERATION_FAILED`, `GENERATION_TIMEOUT`, `MODEL_UNAVAILABLE`, `CONVERSATION_NOT_FOUND`,
`VERSION_CONFLICT`, `CONVERSATION_BUSY`, `CAPACITY_EXCEEDED`, `INTERNAL_ERROR`, `HTTP_ERROR`.

The page also produces `INVALID_RESPONSE` for a reply it cannot read. The server never sends it.

### ErrorEnvelope (every other endpoint)
Used by the non-chat endpoints only: unknown routes, the conversation routes, options, and health.

```json
{"error": {"code": "NOT_FOUND", "message": "Not found.", "details": null}, "request_id": "9f2c..."}
```

### FieldIssue

| Field | Type | Constraints |
|---|---|---|
| `field` | string | The machine-readable path, with the `body.` prefix: `body.message`, `body.attachments.0.name`. |
| `type` | string | A stable type, listed below. Branch on this, not on `message`. |
| `message` | string | Human-readable. Never contains the submitted value. |

### Validation error types

| Type | Field | Meaning |
|---|---|---|
| `missing` | any required field | The field is absent. |
| `string_type`, `list_type`, `model_type`, `literal_error` | any | Wrong JSON type, or a value outside the allowed set. |
| `extra_forbidden` | any | An unknown field was sent. |
| `empty_message` | `message` | The message is empty or only whitespace. |
| `string_too_short`, `string_too_long` | any string | Length outside the published limit. |
| `string_pattern_mismatch` | `conversation_id` | Characters outside `A-Za-z0-9_-`. |
| `invalid_name` | `model`, `mode` | Not a lowercase name. |
| `unknown_model`, `unknown_mode` | `model`, `mode` | A well-formed name that is not allowed. The message lists the allowed values. |
| `unsafe_character` | `message`, attachment `content` | A control character other than newline, tab, or carriage return. |
| `too_long` | `attachments` | More than 3 attachments. |
| `invalid_attachment_name` | `attachments.N.name` | A forbidden character, or `.` or `..`. |
| `invalid_json` | `attachments.N.content` | Content for `application/json` does not parse. |
| `value_error` | `attachments` | Attachments together exceed 128000 characters. |

## Status codes

Chat endpoints return a ChatResponse for every code below.

| HTTP | `status` and `error.code` | When |
|---|---|---|
| 200 | `success` | A complete reply was produced and saved. |
| 200 | `stopped` | The user stopped the reply. `content` is the partial reply. |
| 400 | `error`, `BAD_REQUEST` | The Host header is not allowed. |
| 401 | `error`, `UNAUTHORIZED` | The access key is missing or wrong. `WWW-Authenticate: Bearer` is set. |
| 403 | `error`, `FORBIDDEN` | A browser request from an origin that is not listed in `CORS_ORIGINS` and not the site it was sent to. Refused before the key is checked. |
| 404 | `error`, `CONVERSATION_NOT_FOUND` | Reading a conversation by id (`GET /api/conversations/{id}`) for an id that does not exist, or that was dropped. Chat endpoints create an unknown id instead. |
| 405 | `error`, `METHOD_NOT_ALLOWED` | Wrong method for the route. |
| 409 | `error`, `CONVERSATION_BUSY` | An earlier turn is still running on this conversation. Nothing is saved. |
| 413 | `error`, `PAYLOAD_TOO_LARGE` | Body over 2 MiB. |
| 415 | `error`, `UNSUPPORTED_MEDIA_TYPE` | Body not sent as `application/json`. An empty body needs no content type. |
| 422 | `error`, `VALIDATION_ERROR` | A field fails its constraint. `details` names each field. |
| 429 | `error`, `RATE_LIMITED` | Over the per-client limit. `Retry-After` is set. |
| 500 | `error`, `INTERNAL_ERROR` | Unexpected failure. No internal detail is returned. |
| 502 | `error`, `GENERATION_FAILED` | The model failed, returned no text, or the reply exceeded `reply_max_chars`. |
| 503 | `error`, `MODEL_UNAVAILABLE` | No route serves the model. |
| 503 | `error`, `CAPACITY_EXCEEDED` | The server is at its turn or conversation limit. |
| 504 | `error`, `GENERATION_TIMEOUT` | Generation took longer than the server allows. |

A failure to produce a reply is never a 2xx response. Check both the HTTP status and `status` in the body.

## Streaming
`POST /api/chat/stream` returns `text/event-stream`. Events, in order:
- `start`: `{type, request_id, conversation_id}`. Keep `request_id` for `POST /api/chat/{request_id}/stop`.
- `delta`: `{type, text}`, zero or more.
- `done`: `{type, request_id, response}`, where `response` is a ChatResponse, always last.

The HTTP status is sent before generation starts. Generation failures therefore arrive inside `done` with
`status: "error"`. Refusals before the stream starts are ordinary HTTP errors in the ChatResponse shape.

## Options and models
`GET /api/chat/options` returns `models` (every id), `available_models` (ids with a route), `default_model`,
`modes`, and `default_mode`. `GET /api/models` returns each model with its `available` and `default` flags.
The frontend reads these lists rather than hard-coding them.

## Reference example
The request and responses in the reference (see the examples above) validate against these models. The
tests `test_the_reference_*` check them. Three points differ from the reference, on purpose:
1. `status` has a third value, `stopped`, for a user-stopped reply. Removing it would drop partial replies.
2. `conversation_id: "conv_001"` is accepted, and a chat request that names an unused id creates that
   conversation. Reading a conversation by an unknown id is still 404.
3. `error` carries `details`, which is `null` when there are no field problems, so the key is always present.
4. Every chat response also carries `detail` (the error text as a string), for the page.

## Frontend obligations
1. Enforce the published limits before sending. The server enforces them again and never trusts the page.
2. Read `contracts/chat.contract.json` (or `GET /api/contract`) as the source for limits and defaults.
3. Send `Content-Type: application/json`, and `Authorization: Bearer <key>` when the deployment requires it.
4. Treat `status` in the body, not only the HTTP code, as the outcome of a turn.
5. Keep `conversation_id` from the last confirmed reply, and send only that id.
6. Do not send attachment content again on later turns; the server does not keep it.
