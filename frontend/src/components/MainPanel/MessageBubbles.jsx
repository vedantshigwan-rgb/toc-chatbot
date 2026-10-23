import { useState } from "react";
import { useChat } from "../../context/ChatContext";


export function UserMessage({ message, isLatest }) {
  const { startEditingMessage, sending, editingMessageId } = useChat();

  const canEdit =
    isLatest === true &&
    message.persisted === true &&
    message.payload?.message_type !== "feedback" &&
    !message.payload?.reply_to_message_id;

  const isEditingThisMessage = editingMessageId === message.id;

  return (
    <div
      className={
        "msg-user" + (isEditingThisMessage ? " msg-user-editing" : "")
      }
    >
      {message.replyingToText && (
        <div className="msg-user-reply-snippet">
          {message.replyingToText.length > 70
            ? message.replyingToText.slice(0, 70) + "…"
            : message.replyingToText}
        </div>
      )}

      <div className="msg-user-content">
        {(message.parts || []).map((part, i) =>
          part.pill ? (
            <span key={i} className="var-pill">
              {part.pill}
            </span>
          ) : (
            <span key={i}>{part.text}</span>
          )
        )}
      </div>

      {message.isEdited && (
        <div className="msg-edited-label">Edited</div>
      )}

      {canEdit && (
        <div className="msg-user-actions">
          <button
            type="button"
            className="msg-edit-icon-btn"
            onClick={() => startEditingMessage(message)}
            disabled={sending}
            title="Edit this message"
            aria-label="Edit this message"
          >
            <svg
              viewBox="0 0 24 24"
              width="16"
              height="16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M12 20h9" />
              <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L8 18l-4 1 1-4Z" />
            </svg>
          </button>
        </div>
      )}
    </div>
  );
}



export function BotMessage({ message }) {
  const { feedback, replyTo, openReport } = useChat();

  const canModify =
    message.persisted === true &&
    message.backendResponse?.workflow_used === "primary" &&
    message.backendResponse?.status !== "failed";

  return (
    <div className="msg-bot">
      <div className="msg-bot-text">{message.text}</div>
      <div className="feedback">
        <button
          title="Good response"
          className={message.feedback === "up" ? "active-up" : ""}
          onClick={() => feedback(message.id, "up")}
        >
          👍
        </button>
        <button
          title="Bad response"
          className={message.feedback === "down" ? "active-down" : ""}
          onClick={() => feedback(message.id, "down")}
        >
          👎
        </button>

        {canModify && (
          <>
            <span className="divider" />
            <button
              title="Modify this Primary result"
              onClick={() => replyTo(message.id)}
            >
              ↩ Modify
            </button>
          </>
        )}

        <span className="divider" />
        <button
          className={
            "table-result-btn" +
            (message.hasTable || message.reportKey ? "" : " disabled")
          }
          title={
            message.hasTable
              ? `View ${message.tableRows.length} returned rows`
              : message.reportKey
                ? "View report"
                : "No table available for this response"
          }
          disabled={!message.hasTable && !message.reportKey}
          onClick={() => {
            if (message.hasTable) {
              openReport({
                title: message.tableTitle,
                rows: message.tableRows,
                messageId: message.id,
              });
              return;
            }

            if (message.reportKey) {
              openReport(message.reportKey);
            }
          }}
        >
          ▦ Table
        </button>
      </div>
    </div>
  );
}

export function SuggestBlock({ message }) {
  const { pickSuggestOption, pickOther } = useChat();

  return (
    <div className="suggest-block">
      <div className="suggest-label">{message.label}</div>
      <div className="suggest-options">
        {message.options.map((opt) => (
          <button
            key={opt}
            className="suggest-chip"
            onClick={() => pickSuggestOption(message.id, opt)}
          >
            {opt}
          </button>
        ))}
        <button
          className="suggest-chip suggest-other-btn"
          onClick={() => pickOther(message.id)}
        >
          Other — none of these
        </button>
      </div>
    </div>
  );
}

export function ConfirmMessage({ message }) {
  return <div className="confirm-msg">✓ {message.text}</div>;
}
