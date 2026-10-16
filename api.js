/**
 * Chatify AI — Centralized API Client
 * 
 * Single source of truth for all API communication.
 * - No scattered fetch calls
 * - Consistent headers and timeout handling
 * - JSON + streaming support
 * - Error normalization
 * - Request lifecycle management (abort, retry, regenerate)
 * - Completely separate from UI logic
 * 
 * Import: import { ChatApiClient } from './api.js';
 * Use: const client = new ChatApiClient(config);
 */

/* ========================================================================
   ERROR CLASSES
========================================================================== */
export class ApiError extends Error {
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

export class TimeoutError extends Error {
  constructor(timeoutMs) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "TimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

export class AbortError extends Error {
  constructor() {
    super("Request was cancelled by user");
    this.name = "AbortError";
  }
}

/* ========================================================================
   UTILITIES
========================================================================== */

/**
 * Fetch with built-in timeout support
 * Converts timeout to TimeoutError, external abort to AbortError
 */
async function fetchWithTimeout(url, options = {}, timeoutMs) {
  const controller = new AbortController();
  let timedOut = false;
  
  // Set up internal timeout
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  // Link external abort signal if provided
  const external = options.signal;
  if (external) {
    if (external.aborted) {
      clearTimeout(timer);
      throw new AbortError();
    }
    external.addEventListener("abort", () => controller.abort(), { once: true });
  }

  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (timedOut) throw new TimeoutError(timeoutMs);
    if (err?.name === "AbortError") throw new AbortError();
    throw err; // Network error (TypeError)
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Parse server-sent events (SSE) stream
 * Handles typed deltas: thinking and content
 * Returns final content when stream ends
 */
async function readSseStream(response, onDelta) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalContent = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    let newlineIndex;

    while ((newlineIndex = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, newlineIndex).trim();
      buffer = buffer.slice(newlineIndex + 1);

      if (!line.startsWith("data:")) continue;

      const data = line.slice(5).trim();
      if (data === "[DONE]") return finalContent;

      try {
        const parsed = JSON.parse(data);

        // Check for server-side errors in stream
        if (parsed.error) {
          throw new ApiError(parsed.status || 500, String(parsed.error));
        }

        // Thinking phase
        if (parsed.thinking_delta) {
          onDelta({ type: "thinking", delta: parsed.thinking_delta });
        }

        // Content phase
        if (typeof parsed.delta === "string") {
          onDelta({ type: "content", delta: parsed.delta });
        }

        // Final content (may appear in stream)
        if (typeof parsed.content === "string") {
          finalContent = parsed.content;
        }
      } catch (parseErr) {
        // Re-throw API errors, ignore malformed frames
        if (parseErr instanceof ApiError) throw parseErr;
      }
    }
  }

  return finalContent;
}

/**
 * Resolve endpoint from mode config
 * Allows modes to have different endpoints (e.g., research, coding, writing)
 */
function resolveEndpoint(mode) {
  if (typeof mode === "string") return `/chat`; // Default fallback
  if (mode?.endpoint) return mode.endpoint;
  return `/chat`;
}

/**
 * Build request payload from components
 * Normalizes payload structure, handles mode/model resolution
 * Keeps payload logic in API layer, not UI
 */
export function buildPayload(options) {
  const {
    conversationId,
    model,        // string or object with .id
    mode,         // string or object with .id and .endpoint
    message,
    attachments = [],
    tools = {}
  } = options;

  return {
    conversation_id: conversationId || null,
    model: typeof model === "string" ? model : model?.id || "sonet",
    mode: typeof mode === "string" ? mode : mode?.id || "general",
    message: message || "",
    attachments: attachments.map(a => ({
      name: a.name,
      size: a.size,
      type: a.type,
      kind: a.kind
    })),
    tools: {
      webSearch: tools.webSearch || false,
      files: tools.files || false,
      images: tools.images || false,
      voice: tools.voice || false
    }
  };
}

/* ========================================================================
   CHAT API CLIENT
========================================================================== */
export class ChatApiClient {
  constructor(config = {}) {
    // API configuration
    this.apiBase = config.apiBase || "";
    this.endpoints = config.endpoints || {
      chat: "/chat",
      research: "/research",
      code: "/code",
      write: "/write",
      models: "/models",
      health: "/health"
    };

    // Timeouts
    this.requestTimeoutMs = config.requestTimeoutMs || 60000;
    this.healthTimeoutMs = config.healthTimeoutMs || 5000;

    // Request tracking
    this.activeRequests = new Map(); // id -> { controller, cancelled }
  }

