import { Check, ShieldAlert, X } from "lucide-react";

import type { ReviewRequest } from "../types";

interface ReviewDialogProps {
  review: ReviewRequest;
  selectedIds: Set<string>;
  submitting: boolean;
  onToggle: (itemId: string) => void;
  onApprove: () => void;
  onReject: () => void;
}

export function ReviewDialog({
  review,
  selectedIds,
  submitting,
  onToggle,
  onApprove,
  onReject,
}: ReviewDialogProps) {
  const approveDisabled =
    submitting || (review.selectable && selectedIds.size === 0);

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
          <span className="review-dialog__icon">
            <ShieldAlert size={20} aria-hidden="true" />
          </span>
          <div>
            <h2 id="review-title">{review.title}</h2>
            <p id="review-description">{review.description}</p>
          </div>
        </div>

        <div className="review-list">
          {review.items.map((item) =>
            review.selectable ? (
              <label className="review-item review-item--selectable" key={item.id}>
                <input
                  type="checkbox"
                  checked={selectedIds.has(item.id)}
                  onChange={() => onToggle(item.id)}
                  disabled={submitting}
                />
                <span>{item.title}</span>
              </label>
            ) : (
              <div className="review-item" key={item.id}>
                <div>
                  <strong>{item.title}</strong>
                  {item.description && <p>{item.description}</p>}
                </div>
                {item.details && (
                  <pre>{JSON.stringify(item.details, null, 2)}</pre>
                )}
              </div>
            ),
          )}
        </div>

        <div className="review-dialog__actions">
          <button
            className="secondary-button"
            type="button"
            onClick={onReject}
            disabled={submitting}
          >
            <X size={17} aria-hidden="true" />
            拒绝
          </button>
          <button
            className="primary-button"
            type="button"
            onClick={onApprove}
            disabled={approveDisabled}
          >
            <Check size={17} aria-hidden="true" />
            确认
          </button>
        </div>
      </section>
    </div>
  );
}
