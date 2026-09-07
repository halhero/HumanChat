import {
  Brain,
  LoaderCircle,
  Plus,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from "react";

import { createMemory, deleteMemory, listMemories } from "../api";
import type { MemoryItem } from "../types";

interface MemoryDialogProps {
  onClose: () => void;
  onError: (message: string) => void;
}

const dateFormatter = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "short",
  day: "numeric",
});

export function MemoryDialog({ onClose, onError }: MemoryDialogProps) {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [draft, setDraft] = useState("");
  const [loading, setLoading] = useState(true);
  const [mutating, setMutating] = useState(false);
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    controllerRef.current = controller;
    listMemories(controller.signal)
      .then(setItems)
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) {
          onError(errorMessage(reason));
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) {
          setLoading(false);
          window.setTimeout(() => inputRef.current?.focus(), 0);
        }
      });
    return () => controller.abort();
  }, [onError]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || mutating) {
      return;
    }

    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setMutating(true);
    try {
      const item = await createMemory(text, controller.signal);
      setItems((current) => [...current, item]);
      setDraft("");
      inputRef.current?.focus();
    } catch (reason) {
      if (!isAbortError(reason)) {
        onError(errorMessage(reason));
      }
    } finally {
      if (!controller.signal.aborted) {
        setMutating(false);
      }
    }
  };

  const confirmDelete = async (memoryId: string) => {
    if (mutating) {
      return;
    }
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    setMutating(true);
    try {
      await deleteMemory(memoryId, controller.signal);
      setItems((current) => current.filter((item) => item.id !== memoryId));
      setPendingDeleteId(null);
    } catch (reason) {
      if (!isAbortError(reason)) {
        onError(errorMessage(reason));
      }
    } finally {
      if (!controller.signal.aborted) {
        setMutating(false);
      }
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      controllerRef.current?.abort();
      onClose();
    }
  };

  return (
    <div className="dialog-backdrop">
      <section
        className="memory-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="memory-title"
        onKeyDown={handleKeyDown}
      >
        <header className="memory-dialog__header">
          <span className="memory-dialog__icon">
            <Brain size={20} aria-hidden="true" />
          </span>
          <div>
            <h2 id="memory-title">长期记忆</h2>
            <p>已确认保留的用户与项目背景</p>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={() => {
              controllerRef.current?.abort();
              onClose();
            }}
            aria-label="关闭长期记忆"
            title="关闭"
          >
            <X size={18} aria-hidden="true" />
          </button>
        </header>

        <div className="memory-list" aria-live="polite">
          {loading ? (
            <div className="memory-dialog__state">
              <LoaderCircle className="spin" size={20} aria-hidden="true" />
              <span>正在读取</span>
            </div>
          ) : items.length === 0 ? (
            <div className="memory-dialog__state">
              <span>还没有长期记忆</span>
            </div>
          ) : (
            items.map((item) => (
              <article className="memory-item" key={item.id}>
                <div className="memory-item__body">
                  <p>{item.text}</p>
                  <span>
                    {sourceLabel(item.source)} ·{" "}
                    {dateFormatter.format(new Date(item.created_at))}
                  </span>
                </div>
                <div className="memory-item__actions">
                  {pendingDeleteId === item.id ? (
                    <>
                      <button
                        className="icon-button"
                        type="button"
                        onClick={() => setPendingDeleteId(null)}
                        disabled={mutating}
                        aria-label="取消删除"
                        title="取消删除"
                      >
                        <X size={16} aria-hidden="true" />
                      </button>
                      <button
                        className="icon-button icon-button--danger"
                        type="button"
                        onClick={() => void confirmDelete(item.id)}
                        disabled={mutating}
                        aria-label="确认删除"
                        title="确认删除"
                      >
                        {mutating ? (
                          <LoaderCircle
                            className="spin"
                            size={16}
                            aria-hidden="true"
                          />
                        ) : (
                          <Trash2 size={16} aria-hidden="true" />
                        )}
                      </button>
                    </>
                  ) : (
                    <button
                      className="icon-button"
                      type="button"
                      onClick={() => setPendingDeleteId(item.id)}
                      disabled={mutating}
                      aria-label="删除记忆"
                      title="删除记忆"
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  )}
                </div>
              </article>
            ))
          )}
        </div>

        <form className="memory-form" onSubmit={(event) => void submit(event)}>
          <textarea
            ref={inputRef}
            rows={2}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            placeholder="添加一条需要长期记住的内容"
            maxLength={5_000}
            disabled={loading || mutating}
            aria-label="新的长期记忆"
          />
          <button
            className="primary-button memory-form__submit"
            type="submit"
            disabled={loading || mutating || !draft.trim()}
          >
            {mutating && !pendingDeleteId ? (
              <LoaderCircle className="spin" size={17} aria-hidden="true" />
            ) : (
              <Plus size={17} aria-hidden="true" />
            )}
            添加
          </button>
        </form>
      </section>
    </div>
  );
}

function sourceLabel(source: string): string {
  const labels: Record<string, string> = {
    manual: "手动添加",
    extracted_confirmed: "对话确认",
    legacy: "历史数据",
  };
  return labels[source] || "已保存";
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : "长期记忆请求未能完成。";
}

function isAbortError(reason: unknown): boolean {
  return reason instanceof DOMException && reason.name === "AbortError";
}
