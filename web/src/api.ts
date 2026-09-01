import type {
  ConversationEvent,
  SessionDetail,
  SessionSummary,
  TurnSnapshot,
  VoiceCapabilities,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "/api/v1").replace(
  /\/$/,
  "",
);

interface SessionListResponse {
  sessions: SessionSummary[];
}

interface ApiErrorEnvelope {
  error?: {
    message?: string;
  };
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export async function listSessions(signal?: AbortSignal) {
  const result = await requestJson<SessionListResponse>("/sessions", { signal });
  return result.sessions;
}

export function createSession() {
  return requestJson<SessionSummary>("/sessions", { method: "POST" });
}

export function getSession(sessionId: string, signal?: AbortSignal) {
  return requestJson<SessionDetail>(`/sessions/${encodeURIComponent(sessionId)}`, {
    signal,
  });
}

export function getTurn(turnId: string, signal?: AbortSignal) {
  return requestJson<TurnSnapshot>(`/turns/${encodeURIComponent(turnId)}`, {
    signal,
  });
}

export function cancelTurn(turnId: string) {
  return requestJson<{ id: string; status: "cancelling" | "cancelled" }>(
    `/turns/${encodeURIComponent(turnId)}/cancel`,
    { method: "POST" },
  );
}

export function getVoiceCapabilities(signal?: AbortSignal) {
  return requestJson<VoiceCapabilities>("/voice/capabilities", { signal });
}

export async function transcribeAudio(
  audio: Blob,
  filename: string,
  signal?: AbortSignal,
) {
  const form = new FormData();
  form.append("audio", audio, filename);
  const response = await fetch(`${API_BASE}/voice/transcriptions`, {
    method: "POST",
    headers: { Accept: "application/json" },
    body: form,
    signal,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<{ text: string }>;
}

export async function synthesizeSpeech(text: string, signal?: AbortSignal) {
  const response = await fetch(`${API_BASE}/voice/speech`, {
    method: "POST",
    headers: {
      Accept: "audio/*",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.blob();
}

export function startTurn(
  sessionId: string,
  message: string,
  onOpen: (turnId: string) => void,
  onEvent: (event: ConversationEvent) => void,
  signal?: AbortSignal,
) {
  return streamRequest(
    `/sessions/${encodeURIComponent(sessionId)}/turns`,
    { message },
    onOpen,
    onEvent,
    signal,
  );
}

export function resumeTurn(
  turnId: string,
  decision: "approve" | "reject",
  selectedItemIds: string[],
  onOpen: (activeTurnId: string) => void,
  onEvent: (event: ConversationEvent) => void,
  signal?: AbortSignal,
) {
  return streamRequest(
    `/turns/${encodeURIComponent(turnId)}/decision`,
    { decision, selected_item_ids: selectedItemIds },
    onOpen,
    onEvent,
    signal,
  );
}

async function requestJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      Accept: "application/json",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  return response.json() as Promise<T>;
}

async function streamRequest(
  path: string,
  body: object,
  onOpen: (turnId: string) => void,
  onEvent: (event: ConversationEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw await responseError(response);
  }
  if (!response.body) {
    throw new ApiError("浏览器无法读取流式响应。", response.status);
  }

  const turnId = response.headers.get("X-Turn-ID");
  if (!turnId) {
    throw new ApiError("服务端没有返回对话标识。", response.status);
  }
  onOpen(turnId);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });

    let boundary = findEventBoundary(buffer);
    while (boundary) {
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary.length);
      const event = parseEvent(block);
      if (event) {
        onEvent(event);
      }
      boundary = findEventBoundary(buffer);
    }
    if (done) {
      break;
    }
  }

  const trailingEvent = parseEvent(buffer.trim());
  if (trailingEvent) {
    onEvent(trailingEvent);
  }
}

function parseEvent(block: string): ConversationEvent | null {
  if (!block || block.startsWith(":")) {
    return null;
  }
  const normalizedBlock = block.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  let type = "message";
  const dataLines: string[] = [];
  for (const line of normalizedBlock.split("\n")) {
    if (line.startsWith("event:")) {
      type = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }
  const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
  return { type, data };
}

function findEventBoundary(
  value: string,
): { index: number; length: number } | null {
  const lfIndex = value.indexOf("\n\n");
  const crlfIndex = value.indexOf("\r\n\r\n");
  if (lfIndex < 0 && crlfIndex < 0) {
    return null;
  }
  if (crlfIndex >= 0 && (lfIndex < 0 || crlfIndex < lfIndex)) {
    return { index: crlfIndex, length: 4 };
  }
  return { index: lfIndex, length: 2 };
}

async function responseError(response: Response): Promise<ApiError> {
  let message = `请求失败（${response.status}）`;
  try {
    const payload = (await response.json()) as ApiErrorEnvelope;
    message = payload.error?.message || message;
  } catch {
    // Non-JSON proxy errors still receive a stable fallback message.
  }
  return new ApiError(message, response.status);
}
