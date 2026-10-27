/**
 * Novis AI / Novis — centralized API client
 *
 * Classic script (no ES module syntax) so it works from file:// next to chat.html.
 * Loads before the app script and exposes these globals:
 *   ApiError, TimeoutError, AbortError, ChatApiClient,
 *   buildPayload, normalizeError, getApiErrorMessage, getApiErrorDetail,
 *   chatApi (shared singleton), initializeApi(config)
 *
 * Responsibilities: transport, streaming (SSE), timeouts, cancellation,
 * error normalization. No DOM or UI code.
 */

const API_DEFAULTS = Object.freeze({
  apiBase: "",
  endpoints: Object.freeze({
    chat: "/chat", research: "/research", code: "/code", write: "/write",
    models: "/models", health: "/health"
  }),
  requestTimeoutMs: 60000,
  healthTimeoutMs: 5000
});

/* ========================================================================
   ERRORS
========================================================================== */
class ApiError extends Error {
  constructor(status, message = "") {
    super(message || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
  }
  isClientError() { return this.status >= 400 && this.status < 500; }
  isServerError() { return this.status >= 500 && this.status < 600; }
  isNotFound() { return this.status === 404; }
  isUnauthorized() { return this.status === 401; }
  isForbidden() { return this.status === 403; }
  isRateLimit() { return this.status === 429; }
  isTimeout() { return this.status === 408; }
}

class TimeoutError extends Error {
  constructor(timeoutMs) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "TimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

class AbortError extends Error {
  constructor() {
    super("Request was cancelled by user");
    this.name = "AbortError";
  }
}

/** Map native AbortError DOMExceptions onto our AbortError class. */
function toClientError(err) {
  if (err?.name === "AbortError" && !(err instanceof AbortError)) return new AbortError();
  return err;
}

/* ========================================================================
   TRANSPORT HELPERS
========================================================================== */

/**
 * Forward an external abort signal onto a controller.
 * Returns a detach function so callers can clean up the listener.
 */
function linkAbort(signal, controller) {
  if (!signal) return () => {};
  const onAbort = () => controller.abort();
  signal.addEventListener("abort", onAbort, { once: true });
  return () => signal.removeEventListener("abort", onAbort);
}

/**
 * fetch() with a response-header timeout. The timer is cleared as soon as
 * headers arrive, so long-running SSE bodies are not cut off by the timeout.
 * init.signal (if any) aborts the request as AbortError.
 */
async function fetchWithTimeout(url, init, timeoutMs) {
  const external = init.signal;
  if (external?.aborted) throw new AbortError();

  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
  const detach = linkAbort(external, controller);

  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (timedOut) throw new TimeoutError(timeoutMs);
    throw toClientError(err); // AbortError on cancel, TypeError on network failure
  } finally {
    clearTimeout(timer);
    detach();
  }
}

/** Read `detail | error | message` from a non-OK JSON body, or "" if unavailable. */
async function readErrorDetail(response) {
  try {
    const body = await response.json();
    return String(body?.detail || body?.error || body?.message || "");
  } catch {
    return "";
  }
}

/**
 * Parse an SSE stream. Calls onDelta({ type: "thinking"|"content", delta })
 * for each chunk and returns the final `content` field if the server sent one.
 * Throws ApiError if the stream carries an error frame.
 */
async function readSseStream(response, onDelta, signal) {
  const reader = response.body.getReader();
  // Cancel the body on abort so a stalled read cannot hang the UI.
  const onAbort = () => reader.cancel().catch(() => {});
  if (signal?.aborted) onAbort();
  else signal?.addEventListener("abort", onAbort, { once: true });
  const decoder = new TextDecoder();
  let buffer = "";
  let finalContent = null;

  // Returns true when the stream signalled completion ([DONE]).
  const handleLine = (rawLine) => {
    const line = rawLine.trim();
    if (!line.startsWith("data:")) return false;
    const data = line.slice(5).trim();
    if (data === "[DONE]") return true;

    let parsed;
    try {
      parsed = JSON.parse(data);
    } catch {
      return false; // keep-alive or malformed frame: ignore
    }
    if (parsed.error) throw new ApiError(parsed.status || 500, String(parsed.error));
    if (parsed.thinking_delta) onDelta({ type: "thinking", delta: parsed.thinking_delta });
    if (typeof parsed.delta === "string") onDelta({ type: "content", delta: parsed.delta });
    if (typeof parsed.content === "string") finalContent = parsed.content;
    return false;
  };

  // Drain complete lines from the buffer; returns true on [DONE].
  const drain = () => {
    let newline;
    while ((newline = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      if (handleLine(line)) return true;
    }
    return false;
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (signal?.aborted) throw new AbortError();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      if (drain()) return finalContent;
    }
    // Flush decoder and any final line that lacked a trailing newline.
    buffer += decoder.decode();
    if (buffer) { handleLine(buffer); buffer = ""; }
    return finalContent;
  } catch (err) {
    throw toClientError(signal?.aborted ? new AbortError() : err);
  } finally {
    // Release the connection and listener whenever reading stops.
    signal?.removeEventListener("abort", onAbort);
    reader.cancel().catch(() => {});
  }
}

/* ========================================================================
   PAYLOAD
========================================================================== */

/**
 * Build the chat request body. Accepts ids or objects for model/mode.
 * Keeps payload shape in one place so the UI never assembles it by hand.
 */
function buildPayload({
  conversationId = null,
  model,
  mode,
  message = "",
  attachments = [],
  tools = {}
} = {}) {
  return {
    conversation_id: conversationId || null,
    model: typeof model === "string" ? model : model?.id || "sonet",
    mode: typeof mode === "string" ? mode : mode?.id || "general",
    message: message || "",
    attachments: attachments.map(({ name, size, type, kind }) => ({ name, size, type, kind })),
    tools: {
      webSearch: Boolean(tools.webSearch),
      files: Boolean(tools.files),
      images: Boolean(tools.images),
      voice: Boolean(tools.voice)
    }
  };
}

/* ========================================================================
   CLIENT
========================================================================== */
class ChatApiClient {
  /**
   * @param {Object} config
   *   apiBase           string   prefix for all paths ("" = same origin)
   *   endpoints         object   { chat, research, code, write, models, health }
   *   modeEndpoints     object   { [modeId]: path } — routes each mode to its endpoint
   *   requestTimeoutMs  number
   *   healthTimeoutMs   number
   */
  constructor(config = {}) {
    this.apiBase = config.apiBase ?? API_DEFAULTS.apiBase;
    this.endpoints = Object.freeze({ ...API_DEFAULTS.endpoints, ...(config.endpoints || {}) });
    this.modeEndpoints = Object.freeze({ ...(config.modeEndpoints || {}) });
    this.requestTimeoutMs = config.requestTimeoutMs || API_DEFAULTS.requestTimeoutMs;
    this.healthTimeoutMs = config.healthTimeoutMs || API_DEFAULTS.healthTimeoutMs;
    this.activeRequests = new Map(); // requestId -> { controller, cancelled }
  }

