/**
 * Client for the Janus platform API.
 *
 * Session cookies are HttpOnly, so every call sends credentials and no token is
 * ever handled in JavaScript.
 */

/**
 * Same-origin by default: the web server proxies `/api/*` to the control plane
 * (see next.config.mjs). Nothing here depends on the API's host or port, so the
 * app works unchanged whether it is reached over localhost, a forwarded port, or
 * a hostname on a private network.
 */
const DEFAULT_API_URL = "/api";

let apiUrl = DEFAULT_API_URL;

/**
 * Point the client at an API origin, for deployments that serve the control
 * plane from its own hostname instead of through this app.
 *
 * The URL is supplied by the server at request time rather than compiled in, so
 * one image can be promoted from local to staging to production unchanged.
 */
export function configureApi(url: string | undefined): void {
  if (url) apiUrl = url.replace(/\/$/, "");
}

export function apiOrigin(): string {
  return apiUrl;
}

export type Role = "system" | "user" | "assistant";

export interface Message {
  role: Role;
  content: string;
}

export interface ModelDeployment {
  key: string;
  type: string;
  privacy: string;
  region: string | null;
  availability: string;
}

export interface ModelSummary {
  id: string;
  displayName: string;
  provider: string;
  tier: string;
  type: string;
  contextWindow: number;
  maxOutputTokens: number | null;
  capabilities: string[];
  languages: string[];
  privacy: string;
  verified: boolean;
  notes: string | null;
  costClass: string;
  latencyClass: string;
  deployments: ModelDeployment[];
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  pinned_model: string | null;
  message_count: number;
  last_message_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  role: Role;
  sequence: number;
  content: string;
  status: string;
  model: string | null;
  deployment: string | null;
  provider: string | null;
  privacy: string | null;
  fallback_used: boolean;
  routing_explanation: string | null;
  created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
  messages: ConversationMessage[];
}

export interface Organization {
  id: string;
  name: string;
  slug: string;
  role: string | null;
  default_mode: string;
}

export interface SessionInfo {
  user: { id: string; email: string; name: string | null };
  organization: Organization;
  organizations: Organization[];
}

export interface JanusError {
  type: string;
  code: string;
  message: string;
  details?: Record<string, unknown>;
  retryable?: boolean;
}

export class ApiError extends Error {
  readonly status: number;
  readonly error: JanusError;

