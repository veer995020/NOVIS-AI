# Security and quality audit

Scope: the backend in `backend/` as of this audit. Each finding was reproduced on a running
server before it was fixed, and each fix has a regression test in `backend/tests/test_security.py`.

## Findings and fixes

| Area | Finding | Evidence before the fix | Status |
|---|---|---|---|
| CORS | `PATCH` was not allowed. Browsers could not update conversation titles. | Preflight for PATCH returned 400, with allowed methods `GET, POST`. | **Fixed.** Allowed methods are `GET, POST, PATCH`. Test: `test_cors_preflight_allows_patch_for_configured_origin`. |
| CORS | Unlisted methods and origins. | Not previously tested. | **Verified.** Unlisted origins get no CORS headers. `DELETE` is refused. Tests: `test_cors_rejects_methods_the_api_does_not_use`, `test_cors_never_answers_an_unlisted_origin`. |
| Input limits | The body size limit read only the `Content-Length` header. Chunked requests bypassed it. | 160 KB sent chunked to a 64 KB limit was read and processed. Real Uvicorn returned 422 instead of 413. | **Fixed.** The body is read under the limit before the application sees it. Re-checked on Uvicorn: 413. Tests: `test_chunked_body_over_the_limit_is_rejected_before_the_app_runs`, `test_chunked_body_under_the_limit_still_works`. |
| Input limits | Deeply nested JSON. | 20 KB of `[` returned 422. | **Verified.** No crash. Test: `test_deeply_nested_json_is_a_validation_error_not_a_crash`. |
| Input limits | Message, title, id, and path lengths. | Enforced by the schemas. | **Verified.** Covered by the schema and API tests. |
| Error leakage | Unexpected exceptions. | Generic 500 body, no traceback. | **Verified.** Test: `test_unexpected_errors_return_no_traceback_or_internal_text`. |
| Error leakage | Validation errors could echo submitted values. | Details contain field paths and messages only. | **Verified.** Test: `test_validation_errors_do_not_echo_submitted_values`. |
| Error leakage | Health and model endpoints exposed environment, active request counts, and provider names. | `/health/ready` returned `environment` and `active_requests`. | **Fixed.** Responses carry status words, version, and uptime. Test: `test_liveness_and_readiness_have_safe_fields_only`, `test_health_and_models_do_not_reveal_routing_or_provider_names`. |
| Error leakage | `stop` accepted any path string as an id. | `%2e%2e` and arbitrary text reached the handler. | **Fixed.** The id must be 32 hex characters. Test: `test_stop_rejects_ids_that_are_not_request_ids`. |
| Secrets | Conversation ids and request ids are bearer-style values. Access logs and error logs recorded them in paths. | The conversation id appeared in the server log. | **Fixed.** Paths are redacted before logging (`{id}`, `{request_id}`). Re-checked on Uvicorn: zero occurrences. Tests: `test_safe_path_replaces_bearer_style_ids`, `test_request_log_never_contains_a_conversation_id`. |
| Secrets | Server software was advertised in the `Server` header. | `server: uvicorn/...` on every response. | **Fixed at the server.** The app removes any `Server` header it sets. Uvicorn adds its own, so run with `--no-server-header`, which is now the documented command. Verified: header absent. Test: `test_server_software_is_not_advertised`. |
| Secrets | No credentials are configured in this tree. The provider API key was removed. | `grep` finds no key settings or key-shaped values in code. | **Verified.** `Secret` keeps a future credential out of `repr()`, `str()`, and logs. Settings `repr` is checked: `test_settings_repr_has_no_credentials`. |
| Logging | Request IDs from clients are used in logs. | Accepted only if they match `[A-Za-z0-9._-]{1,64}`, so log lines cannot be forged with newlines. | **Verified.** |
| Logging | Message text and response bodies are never logged. | Logs carry method, redacted path, status, duration, request id, and event name. | **Verified.** Access logs from Uvicorn are silenced in favour of the structured request log. |
| Request validation | Strict types, unknown fields rejected, allowlisted models and modes, control characters rejected. | Covered by the schema tests. | **Verified.** |
| Resource limits | No cap on concurrent replies. | Nothing limited how many replies generated at once. | **Fixed.** `MAX_CONCURRENT_TURNS` (default 32). Extra turns get 503 `capacity_exceeded`. The slot is reserved before any await, so the cap cannot be overshot. Tests: `test_turn_cap_refuses_extra_concurrent_replies`, `test_turn_cap_is_enforced_over_http_as_503`. |
| Resource limits | Conversation memory, message history, generation time, and body size. | Bounded by `MAX_CONVERSATIONS`, `MAX_HISTORY_MESSAGES`, `GENERATION_TIMEOUT_SECONDS`, `MAX_REQUEST_BYTES`. | **Verified.** The README gives the worst-case memory figure. |