  buildUrl(path) {
    return this.apiBase.replace(/\/$/, "") + path;
  }

  createRequestId() {
    return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
  }

  /**
   * Resolve the endpoint path for a mode. Accepts a mode id, or an object
   * with `endpoint` / `id`. Unknown modes fall back to the chat endpoint.
   */
  resolveEndpoint(mode) {
    if (mode && typeof mode === "object" && mode.endpoint) return mode.endpoint;
    const id = typeof mode === "string" ? mode : mode?.id;
    return this.modeEndpoints[id] || this.endpoints.chat;
  }

  /**
   * Send a chat message. Streams when the server answers with text/event-stream
   * and an onDelta callback is provided; otherwise reads one JSON body.
   *
   * @param {Object} payload     from buildPayload()
   * @param {AbortSignal} signal optional external cancel
   * @param {Function} onDelta   optional ({ type, delta }) => void
   * @returns {Promise<{content: string, streamed: boolean, requestId: string}>}
   */
  async sendMessage(payload, signal, onDelta) {
    if (signal?.aborted) throw new AbortError();

    const requestId = this.createRequestId();
    const controller = new AbortController();
    const detach = linkAbort(signal, controller);
    this.activeRequests.set(requestId, { controller, cancelled: false });

    try {
      const response = await fetchWithTimeout(
        this.buildUrl(this.resolveEndpoint(payload.mode)),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream, application/json"
          },
          body: JSON.stringify(payload),
          signal: controller.signal
        },
        this.requestTimeoutMs
      );

      if (!response.ok) {
        throw new ApiError(response.status, await readErrorDetail(response));
      }

      const contentType = (response.headers.get("content-type") || "").toLowerCase();
      if (contentType.includes("text/event-stream") && response.body && onDelta) {
        const content = await readSseStream(response, onDelta, controller.signal);
        return { content: content || "", streamed: true, requestId };
      }