  /**
   * Build full URL from endpoint path
   */
  buildUrl(path) {
    return this.apiBase.replace(/\/$/, "") + path;
  }

  /**
   * Create unique request ID for tracking
   */
  createRequestId() {
    return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
  }

  /**
   * Send chat message (single message or streaming)
   * 
   * @param {Object} payload - Message payload
   *   - conversation_id: string | null
   *   - model: string
   *   - mode: string | object (supports endpoint)
   *   - message: string
   *   - attachments: array
   *   - tools: object
   * @param {AbortSignal} signal - Optional abort signal
   * @param {Function} onDelta - Optional callback for streaming deltas
   * @returns {Promise<{content: string, streamed: boolean}>}
   */
  async sendMessage(payload, signal, onDelta) {
    const requestId = this.createRequestId();
    const controller = new AbortController();

    // Track request for potential abort/retry
    this.activeRequests.set(requestId, { controller, cancelled: false });

    try {
      // Link external abort signal if provided
      if (signal) {
        if (signal.aborted) throw new AbortError();
        signal.addEventListener("abort", () => controller.abort(), { once: true });
      }

      // Resolve endpoint from mode
      const endpoint = resolveEndpoint(payload.mode);

      // Send request
      const response = await fetchWithTimeout(
        this.buildUrl(endpoint),
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

      // Handle error responses
      if (!response.ok) {
        let detail = "";
        try {
          const body = await response.json();
          detail = body.detail || body.error || body.message || "";
        } catch {
          // Non-JSON error body
        }
        throw new ApiError(response.status, detail ? String(detail) : "");
      }

      // Parse response based on content type
      const contentType = (response.headers.get("content-type") || "").toLowerCase();

      if (contentType.includes("text/event-stream") && response.body && onDelta) {
        // Streaming response
        const content = await readSseStream(response, onDelta);
        return {
          content: content || "",
          streamed: true,
          requestId
        };
      } else {
        // JSON response
        const data = await response.json();
        if (data?.error) {
          throw new ApiError(response.status || 500, String(data.error));
        }
        return {
          content: typeof data?.content === "string" ? data.content : "",
          streamed: false,
          requestId
        };
      }
    } finally {
      this.activeRequests.delete(requestId);
    }
  }

  /**
   * Stop an in-flight request
   * Gracefully aborts with cancellation flag for cleanup
   * 
   * @param {string} requestId - Request ID returned from sendMessage
   */
  stopRequest(requestId) {
    const request = this.activeRequests.get(requestId);
    if (!request) return false;

    request.cancelled = true;
    request.controller.abort();
    this.activeRequests.delete(requestId);
    return true;
  }

  /**
   * Retry a failed request
   * Typically used after a network error or timeout
   * Can override original payload
   * 
   * @param {Object} payload - Original or updated payload
   * @param {AbortSignal} signal - Abort signal
   * @param {Function} onDelta - Stream callback
   * @returns {Promise<{content, streamed, requestId}>}
   */
  async retryRequest(payload, signal, onDelta) {
    // Retry is just a fresh sendMessage call with the same payload
    // Caller decides if payload should be modified before retry
    return this.sendMessage(payload, signal, onDelta);
  }

  /**
   * Regenerate an assistant response
   * Calls sendMessage with the original user prompt
   * Used when user clicks "Regenerate"
   * 
   * @param {Object} payload - Payload with original message
   * @param {AbortSignal} signal - Abort signal
   * @param {Function} onDelta - Stream callback
   * @returns {Promise<{content, streamed, requestId}>}
   */
  async regenerateMessage(payload, signal, onDelta) {
    // Regenerate is identical to retry from API perspective
    // Difference is in caller's UI behavior (removing old response first, etc)
    return this.sendMessage(payload, signal, onDelta);
  }

  /**
   * Check API health
   * Lightweight endpoint to verify backend is running
   * 
   * @param {AbortSignal} signal - Optional abort signal
   * @returns {Promise<boolean>}
   */
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

  /**
   * Fetch available models from backend
   * 
   * @param {AbortSignal} signal - Optional abort signal
   * @returns {Promise<Array>}
   */
  async getModels(signal) {
    const response = await fetchWithTimeout(
      this.buildUrl(this.endpoints.models),
      { method: "GET", signal },
      this.requestTimeoutMs
    );

    if (!response.ok) {
      throw new ApiError(response.status);
    }

    const data = await response.json();
    return Array.isArray(data) ? data : Array.isArray(data?.models) ? data.models : [];
  }

  /**
   * Get request status
   * Useful for checking if a request is still in-flight
   * 
   * @param {string} requestId - Request ID to check
   * @returns {Object|null} - Request object or null if not found
   */
  getRequestStatus(requestId) {
    return this.activeRequests.get(requestId) || null;
  }

  /**
   * Stop all in-flight requests
   * Useful for cleanup on navigation or app shutdown
   */
  stopAllRequests() {
    for (const [, request] of this.activeRequests) {
      request.controller.abort();
    }
    this.activeRequests.clear();
  }

  /**
   * Get list of all active request IDs
   * Useful for debugging
   */
  getActiveRequestIds() {
    return Array.from(this.activeRequests.keys());
  }
}

/* ========================================================================
   ERROR NORMALIZATION
========================================================================== */

/**
 * Convert any error to a normalized error object
 * Useful for consistent error handling in UI
 * 
 * @param {Error} err - Any error
 * @returns {Object} - {type, status, message, detail, recoverable}
 */
export function normalizeError(err) {
  if (err instanceof TimeoutError) {
    return {
      type: "timeout",
      status: 408,
      message: "Request timed out",
      detail: "The server took too long to respond. Please check your connection and try again.",
      recoverable: true
    };
  }

  if (err instanceof AbortError) {
    return {
      type: "abort",
      status: 0,
      message: "Request cancelled",
      detail: "You cancelled this request.",
      recoverable: true
    };
  }

  if (err instanceof ApiError) {
    const detail = getApiErrorDetail(err);
    return {
      type: "api",
      status: err.status,
      message: getApiErrorMessage(err),
      detail,
      recoverable: !err.isClientError() || err.isRateLimit() || err.isTimeout()
    };
  }

  if (err?.name === "TypeError") {
    // Network error (offline, CORS, DNS, etc)
    return {
      type: "network",
      status: 0,
      message: "Network error",
      detail: "Unable to reach the server. Check your internet connection and try again.",
      recoverable: true
    };
  }

  // Unknown error
  return {
    type: "unknown",
    status: 0,
    message: "Something went wrong",
    detail: "An unexpected error occurred. Please try again.",
    recoverable: true
  };
}

/**
 * Get user-facing message for API error
 */
export function getApiErrorMessage(err) {
  if (!(err instanceof ApiError)) return "API error";

  switch (err.status) {
    case 400: return "Invalid request";
    case 401: return "Authentication required";
    case 403: return "Permission denied";
    case 404: return "Endpoint not found";
    case 408: return "Request timed out";
    case 429: return "Rate limit exceeded";
    case 500: return "Server error";
    case 502: return "Service unavailable";
    case 503: return "Service unavailable";
    default: return `Request failed (HTTP ${err.status})`;
  }
}

/**
 * Get detailed explanation for API error
 */
export function getApiErrorDetail(err) {
  if (!(err instanceof ApiError)) return "";

  switch (err.status) {
    case 400:
      return "The server rejected this request as invalid. Check your message and try again." +
        (err.message ? ` Server: ${err.message}` : "");
    case 401:
      return "Authentication is required. Configure your credentials and try again.";
    case 403:
      return "Your account does not have permission for this action.";
    case 404:
      return "The API endpoint was not found. Verify the backend configuration.";
    case 408:
      return "The server timed out while processing. Please retry.";
    case 429:
      return "Rate limit reached. Wait a moment and try again.";
    case 500:
      return "The server encountered an internal error. Please retry.";
    case 502:
    case 503:
      return "The service is temporarily unavailable. Please try again shortly.";
    default:
      return err.message || "Check your connection and try again.";
  }
}

/* ========================================================================
   SINGLETON EXPORT
========================================================================== */

/**
 * Default singleton instance for convenience
 * Usage: import { chatApi } from './api.js'; chatApi.sendMessage(payload);
 */
export const chatApi = new ChatApiClient();

/**
 * Initialize the singleton with custom config
 * Must be called once on app startup
 * Usage: initializeApi({ apiBase: 'http://localhost:8000', ... });
 */
export function initializeApi(config) {
  Object.assign(chatApi, new ChatApiClient(config));
  return chatApi;
}