## Optimisation and hardening pass

| Item | Before | After | Evidence |
|---|---|---|---|
| Store expiry cost | 9.3 / 81 / 462 µs per get at 100 / 1,000 / 5,000 conversations | 1.33 / 1.33 / 1.41 µs | `scripts/benchmark.py`; `test_store_expiry.py` |
| Unauthenticated API | Anyone reaching the service could create conversations | Optional shared key, required in production | `test_access_control.py` |
| Rate limiting | None | Per-client sliding window, 429 with Retry-After, cost under 1% | `test_access_control.py`; benchmark |
| Host header | Not checked | Allowlist, required in production | `test_access_control.py` |
| Conversation read size | Always up to 200 messages | `?limit=` with `message_count` | `test_pagination.py` |
| Key in logs | Not logged (verified) | Still not logged; checked again after the change | live server check |

## Attachments (contract change)

| Risk | Control | Evidence |
|---|---|---|
| Oversized or hostile file content | 3 files, 64000 characters each, 128000 in total; text types only; control characters rejected | `test_attachments.py` |
| Path traversal in file names | `/ \` and `..` rejected; Windows-invalid characters rejected | `test_attachments.py` |
| Images and binary content reaching a text-only model | Only four text media types are accepted | `test_attachments.py`; live check |
| Body size exceeded by escaped JSON | Default body limit 2 MiB, sized for the worst case of 12-byte escapes | `test_worst_case_attachment_payload_fits_the_default_body_limit` |
| Attachment content kept in memory | Only metadata is stored; content is used for the turn that carries it | `test_history_keeps_attachment_metadata_but_not_content` |
| Attachment content echoed in errors | Errors name the field and reason, never the value | `test_error_messages_do_not_echo_attachment_content` |

Residual: attachment text is user input. It can contain instructions that try to change the
model's behaviour (prompt injection). It is clearly delimited, but it is not filtered.

## Contract security audit (version 3)

The audit probed the live API with hostile requests and recorded each response. Each finding below was
reproduced before it was fixed. Each fix has a regression test in `backend/tests/test_contract_security.py`,
which has 189 cases. The suite reads its limits from the published contract, so it enforces what the contract says.

| Finding | Before | Fix |
|---|---|---|
| Invisible and bidirectional characters in file names | `report\u202egnp.txt` (displays as `reportpng.txt`) and zero-width spaces were accepted. | Names reject all format characters (`Cf`). Bidi overrides and isolates are refused in messages, titles, and attachment content. |
| Lenient identifiers | `"rapid "` was accepted as `rapid`. | Model and mode names are matched exactly. |
| Body parsed by a guessing parser | UTF-16 bodies and bodies with NUL bytes were accepted. | Bodies must be strict UTF-8 JSON, checked once before any route. Failures get 422 `json_invalid`. |
| Inconsistent malformed-body errors | Invalid UTF-8 and oversized integers returned a framework 400 with a plain message. | Same 422 envelope as every other malformed body. |
| Oversized model reply | A reply over `reply_max_chars` returned 500 `internal_error`. | Refused as `generation_failed` (502), with empty content. Nothing is saved. |
| Plain-text CORS refusal | A preflight from an unlisted origin got a plain-text 400. | Refused with the envelope, 403 `forbidden`, and no CORS headers. |
| No content policy on API responses | No Content-Security-Policy. | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'`. The page and the docs keep their own policy. |
| One rate limit for everything | A reply costs a model call, but counted the same as a health check. | Generation endpoints have their own limit, `RATE_LIMIT_GENERATION_PER_MINUTE` (default 30). |

