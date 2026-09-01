import { AlertCircle, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  cancelTurn,
  createSession,
  getSession,
  getTurn,
  listSessions,
  resumeTurn,
  startTurn,
} from "./api";
import { ChatView } from "./components/ChatView";
import { ReviewDialog } from "./components/ReviewDialog";
import { Sidebar } from "./components/Sidebar";
import { useVoice } from "./hooks/useVoice";
import type {
  ChatMessage,
  CompletedMessageEvent,
  ConversationEvent,
  ProgressEvent,
  ReviewRequest,
  SessionSummary,
} from "./types";

const turnStorageKey = (sessionId: string) => `human-chat:turn:${sessionId}`;

export default function App() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [currentTurnId, setCurrentTurnId] = useState<string | null>(null);
  const [review, setReview] = useState<ReviewRequest | null>(null);
  const [selectedReviewIds, setSelectedReviewIds] = useState<Set<string>>(
    new Set(),
  );
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const streamControllerRef = useRef<AbortController | null>(null);
  const voice = useVoice({
    onTranscription: (text) =>
      setDraft((current) =>
        current.trim() ? `${current.trim()} ${text}` : text,
      ),
    onError: setError,
  });

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId) ?? null,
    [activeSessionId, sessions],
  );
  const locked = busy || review !== null;

  useEffect(() => {
    const controller = new AbortController();
    listSessions(controller.signal)
      .then((items) => {
        setSessions(items);
        setActiveSessionId((current) => current ?? items[0]?.id ?? null);
      })
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) {
          setError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingSessions(false);
        }
      });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([]);
      setReview(null);
      setCurrentTurnId(null);
      return;
    }

    const controller = new AbortController();
    setLoadingHistory(true);
    setMessages([]);
    setReview(null);
    setCurrentTurnId(null);

    const load = async () => {
      const detail = await getSession(activeSessionId, controller.signal);
      setMessages(detail.messages);

      const storedTurnId = sessionStorage.getItem(turnStorageKey(activeSessionId));
      if (!storedTurnId) {
        return;
      }
      try {
        const turn = await getTurn(storedTurnId, controller.signal);
        if (turn.status === "awaiting_review" && turn.review) {
          setCurrentTurnId(turn.id);
          setReview(turn.review);
          setSelectedReviewIds(defaultSelection(turn.review));
          return;
        }
        if (turn.status === "running" || turn.status === "cancelling") {
          await cancelTurn(turn.id).catch(() => undefined);
        }
        sessionStorage.removeItem(turnStorageKey(activeSessionId));
      } catch (reason) {
        if (reason instanceof ApiError && reason.status === 404) {
          sessionStorage.removeItem(turnStorageKey(activeSessionId));
          return;
        }
        throw reason;
      }
    };

    load()
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) {
          setError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoadingHistory(false);
        }
      });
    return () => controller.abort();
  }, [activeSessionId]);

  useEffect(
    () => () => {
      streamControllerRef.current?.abort();
    },
    [],
  );

  const refreshSessions = async () => {
    try {
      setSessions(await listSessions());
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const handleCreateSession = async () => {
    if (locked) {
      return;
    }
    setError(null);
    try {
      const session = await createSession();
      setSessions((current) => [
        session,
        ...current.filter((item) => item.id !== session.id),
      ]);
      setActiveSessionId(session.id);
      setSidebarOpen(false);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const handleSelectSession = (sessionId: string) => {
    if (locked || sessionId === activeSessionId) {
      setSidebarOpen(false);
      return;
    }
    setActiveSessionId(sessionId);
    setSidebarOpen(false);
    setError(null);
  };

  const rememberTurn = (turnId: string, sessionId: string) => {
    setCurrentTurnId(turnId);
    sessionStorage.setItem(turnStorageKey(sessionId), turnId);
  };

  const clearTurn = (sessionId: string) => {
    setCurrentTurnId(null);
    sessionStorage.removeItem(turnStorageKey(sessionId));
  };

  const handleConversationEvent = (
    event: ConversationEvent,
    sessionId: string,
  ) => {
    switch (event.type) {
      case "turn.started":
        setProgress("正在理解你的问题");
        break;
      case "turn.resumed":
        setReview(null);
        setProgress("正在继续处理");
        break;
      case "turn.progress": {
        const data = event.data as unknown as ProgressEvent;
        setProgress(data.message);
        break;
      }
      case "message.completed": {
        const data = event.data as unknown as CompletedMessageEvent;
        setMessages((current) =>
          current.some((message) => message.id === data.message.id)
            ? current
            : [...current, data.message],
        );
        if (data.message.role === "assistant") {
          voice.speakAutomatically(data.message.id, data.message.content);
        }
        break;
      }
      case "memory.saved":
        setProgress(`已保存 ${String(event.data.count)} 条长期记忆`);
        break;
      case "review.required": {
        const nextReview = event.data as unknown as ReviewRequest;
        setReview(nextReview);
        setSelectedReviewIds(defaultSelection(nextReview));
        setProgress(null);
        break;
      }
      case "turn.failed":
        setError(String(event.data.message || "本轮对话未能完成。"));
        setReview(null);
        setProgress(null);
        clearTurn(sessionId);
        break;
      case "turn.cancelled":
        setReview(null);
        setProgress(null);
        clearTurn(sessionId);
        break;
      case "turn.completed":
        setReview(null);
        setProgress(null);
        clearTurn(sessionId);
        void refreshSessions();
        break;
      default:
        break;
    }
  };

  const runStream = async (
    operation: (signal: AbortSignal) => Promise<void>,
  ): Promise<boolean> => {
    const controller = new AbortController();
    streamControllerRef.current = controller;
    setBusy(true);
    setError(null);
    try {
      await operation(controller.signal);
      return true;
    } catch (reason) {
      if (!isAbortError(reason)) {
        setError(errorMessage(reason));
      }
      return false;
    } finally {
      if (streamControllerRef.current === controller) {
        streamControllerRef.current = null;
      }
      setBusy(false);
    }
  };

  const handleSubmit = async () => {
    const message = draft.trim();
    const sessionId = activeSessionId;
    if (!message || !sessionId || busy || review) {
      return;
    }

    const optimisticId = `local-${crypto.randomUUID()}`;
    setMessages((current) => [
      ...current,
      { id: optimisticId, role: "user", content: message },
    ]);
    setDraft("");
    let opened = false;
    const succeeded = await runStream((signal) =>
      startTurn(
        sessionId,
        message,
        (turnId) => {
          opened = true;
          rememberTurn(turnId, sessionId);
        },
        (event) => handleConversationEvent(event, sessionId),
        signal,
      ),
    );
    if (!succeeded && !opened) {
      setMessages((current) =>
        current.filter((item) => item.id !== optimisticId),
      );
      setDraft(message);
    }
  };

  const handleCancel = async () => {
    if (!currentTurnId) {
      return;
    }
    setProgress("正在停止");
    try {
      await cancelTurn(currentTurnId);
    } catch (reason) {
      setError(errorMessage(reason));
    }
  };

  const submitReview = async (decision: "approve" | "reject") => {
    const turnId = currentTurnId;
    const sessionId = activeSessionId;
    if (!turnId || !sessionId || !review || busy) {
      return;
    }
    const selectedIds =
      decision === "approve" && review.selectable
        ? [...selectedReviewIds]
        : [];
    await runStream((signal) =>
      resumeTurn(
        turnId,
        decision,
        selectedIds,
        (activeTurnId) => rememberTurn(activeTurnId, sessionId),
        (event) => handleConversationEvent(event, sessionId),
        signal,
      ),
    );
  };

  const toggleReviewItem = (itemId: string) => {
    setSelectedReviewIds((current) => {
      const next = new Set(current);
      if (next.has(itemId)) {
        next.delete(itemId);
      } else {
        next.add(itemId);
      }
      return next;
    });
  };

  return (
    <div className="app-layout">
      <Sidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        open={sidebarOpen}
        locked={locked}
        onClose={() => setSidebarOpen(false)}
        onCreate={() => void handleCreateSession()}
        onSelect={handleSelectSession}
      />
      <ChatView
        sessionTitle={activeSession?.title || "HumanChat"}
        hasSession={activeSession !== null}
        messages={messages}
        loadingHistory={loadingSessions || loadingHistory}
        busy={busy}
        progress={progress}
        draft={draft}
        onDraftChange={setDraft}
        onOpenSidebar={() => setSidebarOpen(true)}
        onCreateSession={() => void handleCreateSession()}
        onSubmit={() => void handleSubmit()}
        onCancel={() => void handleCancel()}
        sttEnabled={voice.capabilities?.stt_enabled === true}
        ttsEnabled={voice.capabilities?.tts_enabled === true}
        recording={voice.recording}
        transcribing={voice.transcribing}
        autoSpeak={voice.autoSpeak}
        speechLoadingId={voice.speechLoadingId}
        speakingId={voice.speakingId}
        onToggleRecording={voice.toggleRecording}
        onAudioFile={voice.transcribeFile}
        onToggleAutoSpeak={voice.toggleAutoSpeak}
        onToggleSpeech={voice.toggleSpeech}
      />

      {error && (
        <div className="error-toast" role="alert">
          <AlertCircle size={19} aria-hidden="true" />
          <span>{error}</span>
          <button
            className="icon-button"
            type="button"
            onClick={() => setError(null)}
            aria-label="关闭错误提示"
            title="关闭"
          >
            <X size={17} aria-hidden="true" />
          </button>
        </div>
      )}

      {review && (
        <ReviewDialog
          review={review}
          selectedIds={selectedReviewIds}
          submitting={busy}
          onToggle={toggleReviewItem}
          onApprove={() => void submitReview("approve")}
          onReject={() => void submitReview("reject")}
        />
      )}
    </div>
  );
}

function defaultSelection(review: ReviewRequest): Set<string> {
  return review.selectable
    ? new Set(review.items.map((item) => item.id))
    : new Set();
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "请求未能完成，请稍后重试。";
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}
