import {
  Check,
  ListChecks,
  MessageSquareText,
  Play,
  Send,
  ShieldAlert,
  X,
} from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import type { ReviewDecision, ReviewRequest } from "../types";

interface ReviewDialogProps {
  review: ReviewRequest;
  submitting: boolean;
  onDecision: (decision: ReviewDecision) => void;
}

export function ReviewDialog({
  review,
  submitting,
  onDecision,
}: ReviewDialogProps) {
  const [message, setMessage] = useState("");
  const normalizedMessage = message.trim();

  useEffect(() => setMessage(""), [review]);

  if (review.kind === "tool") {
    return (
      <DialogFrame
        icon={<ShieldAlert size={20} aria-hidden="true" />}
        title={review.title}
        description={review.description}
      >
        <div className="review-list">
          {review.items.map((item) => (
            <div className="review-item" key={item.id}>
              <div>
                <strong>{item.title}</strong>
                {item.description && <p>{item.description}</p>}
              </div>
              {item.details && (
                <pre>{JSON.stringify(item.details, null, 2)}</pre>
              )}
            </div>
          ))}
        </div>
        <div className="review-dialog__actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => onDecision({ decision: "reject" })}
            disabled={submitting}
          >
            <X size={17} aria-hidden="true" />
            拒绝
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={() => onDecision({ decision: "approve" })}
            disabled={submitting}
          >
            <Check size={17} aria-hidden="true" />
            确认
          </button>
        </div>
      </DialogFrame>
    );
  }

  if (review.kind === "user_input") {
    return (
      <DialogFrame
        icon={<MessageSquareText size={20} aria-hidden="true" />}
        title="需要你补充信息"
        description={review.question}
      >
        <label className="review-dialog__input">
          <span>你的回答</span>
          <textarea
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            rows={4}
            autoFocus
          />
        </label>
        <div className="review-dialog__actions">
          <button
            className="secondary-button"
            type="button"
            onClick={() => onDecision({ decision: "stop" })}
            disabled={submitting}
          >
            <X size={17} aria-hidden="true" />
            停止
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={() =>
              onDecision({ decision: "respond", message: normalizedMessage })
            }
            disabled={submitting || !normalizedMessage}
          >
            <Send size={17} aria-hidden="true" />
            提交
          </button>
        </div>
      </DialogFrame>
    );
  }

  return (
    <DialogFrame
      icon={<ListChecks size={20} aria-hidden="true" />}
      title="继续执行计划"
      description={`已执行 ${review.completed_steps} 次尝试，本轮硬上限为 ${review.hard_limit} 次。`}
    >
      <div className="review-list" aria-label="剩余步骤">
        {review.remaining_steps.map((step, index) => (
          <div className="review-item" key={`${index}-${step}`}>
            <span>{step}</span>
          </div>
        ))}
      </div>
      <label className="review-dialog__input">
        <span>可选补充</span>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          rows={3}
        />
      </label>
      <div className="review-dialog__actions review-dialog__actions--wrap">
        <button
          className="secondary-button"
          type="button"
          onClick={() => onDecision({ decision: "stop" })}
          disabled={submitting}
        >
          <X size={17} aria-hidden="true" />
          停止任务
        </button>
        <button
          className="secondary-button"
          type="button"
          onClick={() =>
            onDecision({
              decision: "continue_with_input",
              message: normalizedMessage,
            })
          }
          disabled={submitting || !normalizedMessage}
        >
          <MessageSquareText size={17} aria-hidden="true" />
          补充后继续
        </button>
        <button
          className="primary-button"
          type="button"
          onClick={() => onDecision({ decision: "continue" })}
          disabled={submitting}
        >
          <Play size={17} aria-hidden="true" />
          继续执行
        </button>
      </div>
    </DialogFrame>
  );
}

interface DialogFrameProps {
  icon: ReactNode;
  title: string;
  description: string;
  children: ReactNode;
}

function DialogFrame({
  icon,
  title,
  description,
  children,
}: DialogFrameProps) {
  return (
    <div className="dialog-backdrop">
      <section
        className="review-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="review-title"
        aria-describedby="review-description"
      >
        <div className="review-dialog__heading">
          <span className="review-dialog__icon">{icon}</span>
          <div>
            <h2 id="review-title">{title}</h2>
            <p id="review-description">{description}</p>
          </div>
        </div>
        {children}
      </section>
    </div>
  );
}