Checked and found correct, with no change: body size on the bytes received (chunked included); message,
title, identifier, name, and count limits at their exact boundaries; the attachment media type allowlist;
every forbidden name character; the model and mode allowlists; task endpoints refusing a `mode` field;
strict types and unknown fields on every chat-style endpoint; error bodies with no traceback, no provider
or internal text, and no key; the access key compared in constant time and redacted in `repr`, errors, the
schema, and the contract; failed key attempts counting towards the rate limit; the Host allowlist; CORS with
exact origins only, no wildcard, no `null` origin, no credentials, and only the methods the API uses; and docs
off by default.

Probing also showed that frontend checks are convenience only: every rule above is enforced by the server
on raw requests, so a modified or missing frontend cannot bypass it.

Residual, accepted or out of scope for the application:
- Media types must match exactly. `text/plain; charset=utf-8` is refused. This is strict by design and is documented.
- Attachment text is user input. It can still carry instructions aimed at the model (prompt injection).
- Rate limits are per process and per client address. Behind a proxy, set `TRUST_FORWARDED_FOR` only if the proxy sets the header, and enforce a shared limit there for several workers.
- Health responses include the version string.
- Unexpected errors are logged with a traceback on the server, for operators only. Restrict access to the logs.
- The chat page as shipped has no access-key field, so it cannot send a bearer key. A deployment that sets `API_ACCESS_KEY` cannot be used from that page until the page is changed. The key must never be placed in a browser page.

## Residual risks (not fixed in this tree)

These need decisions or infrastructure outside this code. None is silently accepted.

1. **Shared key, not per-user identity.** `API_ACCESS_KEY` admits any holder of the key. A conversation id is still the only access control inside the service. Anyone holding an id can read, rename, clear, and chat in that conversation. Per-user accounts and per-conversation ownership are not implemented.
2. **Creation can still evict others.** Creating conversations up to `MAX_CONVERSATIONS` evicts the least recently used idle conversations, which may belong to someone else. The key and rate limit slow this down; per-user quotas are not implemented.
3. **Rate limit is per process.** Counters live in memory. With several workers, the effective limit is multiplied by the number of workers. Enforce a shared limit at the reverse proxy for multi-worker deployments.
4. **No TLS.** Serve only behind an HTTPS-terminating proxy, and set HSTS there.
5. **Host allowlist is optional outside production.** Without `ALLOWED_HOSTS`, the Host header is not checked. Production requires it.
6. **Full conversation reads.** `GET /api/conversations/{id}` returns up to 200 messages by default, about 1.6 MB worst case. Use `?limit=` to send less; the default is unchanged.
7. **Stream request ids are bearer-style.** Anyone with a request id can stop that turn. The id is sent to the client that started the turn, and it is 128 random bits.
8. **Internal logs contain tracebacks.** Unexpected errors are logged with a traceback for operators. Restrict access to the log store.
9. **Future provider messages must be safe.** `ProviderError` text is logged. Any provider must keep credentials and response bodies out of its error messages.
10. **Ephemeral state.** Restarts and multiple workers lose or split state, as documented in the README.
11. **Python 3.15 is not verified.** The alpha cannot load the compiled Pydantic and FastAPI dependencies yet.

## Audit pass: origins, check order, log redaction

Probed against a live server with hostile requests. Findings and fixes:

| # | Finding | Fix | Regression test |
|---|---|---|---|
| 1 | An unauthenticated 3 MiB body was read and size-checked before the key was checked (413, not 401). | The body is now read by its own layer, innermost, after authentication, host, and rate checks. | `test_an_unauthenticated_oversized_body_is_refused_before_it_is_read` |
| 2 | A request from a foreign origin with a valid key was processed; only the CORS header was withheld. | Any browser request from an origin that is neither listed nor the request's own site is refused with 403, for every method, with or without CORS configured. | `test_a_browser_request_from_a_foreign_origin_is_refused_even_with_a_valid_key` |
| 3 | Log lines could carry a provider's error text, and with it any credential it echoed. | The access key is registered with a redactor. Every log line (JSON and plain) is scrubbed, including the JSON-escaped form. | `test_the_key_is_redacted_even_inside_an_exception_message_in_logs` |

