/**
 * Proxy for the control plane.
 *
 * The browser talks only to this origin and the web server forwards to the API.
 * That keeps the session cookie same-origin, removes CORS from the picture, and
 * means a remote browser needs one reachable port instead of two.
 *
 * This is a route handler rather than a `rewrites()` entry on purpose: Next
 * serializes rewrite destinations into the build manifest, so a rewrite would
 * freeze the API address at image build time. Here it is read per request.
 */

import type { NextRequest } from "next/server";

export const dynamic = "force-dynamic";

function apiBaseUrl(): string {
  return (process.env.JANUS_API_URL ?? "http://localhost:8080").replace(/\/$/, "");
}

/** Headers that describe a single hop and must not be passed along. */
const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  // Recomputed for the forwarded body.
  "content-length",
  "host",
]);

function forwardHeaders(request: NextRequest): Headers {
  const headers = new Headers();
  for (const [name, value] of request.headers) {
    if (!HOP_BY_HOP.has(name.toLowerCase())) headers.set(name, value);
  }

  const clientIp = request.headers.get("x-forwarded-for");
  if (clientIp) headers.set("x-forwarded-for", clientIp);

  return headers;
}

function responseHeaders(upstream: Response): Headers {
  const headers = new Headers();
  for (const [name, value] of upstream.headers) {
    const lower = name.toLowerCase();
    // Content encoding and length no longer describe what is being streamed on.
    if (HOP_BY_HOP.has(lower) || lower === "set-cookie" || lower === "content-encoding") {
      continue;
    }
    headers.set(name, value);
  }

  // Multiple cookies must survive as separate headers, so they are appended.
  for (const cookie of upstream.headers.getSetCookie()) {
    headers.append("set-cookie", cookie);
  }

  return headers;
}

async function proxy(request: NextRequest, path: string[]): Promise<Response> {
  const target = `${apiBaseUrl()}/${path.join("/")}${request.nextUrl.search}`;

  // Requests are small JSON documents, so buffering the body avoids negotiating
  // a duplex stream. Responses are not buffered — that is where streaming lives.
  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: forwardHeaders(request),
      body,
      redirect: "manual",
      cache: "no-store",
      signal: request.signal,
    });
  } catch (error) {
    if (request.signal.aborted) {
      // The user navigated away or pressed stop; not a failure worth reporting.
      return new Response(null, { status: 499 });
    }
    console.error(`Proxy to ${target} failed`, error);
    return Response.json(
      {
        error: {
          type: "unavailable",
          code: "control_plane_unreachable",
          message: "The Janus control plane is not reachable from the web server.",
          retryable: true,
        },
      },
      { status: 502 },
    );
  }

  // upstream.body is passed through untouched so server-sent events reach the
  // browser as they are produced rather than at the end of the response.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders(upstream),
  });
}

type Context = { params: Promise<{ path: string[] }> };

async function handle(request: NextRequest, context: Context): Promise<Response> {
  const { path } = await context.params;
  return proxy(request, path);
}

export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const PUT = handle;
export const DELETE = handle;
export const HEAD = handle;
