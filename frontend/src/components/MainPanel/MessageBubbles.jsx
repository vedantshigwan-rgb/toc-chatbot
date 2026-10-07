import { useChat } from "../../context/ChatContext";

export function UserMessage({ message }) {
  return (
    <div className="msg-user">
      {message.replyingToText && (
        <div className="msg-user-reply-snippet">
          {message.replyingToText.length > 70
            ? message.replyingToText.slice(0, 70) + "…"
            : message.replyingToText}
        </div>
      )}
      {message.parts.map((part, i) =>
        part.pill ? (
          <span key={i} className="var-pill">
            {part.pill}
          </span>
        ) : (
          <span key={i}>{part.text}</span>
        )
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
