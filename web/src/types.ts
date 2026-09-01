export interface SessionSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface SessionDetail extends SessionSummary {
  messages: ChatMessage[];
}

export interface ReviewItem {
  id: string;
  title: string;
  description?: string;
  details?: Record<string, unknown>;
}

export interface ReviewRequest {
  kind: "tool" | "memory";
  title: string;
  description: string;
  selectable: boolean;
  items: ReviewItem[];
}

export type TurnStatus =
  | "running"
  | "awaiting_review"
  | "cancelling"
  | "cancelled"
  | "completed"
  | "failed";

export interface TurnSnapshot {
  id: string;
  session_id: string;
  status: TurnStatus;
  review: ReviewRequest | null;
}

export interface ConversationEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface CompletedMessageEvent {
  message: ChatMessage;
}

export interface ProgressEvent {
  stage: string;
  message: string;
}
