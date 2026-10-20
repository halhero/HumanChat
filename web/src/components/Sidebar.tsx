import { MessageSquare, PanelLeftClose, Plus } from "lucide-react";

import type { SessionSummary } from "../types";

interface SidebarProps {
  sessions: SessionSummary[];
  activeSessionId: string | null;
  open: boolean;
  locked: boolean;
  onClose: () => void;
  onCreate: () => void;
  onSelect: (sessionId: string) => void;
}

const timeFormatter = new Intl.DateTimeFormat("zh-CN", {
  month: "numeric",
  day: "numeric",
});

export function Sidebar({
  sessions,
  activeSessionId,
  open,
  locked,
  onClose,
  onCreate,
  onSelect,
}: SidebarProps) {
  return (
    <>
      <aside className={`sidebar ${open ? "sidebar--open" : ""}`}>
        <div className="sidebar__header">
          <div>
            <p className="brand">HumanChat</p>
            <p className="brand-caption">Agent workspace</p>
          </div>
          <div className="sidebar__actions">
            <button
              className="icon-button"
              type="button"
              onClick={onCreate}
              disabled={locked}
              aria-label="新建对话"
              title="新建对话"
            >
              <Plus size={18} aria-hidden="true" />
            </button>
            <button
              className="icon-button sidebar__close"
              type="button"
              onClick={onClose}
              aria-label="关闭会话列表"
              title="关闭会话列表"
            >
              <PanelLeftClose size={18} aria-hidden="true" />
            </button>
          </div>
        </div>

        <nav className="session-list" aria-label="会话列表">
          {sessions.length === 0 ? (
            <p className="session-list__empty">还没有对话</p>
          ) : (
            sessions.map((session) => (
              <button
                className={`session-item ${
                  session.id === activeSessionId ? "session-item--active" : ""
                }`}
                type="button"
                key={session.id}
                onClick={() => onSelect(session.id)}
                disabled={locked && session.id !== activeSessionId}
              >
                <MessageSquare size={16} aria-hidden="true" />
                <span className="session-item__body">
                  <span className="session-item__title">{session.title}</span>
                  <span className="session-item__meta">
                    {timeFormatter.format(new Date(session.updated_at))}
                    {session.message_count > 0
                      ? ` · ${session.message_count} 条消息`
                      : " · 新对话"}
                  </span>
                </span>
              </button>
            ))
          )}
        </nav>
      </aside>
      {open && (
        <button
          className="sidebar-backdrop"
          type="button"
          onClick={onClose}
          aria-label="关闭会话列表"
        />
      )}
    </>
  );
}
