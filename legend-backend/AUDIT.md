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

## How to re-check

    python -m pytest -q                              # regression tests, including every fix above
    uvicorn backend.main:app --no-server-header      # the documented run command
