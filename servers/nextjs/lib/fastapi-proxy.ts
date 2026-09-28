/**
 * Shared server-side forwarder from Next.js route handlers to FastAPI.
 *
 * Every `app/api/*` route that relays a browser request to the backend used to
 * hand-roll the same pieces: header allowlist, server auth, a bounded fetch
 * signal, response-header copying and a 502 envelope. This module owns them.
 * Routes pass the parts that legitimately differ (timeout, header allowlists,
 * failure envelope) as explicit options instead of re-implementing the plumbing.
 *
 * It deliberately uses the standard `Request`/`Response` types (not
 * `NextRequest`/`NextResponse`) so it can be exercised under plain `node:test`.
 */
import { getFastApiAuthHeaders, getFastApiBaseUrl } from "@/lib/fastapi-internal";
import { fetchTimeoutSignal } from "@/lib/fetch-timeout-signal";

/** Request headers forwarded by default (the public v2 surface's allowlist). */
export const DEFAULT_FORWARDED_REQUEST_HEADERS = [
  "accept",
  "authorization",
  "cookie",
  "content-type",
  "idempotency-key",
  "if-none-match",
  "if-modified-since",
  "if-range",
  "range",
  "x-request-id",
] as const;

/** Response headers copied back by default (covers range/download responses). */
export const DEFAULT_FORWARDED_RESPONSE_HEADERS = [
  "accept-ranges",
  "cache-control",
  "content-length",
  "content-range",
  "content-disposition",
  "content-type",
  "etag",
  "expires",
  "last-modified",
  "location",
  "retry-after",
  "x-request-id",
] as const;

/** Minimal response-header set for JSON APIs. */
export const JSON_RESPONSE_HEADERS = ["content-type", "x-request-id"] as const;

const UNDICI_TIMEOUT_CODES = new Set([
  "UND_ERR_HEADERS_TIMEOUT",
  "UND_ERR_BODY_TIMEOUT",
  "UND_ERR_CONNECT_TIMEOUT",
]);

export type ProxyFailureKind = "timeout" | "unavailable";

/** Opaque undici dispatcher (an `Agent`); typed loosely to avoid importing undici here. */
export type FastApiDispatcher = object;

export interface FastApiFetchOptions {
  /**
   * Upper bound for the whole upstream call. `null` means unbounded: only for
   * long-lived streams whose real limit is the route's `maxDuration`.
   */
  timeoutMs: number | null;
  /** Defaults to the incoming request's method. */
  method?: string;
  /** Client request headers to forward (lower-case). Defaults to the v2 allowlist. */
  forwardRequestHeaders?: readonly string[];
  /** Headers set by the route itself, applied before the forwarded client headers. */
  headers?: Record<string, string>;
  /** Attach the server-side FastAPI credentials. Defaults to true. */
  includeServerAuth?: boolean;
  /**
   * Explicit body. When omitted the incoming request body is streamed for
   * non-GET/HEAD methods; pass `await request.text()` to buffer it instead.
   */
  body?: string | null;
  /** Append the incoming query string to the backend path. Defaults to true. */
  forwardSearch?: boolean;
  /** Abort the upstream call when the client disconnects. Defaults to true. */
  linkClientAbort?: boolean;
  redirect?: RequestRedirect;
  /** An undici `Agent` for calls that must outlive undici's 300s default timeouts. */
  dispatcher?: FastApiDispatcher;
}

/** Classify a failed upstream call. Client disconnects count as unavailable. */
export function classifyProxyError(
  error: unknown,
  clientSignal?: AbortSignal | null,
): ProxyFailureKind {
  if (clientSignal?.aborted) return "unavailable";
  if (!(error instanceof Error)) return "unavailable";
  if (error.name === "TimeoutError" || error.name === "AbortError") return "timeout";
  const cause = error.cause;
  if (
    typeof cause === "object" &&
    cause !== null &&
    "code" in cause &&
    typeof cause.code === "string" &&
    UNDICI_TIMEOUT_CODES.has(cause.code)
  ) {
    return "timeout";
  }
  return "unavailable";
}

