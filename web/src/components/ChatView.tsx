import {
  FileAudio,
  LoaderCircle,
  Menu,
  Mic,
  Plus,
  SendHorizontal,
  Square,
  Volume2,
  VolumeX,
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
  sttEnabled: boolean;
  ttsEnabled: boolean;
  recording: boolean;
  transcribing: boolean;
  autoSpeak: boolean;
  speechLoadingId: string | null;
  speakingId: string | null;
  onToggleRecording: () => void;
  onAudioFile: (file: File) => void;
  onToggleAutoSpeak: () => void;
  onToggleSpeech: (messageId: string, text: string) => void;
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
  sttEnabled,
  ttsEnabled,
  recording,
  transcribing,
  autoSpeak,
  speechLoadingId,
  speakingId,
  onToggleRecording,
  onAudioFile,
  onToggleAutoSpeak,
  onToggleSpeech,
}: ChatViewProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const audioFileRef = useRef<HTMLInputElement>(null);

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
        {ttsEnabled && (
          <button
            className="icon-button chat-header__voice"
            type="button"
            onClick={onToggleAutoSpeak}
            aria-label={autoSpeak ? "关闭自动朗读" : "开启自动朗读"}
            aria-pressed={autoSpeak}
            title={autoSpeak ? "关闭自动朗读" : "开启自动朗读"}
          >
            {autoSpeak ? (
              <Volume2 size={19} aria-hidden="true" />
            ) : (
              <VolumeX size={19} aria-hidden="true" />
            )}
          </button>
        )}
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
                <div className="message-content">
                  <div className="message-bubble">{message.content}</div>
                  {message.role === "assistant" && ttsEnabled && (
                    <button
                      className="icon-button message-speech"
                      type="button"
                      onClick={() => onToggleSpeech(message.id, message.content)}
                      aria-label={
                        speakingId === message.id ? "停止朗读" : "朗读消息"
                      }
                      title={
                        speakingId === message.id ? "停止朗读" : "朗读消息"
                      }
                    >
                      {speechLoadingId === message.id ? (
                        <LoaderCircle
                          className="spin"
                          size={15}
                          aria-hidden="true"
                        />
                      ) : speakingId === message.id ? (
                        <Square size={12} fill="currentColor" aria-hidden="true" />
                      ) : (
                        <Volume2 size={15} aria-hidden="true" />
                      )}
                    </button>
                  )}
                </div>
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
        <form
          className={`composer ${sttEnabled ? "composer--voice" : ""}`}
          onSubmit={submit}
        >
          {sttEnabled && (
            <div className="composer__voice-tools">
              <input
                ref={audioFileRef}
                className="visually-hidden"
                type="file"
                accept="audio/*,.m4a,.mp3,.wav,.webm,.ogg,.flac"
                aria-hidden="true"
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    onAudioFile(file);
                  }
                  event.target.value = "";
                }}
                tabIndex={-1}
              />
              <button
                className="icon-button composer__voice-action"
                type="button"
                onClick={() => audioFileRef.current?.click()}
                disabled={!hasSession || busy || recording || transcribing}
                aria-label="选择音频文件"
                title="选择音频文件"
              >
                <FileAudio size={18} aria-hidden="true" />
              </button>
              <button
                className={`icon-button composer__voice-action ${
                  recording ? "composer__voice-action--recording" : ""
                }`}
                type="button"
                onClick={onToggleRecording}
                disabled={!hasSession || busy || transcribing}
                aria-label={recording ? "结束录音" : "开始录音"}
                title={recording ? "结束录音" : "开始录音"}
              >
                {transcribing ? (
                  <LoaderCircle className="spin" size={17} aria-hidden="true" />
                ) : recording ? (
                  <Square size={13} fill="currentColor" aria-hidden="true" />
                ) : (
                  <Mic size={18} aria-hidden="true" />
                )}
              </button>
            </div>
          )}
          <textarea
            rows={1}
            value={draft}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={hasSession ? "输入消息" : "暂无活动会话"}
            disabled={!hasSession || busy || transcribing}
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