  constructor(status: number, error: JanusError) {
    super(error.message);
    this.status = status;
    this.error = error;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${apiUrl}${path}`, {
    ...init,
    credentials: "include",
    headers: { "Content-Type": "application/json", ...init.headers },
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      body.error ?? {
        type: "unknown",
        code: "unknown_error",
        message: "Something went wrong.",
      },
    );
  }

  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

function mapModel(entry: RawModel): ModelSummary {
  return {
    id: entry.id,
    displayName: entry.janus.display_name,
    provider: entry.owned_by,
    tier: entry.janus.tier,
    type: entry.janus.type,
    contextWindow: entry.janus.context_window,
    maxOutputTokens: entry.janus.max_output_tokens ?? null,
    capabilities: entry.janus.capabilities,
    languages: entry.janus.languages ?? [],
    privacy: entry.janus.deployments[0]?.privacy ?? "unknown",
    verified: entry.janus.metadata_verified,
    notes: entry.janus.notes ?? null,
    costClass: entry.janus.cost_class,
    latencyClass: entry.janus.latency_class,
    deployments: entry.janus.deployments.map((deployment) => ({
      key: deployment.key,
      type: deployment.type,
      privacy: deployment.privacy,
      region: deployment.region ?? null,
      availability: deployment.availability,
    })),
  };
}

export const api = {
  session: () => request<SessionInfo>("/v1/auth/session"),

  register: (body: {
    email: string;
    password: string;
    name?: string;
    organization_name?: string;
  }) =>
    request<SessionInfo>("/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  login: (body: { email: string; password: string }) =>
    request<SessionInfo>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  logout: () => request<void>("/v1/auth/logout", { method: "POST" }),

  models: async (): Promise<ModelSummary[]> => {
    const body = await request<{ data: RawModel[] }>("/v1/models");
    return body.data.map(mapModel);
  },

  model: async (id: string): Promise<ModelSummary> => {
    const entry = await request<RawModel>(`/v1/models/${id}`);
    return mapModel(entry);
  },

  conversations: async (): Promise<ConversationSummary[]> => {
    const body = await request<{ data: ConversationSummary[] }>("/v1/conversations");
    return body.data;
  },

  conversation: (id: string) => request<ConversationDetail>(`/v1/conversations/${id}`),

  createConversation: (body: { title?: string; pinned_model?: string } = {}) =>
    request<ConversationSummary>("/v1/conversations", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  deleteConversation: (id: string) =>
    request<void>(`/v1/conversations/${id}`, { method: "DELETE" }),

  cancelConversation: (id: string) =>
    request<{ cancelled: number }>(`/v1/conversations/${id}/cancel`, { method: "POST" }),
};

interface RawModel {
  id: string;
  owned_by: string;
  janus: {
    display_name: string;
    tier: string;
    type: string;
    context_window: number;
    max_output_tokens?: number | null;
    capabilities: string[];
    languages?: string[];
    metadata_verified: boolean;
    notes?: string | null;
    cost_class: string;
    latency_class: string;
    deployments: {
      key: string;
      type: string;
      privacy: string;
      region?: string | null;
      availability: string;
    }[];
  };
}

export interface RoutingInfo {
  model: string;
  deployment: string;
  provider: string;
  privacy: string;
  fallback_used: boolean;
  routing_explanation?: string | null;
}

export interface MessageIds {
  conversation_id: string;
  user_message_id: string;
  assistant_message_id: string;
}

export interface StreamHandlers {
  onRouting: (routing: RoutingInfo) => void;
  onDelta: (text: string) => void;
  onError: (error: JanusError) => void;
  onMessageIds?: (ids: MessageIds) => void;
}

async function consumeSse(
  response: Response,
  handlers: StreamHandlers,
): Promise<void> {
  if (!response.ok || !response.body) {
    const errorBody = await response.json().catch(() => ({}));
    handlers.onError(
      errorBody.error ?? {
        type: "unknown",
        code: "stream_failed",
        message: "The model could not be reached.",
      },
    );
    return;
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      let eventName: string | null = null;
      const dataLines: string[] = [];

      for (const line of block.split("\n")) {
        if (line.startsWith("event: ")) eventName = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataLines.push(line.slice(6));
      }
      if (dataLines.length === 0) continue;

      const data = dataLines.join("\n");
      if (data === "[DONE]") return;

      try {
        const parsed = JSON.parse(data);
        if (eventName === "janus.routing") handlers.onRouting(parsed as RoutingInfo);
        else if (eventName === "janus.message") handlers.onMessageIds?.(parsed as MessageIds);
        else if (eventName === "janus.error") handlers.onError(parsed.error as JanusError);
        else if (!eventName) {
          const delta = parsed.choices?.[0]?.delta?.content;
          if (typeof delta === "string") handlers.onDelta(delta);
        }
      } catch {
        // A partial or unparseable frame is skipped rather than breaking the stream.
      }
    }
  }
}

/**
 * Stream a completion.
 *
 * Routing metadata arrives as its own event before any content, so the UI can
 * attribute an answer to a model while it is still being written — which is the
 * whole point of showing it.
 *
 * The product UI uses {@link streamConversationMessage}. This path stays for
 * programmatic callers and the SSE parser tests.
 */
export async function streamChat(
  body: { model: string; messages: Message[] },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${apiUrl}/v1/chat`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...body, stream: true, janus: { routing: { explain: true } } }),
    signal,
  });
  await consumeSse(response, handlers);
}

/**
 * Persist a user turn and stream the assistant reply into that conversation.
 */
export async function streamConversationMessage(
  conversationId: string,
  body: { content: string; model?: string },
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${apiUrl}/v1/conversations/${conversationId}/messages`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  await consumeSse(response, handlers);
}
