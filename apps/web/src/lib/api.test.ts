import { describe, expect, it, vi } from "vitest";
import {
  type JanusError,
  type RoutingInfo,
  configureApi,
  streamChat,
} from "@/lib/api";

/** A fetch that replays the given SSE text, split at arbitrary byte boundaries. */
function respondWith(sse: string, { chunkSize = 7 }: { chunkSize?: number } = {}) {
  const bytes = new TextEncoder().encode(sse);
  let offset = 0;

  const body = {
    getReader: () => ({
      read: async () => {
        if (offset >= bytes.length) return { done: true, value: undefined };
        const value = bytes.slice(offset, offset + chunkSize);
        offset += chunkSize;
        return { done: false, value };
      },
    }),
  };

  return vi.fn().mockResolvedValue({ ok: true, body });
}

function handlers() {
  const routing: RoutingInfo[] = [];
  const deltas: string[] = [];
  const errors: JanusError[] = [];
  return {
    routing,
    deltas,
    errors,
    onRouting: (info: RoutingInfo) => routing.push(info),
    onDelta: (text: string) => deltas.push(text),
    onError: (error: JanusError) => errors.push(error),
  };
}

const ROUTING_EVENT =
  'event: janus.routing\ndata: {"request_id":"rq_1","model":"janus/mock-small",' +
  '"deployment":"mock-small-local","provider":"janus","privacy":"local",' +
  '"fallback_used":false,"routing_explanation":"because reasons"}\n\n';

const chunk = (content: string) =>
  `data: {"choices":[{"delta":{"content":${JSON.stringify(content)}}}]}\n\n`;

describe("streamChat", () => {
  it("reports routing before any content, then the content in order", async () => {
    const h = handlers();
    vi.stubGlobal(
      "fetch",
      respondWith(ROUTING_EVENT + chunk("Hello") + chunk(" world") + "data: [DONE]\n\n"),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    expect(h.routing).toHaveLength(1);
    expect(h.routing[0]?.model).toBe("janus/mock-small");
    expect(h.routing[0]?.privacy).toBe("local");
    expect(h.deltas.join("")).toBe("Hello world");
    expect(h.errors).toEqual([]);
  });

  it("reassembles events split across network chunks", async () => {
    const h = handlers();
    // One byte at a time: no frame arrives whole, so the buffer does the work.
    vi.stubGlobal(
      "fetch",
      respondWith(ROUTING_EVENT + chunk("split") + "data: [DONE]\n\n", { chunkSize: 1 }),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    expect(h.routing).toHaveLength(1);
    expect(h.deltas.join("")).toBe("split");
  });

  it("surfaces a failure that arrives mid-stream", async () => {
    const h = handlers();
    vi.stubGlobal(
      "fetch",
      respondWith(
        ROUTING_EVENT +
          chunk("partial") +
          'event: janus.error\ndata: {"error":{"type":"provider","code":"upstream_timeout",' +
          '"message":"The model stopped responding.","retryable":true}}\n\n' +
          "data: [DONE]\n\n",
      ),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    // Content already delivered is kept; the error is additional, not a replacement.
    expect(h.deltas.join("")).toBe("partial");
    expect(h.errors[0]?.code).toBe("upstream_timeout");
  });

  it("stops at [DONE] and ignores anything after it", async () => {
    const h = handlers();
    vi.stubGlobal(
      "fetch",
      respondWith(chunk("kept") + "data: [DONE]\n\n" + chunk("ignored")),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    expect(h.deltas.join("")).toBe("kept");
  });

  it("reports a structured error when the request itself is rejected", async () => {
    const h = handlers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        body: null,
        json: async () => ({
          error: {
            type: "policy",
            code: "no_eligible_model",
            message: "No model satisfies this request under the applicable policy.",
          },
        }),
      }),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    expect(h.errors[0]?.code).toBe("no_eligible_model");
    expect(h.deltas).toEqual([]);
  });

  it("skips a malformed frame rather than abandoning the stream", async () => {
    const h = handlers();
    vi.stubGlobal(
      "fetch",
      respondWith(
        "data: {not json}\n\n" + chunk("recovered") + "data: [DONE]\n\n",
      ),
    );

    await streamChat({ model: "auto", messages: [] }, h);

    expect(h.deltas.join("")).toBe("recovered");
  });

  it("posts same-origin by default, with credentials and streaming on", async () => {
    const h = handlers();
    const fetchMock = respondWith("data: [DONE]\n\n");
    vi.stubGlobal("fetch", fetchMock);

    await streamChat({ model: "auto", messages: [{ role: "user", content: "hi" }] }, h);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    // Relative, so the app works behind any hostname or forwarded port.
    expect(url).toBe("/api/v1/chat");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(String(init.body)).stream).toBe(true);
  });

  it("honors an explicitly configured API origin", async () => {
    const h = handlers();
    const fetchMock = respondWith("data: [DONE]\n\n");
    vi.stubGlobal("fetch", fetchMock);
    configureApi("https://api.example.com/");

    try {
      await streamChat({ model: "auto", messages: [] }, h);
      expect(fetchMock.mock.calls[0]?.[0]).toBe("https://api.example.com/v1/chat");
    } finally {
      configureApi("/api");
    }
  });
});