function buildRequestHeaders(request: Request, options: FastApiFetchOptions): Headers {
  const headers = new Headers(
    options.includeServerAuth === false ? undefined : getFastApiAuthHeaders(),
  );
  for (const [name, value] of Object.entries(options.headers ?? {})) {
    headers.set(name, value);
  }
  for (const name of options.forwardRequestHeaders ?? DEFAULT_FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

type FetchWithDispatcher = (
  input: string,
  init: RequestInit & { dispatcher?: FastApiDispatcher; duplex?: "half" },
) => Promise<Response>;

/**
 * Call FastAPI on behalf of `request` and return the raw upstream response.
 * Throws on transport failure or timeout; use `classifyProxyError` to tell them apart.
 */
export async function fetchFastApi(
  request: Request,
  backendPath: string,
  options: FastApiFetchOptions,
): Promise<Response> {
  const method = (options.method ?? request.method).toUpperCase();
  const search = options.forwardSearch === false ? "" : new URL(request.url).search;
  const bodyless = method === "GET" || method === "HEAD";
  const body =
    options.body !== undefined
      ? options.body
      : bodyless
        ? null
        : request.body;

  const init: RequestInit & { dispatcher?: FastApiDispatcher; duplex?: "half" } = {
    method,
    headers: buildRequestHeaders(request, options),
    cache: "no-store",
    ...(options.redirect ? { redirect: options.redirect } : {}),
  };
  if (options.timeoutMs !== null) {
    init.signal = fetchTimeoutSignal(
      options.timeoutMs,
      options.linkClientAbort === false ? null : request.signal,
    );
  }
  if (body !== null && body !== undefined) {
    init.body = body as BodyInit;
    if (typeof body !== "string") init.duplex = "half";
  }
  if (options.dispatcher) init.dispatcher = options.dispatcher;

  return (fetch as unknown as FetchWithDispatcher)(
    `${getFastApiBaseUrl()}${backendPath}${search}`,
    init,
  );
}

export interface FastApiProxyOptions extends FastApiFetchOptions {
  /** Upstream response headers to copy back. Defaults to the v2 allowlist. */
  forwardResponseHeaders?: readonly string[];
  /** Content-Type used when the upstream response carries none. */
  defaultContentType?: string;
  /** `Cache-Control` for the relayed response; `null` leaves it untouched. Defaults to `no-store`. */
  cacheControl?: string | null;
  /** Failure envelope. Defaults to `{ detail: { reasonCode } }` with 502/504. */
  onFailure?: (kind: ProxyFailureKind, error: unknown) => Response;
  /** Prefix for the server log line on failure. */
  logLabel?: string;
}

/** Copy the allowlisted headers of an upstream response. */
export function relayResponseHeaders(
  response: Response,
  options: Pick<
    FastApiProxyOptions,
    "forwardResponseHeaders" | "defaultContentType" | "cacheControl"
  > = {},
): Headers {
  const headers = new Headers();
  for (const name of options.forwardResponseHeaders ?? DEFAULT_FORWARDED_RESPONSE_HEADERS) {
    const value = response.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (options.defaultContentType && !headers.has("content-type")) {
    headers.set("content-type", options.defaultContentType);
  }
  if (options.cacheControl !== null) {
    headers.set("cache-control", options.cacheControl ?? "no-store");
  }
  return headers;
}

function defaultFailure(kind: ProxyFailureKind): Response {
  return Response.json(
    {
      detail: {
        reasonCode: kind === "timeout" ? "upstream_timeout" : "upstream_unreachable",
      },
    },
    {
      status: kind === "timeout" ? 504 : 502,
      headers: { "cache-control": "no-store" },
    },
  );
}

/**
 * Forward `request` to FastAPI and stream the upstream body back unchanged
 * (nothing is buffered, so SSE and range downloads keep working).
 */
export async function proxyToFastApi(
  request: Request,
  backendPath: string,
  options: FastApiProxyOptions,
): Promise<Response> {
  try {
    const response = await fetchFastApi(request, backendPath, options);
    return new Response(response.body, {
      status: response.status,
      headers: relayResponseHeaders(response, options),
    });
  } catch (error) {
    const kind = classifyProxyError(
      error,
      options.linkClientAbort === false ? null : request.signal,
    );
    console.error(
      `${options.logLabel ?? "[fastapi-proxy]"} FastAPI request failed (${kind})`,
      error,
    );
    return (options.onFailure ?? defaultFailure)(kind, error);
  }
}

/**
 * Re-emit an upstream body so a mid-stream failure errors the outgoing
 * response instead of closing it cleanly. A clean close makes an EventSource
 * see a normal end-of-stream and skip its reconnect logic; an errored stream
 * aborts the HTTP response so the browser observes a real transport failure.
 */
export function errorPropagatingStream(
  upstreamBody: ReadableStream<Uint8Array>,
  logLabel = "[fastapi-proxy]",
): ReadableStream<Uint8Array> {
  const reader = upstreamBody.getReader();
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const { done, value } = await reader.read();
        if (done) {
          controller.close();
          return;
        }
        controller.enqueue(value);
      } catch (error) {
        console.error(`${logLabel} Upstream body stream errored mid-flight`, error);
        controller.error(error instanceof Error ? error : new Error(String(error)));
      }
    },
    cancel(reason) {
      return reader.cancel(reason);
    },
  });
}
