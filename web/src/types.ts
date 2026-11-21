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

export interface ToolReviewRequest {
  kind: "tool";
  title: string;
  description: string;
  items: ReviewItem[];
}

export interface UserInputReviewRequest {
  kind: "user_input";
  question: string;
}

export interface PlanBudgetReviewRequest {
  kind: "plan_budget";
  completed_steps: number;
  remaining_steps: string[];
  hard_limit: number;
}

export type ReviewRequest =
  | ToolReviewRequest
  | UserInputReviewRequest
  | PlanBudgetReviewRequest;

export interface ReviewDecision {
  decision:
    | "approve"
    | "reject"
    | "respond"
    | "continue"
    | "continue_with_input"
    | "stop";
  message?: string;
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

export interface VoiceCapabilities {
  stt_enabled: boolean;
  tts_enabled: boolean;
  tts_available: boolean;
  tts_auto_start: boolean;
  max_audio_bytes: number;
}