Checked and found correct: the key is compared in constant time and never logged; wrong and missing keys
get the same generic refusal; unknown routes are not distinguishable without a key; a foreign Host gets 400;
a crashing provider returns a fixed message and no traceback; deep JSON, invalid UTF-8, lone surrogates,
NUL characters, and truncated JSON all get 422; attachment and model and mode rules are enforced on the
server; rate limits return 429 with Retry-After; docs are off in production.

Residual risks, accepted and documented:
- The shared access key authenticates the client, not a user. Anyone holding it has full access.
- A key entered in the page is visible to anyone who can use the page. Put the API behind real authentication before exposing it.
- TLS and HSTS are the reverse proxy's job. The app sends no HSTS header.
- Rate limits are per process. Run one worker, or limit at the proxy.
- Logs keep tracebacks for operators. The redactor covers credentials, not other sensitive text a provider might echo.
- The page uses inline scripts, so it cannot carry a strict Content-Security-Policy. It is still framed-denied and escapes model text.
- Trusted forwarded addresses are used for rate limits only when TRUST_FORWARDED_FOR is set. Enable it only behind a proxy that overwrites the header.
- `backend/tests/test_contract_security.py` (189 parametrized tests) overlaps with `test_security_audit.py`. Both pass. The second covers the ordering, origin, and redaction guarantees the first does not.

## Integration verification (contract version 4)

`backend/tests/test_integration.py` (49 tests) exercises the reference contract end to end through the
real app. It covers valid requests, missing fields, invalid types, oversized inputs, invalid and retired
model and mode names, attachment errors, API failures (provider crash, timeout, no route, busy, rate limit,
access key, content type), and mismatches between the page and the server.

Defects found and fixed in this round:

| # | Defect | Fix |
|---|---|---|
| 1 | Chat success status was `completed`; the reference says `success`. | Renamed. The page's status list follows. |
| 2 | Error codes were lowercase; the reference says uppercase. | All 18 server codes are uppercase. The page's error table follows. |
| 3 | Mode names were `standard`, `code`, `write`, `creative`; the reference says `general`, `research`, `coding`, `writing`. | Renamed. `creative` removed. The page sent its own mode names through a mapping table that would now have sent unknown modes; the mapping is removed. |
| 4 | A failed chat reply had `content: ""`. | `content` is `null` on every error. |
| 5 | Refusals and validation failures on chat endpoints used the generic envelope, so a client had to handle two shapes. | Every chat outcome is a ChatResponse. |
| 6 | Validation messages were raw Pydantic text ("Field required"). | Sentences such as "The message field is required." Field paths keep the `body.` prefix. |
| 7 | The page expected `completed` and lowercase codes, and sent a mode mapping the server no longer accepted. | Page updated. Its status list, error table, and mode ids now match the server. Covered by page-to-server tests. |
| 8 | Type checking reported 19 errors after the shape change (optional fields read as required). | Explicit checks in code and tests. `mypy` is clean. |

Changes to existing tests, all deliberate: error bodies on chat endpoints are read with a helper that
accepts either shape; expectations of `""` content became `null`; the request id on chat errors is read
from the `X-Request-ID` header; three schema tests now describe the new rules. Each was checked against
the contract, not loosened to pass.

Deviations from the reference, each documented in CONTRACT.md: a third status `stopped`; a server-issued
conversation id (an unknown id returns 404); `details` present as a key on every error.

Not verified: Python 3.15 (the stack runs on 3.14.4), and real browser rendering (the page was tested in jsdom).

## How to re-check

    python -m pytest -q                              # regression tests, including every fix above
    uvicorn backend.main:app --no-server-header      # the documented run command
