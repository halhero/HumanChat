import {
  LoaderCircle,
  Menu,
  Plus,
  SendHorizontal,
  Square,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef } from "react";

import type { ChatMessage } from "../types";

interface ChatViewProps {
  sessionTitle: string;
  hasSession: boolean;
  messages: ChatMessage[];
  loadingHistory: boolean;
  busy: boolean;
  progress: string | null;
  draft: string;
  onDraftChange: (value: string) => void;
  onOpenSidebar: () => void;
  onCreateSession: () => void;
  onSubmit: () => void;
  onCancel: () => void;
}

export function ChatView({
  sessionTitle,
  hasSession,
  messages,
  loadingHistory,
  busy,
  progress,
  draft,
  onDraftChange,
  onOpenSidebar,
  onCreateSession,
  onSubmit,
  onCancel,
}: ChatViewProps) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, progress]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    onSubmit();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  };

  return (
    <main className="chat-shell">
      <header className="chat-header">
        <button
          className="icon-button chat-header__menu"
          type="button"
          onClick={onOpenSidebar}
          aria-label="打开会话列表"
          title="打开会话列表"
        >
          <Menu size={20} aria-hidden="true" />
        </button>
        <div className="chat-header__identity">
          <img src="/assistant-avatar.png" alt="七海头像" />
          <div>
            <h1>{sessionTitle}</h1>
            <p><span className="status-dot" />七海 · 已连接</p>
          </div>
        </div>
      </header>

      <section className="message-region" aria-live="polite">
        {loadingHistory ? (
          <div className="center-state">
            <LoaderCircle className="spin" size={24} aria-hidden="true" />
            <p>正在读取对话</p>
          </div>
        ) : !hasSession ? (
          <div className="empty-state">
            <img src="/assistant-avatar.png" alt="七海" />
            <h2>还没有对话</h2>
            <p>新的交流可以从这里开始。</p>
            <button className="primary-button" type="button" onClick={onCreateSession}>
              <Plus size={18} aria-hidden="true" />
              新建对话
            </button>
          </div>
        ) : messages.length === 0 && !busy ? (
          <div className="empty-state empty-state--compact">
            <img src="/assistant-avatar.png" alt="七海" />
            <h2>今天想聊些什么？</h2>
            <p>我在这里。</p>
          </div>
        ) : (
          <div className="message-list">
            {messages.map((message) => (
              <article
                className={`message-row message-row--${message.role}`}
                key={message.id}
              >
                {message.role === "assistant" && (
                  <img
                    className="message-avatar"
                    src="/assistant-avatar.png"
                    alt=""
                  />
                )}
                <div className="message-bubble">{message.content}</div>
              </article>
            ))}
            {busy && (
              <div className="progress-line" role="status">
                <LoaderCircle className="spin" size={17} aria-hidden="true" />
                <span>{progress || "正在处理"}</span>
              </div>
            )}
          </div>
        )}
        <div ref={endRef} />
      </section>

      <footer className="composer-area">
        <form className="composer" onSubmit={submit}>
          <textarea
            rows={1}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={hasSession ? "输入消息" : "暂无活动会话"}
            disabled={!hasSession || busy}
            maxLength={20_000}
            aria-label="消息内容"
          />
          {busy ? (
            <button
              className="composer__action composer__action--stop"
              type="button"
              onClick={onCancel}
              aria-label="停止生成"
              title="停止生成"
            >
              <Square size={16} fill="currentColor" aria-hidden="true" />
            </button>
          ) : (
            <button
              className="composer__action"
              type="submit"
              disabled={!hasSession || !draft.trim()}
              aria-label="发送消息"
              title="发送消息"
            >
              <SendHorizontal size={19} aria-hidden="true" />
            </button>
          )}
        </form>
      </footer>
    </main>
  );
}