      const data = await response.json();
      if (data?.error) throw new ApiError(response.status, String(data.error));
      return {
        content: typeof data?.content === "string" ? data.content : "",
        streamed: false,
        requestId
      };
    } catch (err) {
      throw toClientError(err);
    } finally {
      detach();
      this.activeRequests.delete(requestId);
    }
  }

  /** Cancel one in-flight request. Returns false if it is not active. */
  stopRequest(requestId) {
    const request = this.activeRequests.get(requestId);
    if (!request) return false;
    request.cancelled = true;
    request.controller.abort();
    this.activeRequests.delete(requestId);
    return true;
  }

  /** Retry and regenerate are the same call at the API layer; the UI decides what to remove. */
  retryRequest(payload, signal, onDelta) {
    return this.sendMessage(payload, signal, onDelta);
  }

  regenerateMessage(payload, signal, onDelta) {
    return this.sendMessage(payload, signal, onDelta);
  }

  /** Lightweight liveness check. Never throws. */
  async health(signal) {
    try {
      const response = await fetchWithTimeout(
        this.buildUrl(this.endpoints.health),
        { method: "GET", signal },
        this.healthTimeoutMs
      );
      return response.ok;
    } catch {
      return false;
    }
  }

  /** Fetch the backend model list. Returns [] when the shape is unexpected. */
  async getModels(signal) {
    const response = await fetchWithTimeout(
      this.buildUrl(this.endpoints.models),
      { method: "GET", signal },
      this.requestTimeoutMs
    );
    if (!response.ok) throw new ApiError(response.status);

    const data = await response.json();
    return Array.isArray(data) ? data : Array.isArray(data?.models) ? data.models : [];
  }

  getRequestStatus(requestId) {
    return this.activeRequests.get(requestId) || null;
  }

  stopAllRequests() {
    for (const { controller } of this.activeRequests.values()) controller.abort();
    this.activeRequests.clear();
  }

  getActiveRequestIds() {
    return Array.from(this.activeRequests.keys());
  }
}

/* ========================================================================
   ERROR NORMALIZATION — maps any thrown value to a UI-ready object
========================================================================== */
const API_ERROR_MESSAGES = Object.freeze({
  400: "Invalid request",
  401: "Authentication required",
  403: "Permission denied",
  404: "Endpoint not found",
  408: "Request timed out",
  429: "Rate limit exceeded",
  500: "Server error",
  502: "Service unavailable",
  503: "Service unavailable"
});

const API_ERROR_DETAILS = Object.freeze({
  400: "The server rejected this request as invalid. Check your message and try again.",
  401: "Authentication is required. Configure your credentials and try again.",
  403: "Your account does not have permission for this action.",
  404: "The API endpoint was not found. Verify the backend configuration.",
  408: "The server timed out while processing. Please retry.",
  429: "Rate limit reached. Wait a moment and try again.",
  500: "The server encountered an internal error. Please retry.",
  502: "The service is temporarily unavailable. Please try again shortly.",
  503: "The service is temporarily unavailable. Please try again shortly."
});

/** User-facing title for an ApiError. */
function getApiErrorMessage(err) {
  if (!(err instanceof ApiError)) return "API error";
  return API_ERROR_MESSAGES[err.status] || `Request failed (HTTP ${err.status})`;
}

/** User-facing explanation for an ApiError. */
function getApiErrorDetail(err) {
  if (!(err instanceof ApiError)) return "";
  if (err.status === 400 && err.message) {
    return `${API_ERROR_DETAILS[400]} Server: ${err.message}`;
  }
  return API_ERROR_DETAILS[err.status] || err.message || "Check your connection and try again.";
}

/**
 * Convert any error to { type, status, message, detail, recoverable }.
 * The UI renders this shape and never inspects raw errors.
 */
function normalizeError(err) {
  if (err instanceof TimeoutError) {
    return {
      type: "timeout", status: 408, message: "Request timed out",
      detail: "The server took too long to respond. Please check your connection and try again.",
      recoverable: true
    };
  }
  if (err instanceof AbortError) {
    return {
      type: "abort", status: 0, message: "Request cancelled",
      detail: "You cancelled this request.", recoverable: true
    };
  }
  if (err instanceof ApiError) {
    return {
      type: "api",
      status: err.status,
      message: getApiErrorMessage(err),
      detail: getApiErrorDetail(err),
      recoverable: !err.isClientError() || err.isRateLimit() || err.isTimeout()
    };
  }
  if (err?.name === "TypeError") {
    return {
      type: "network", status: 0, message: "Network error",
      detail: "Unable to reach the server. Check your internet connection and try again.",
      recoverable: true
    };
  }
  return {
    type: "unknown", status: 0, message: "Something went wrong",
    detail: "An unexpected error occurred. Please try again.",
    recoverable: true
  };
}

/* ========================================================================
   SINGLETON
========================================================================== */

/** Shared client. Configure once at startup with initializeApi(). */
const chatApi = new ChatApiClient();

/**
 * Reconfigure the shared client. Call once during app startup, before the first request.
 * Usage: initializeApi({ apiBase: "http://localhost:8000", modeEndpoints: { research: "/research" } });
 */
function initializeApi(config) {
  Object.assign(chatApi, new ChatApiClient(config));
  return chatApi;
}

// Node / test harness support. Harmless in the browser.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    ApiError, TimeoutError, AbortError, ChatApiClient,
    buildPayload, normalizeError, getApiErrorMessage, getApiErrorDetail,
    chatApi, initializeApi
  };
}