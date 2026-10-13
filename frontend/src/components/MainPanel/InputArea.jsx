import {
  useEffect,
  useRef,
} from "react";

import { useChat } from "../../context/ChatContext";
import ReplyBanner from "./ReplyBanner";
import LookupPopover from "./LookupPopover";

const MODES = [
  { value: "normal_message", label: "Ask" },
  { value: "help", label: "Help" },
  { value: "metadata", label: "Report Terms" },
];

export default function InputArea() {
  const {
    chatInput,
    sendMessage,
    onChatInputChange,
    onChatCursorChange,
    onChatInputKeyDown,
    requestedCaretPosition,
    clearRequestedCaretPosition,
    inputDisabled,
    sending,
    selectedReport,
    requestMode,
    setRequestMode,
    replyingTo,
  } = useChat();

  const chatInputRef = useRef(null);

  useEffect(() => {
    if (requestedCaretPosition === null) return;

    requestAnimationFrame(() => {
      const input = chatInputRef.current;
      if (!input) return;

      input.focus();
      input.setSelectionRange(
        requestedCaretPosition,
        requestedCaretPosition
      );
      clearRequestedCaretPosition();
    });
  }, [requestedCaretPosition, clearRequestedCaretPosition]);

  const reportRequired =
    !replyingTo &&
    (requestMode === "help" || requestMode === "metadata");

  const chatDisabled =
    inputDisabled ||
    sending ||
    (reportRequired && !selectedReport);

  let placeholder = "Ask a question";

  if (sending) {
    placeholder = "Waiting for the response...";
  } else if (replyingTo) {
    placeholder = "Describe how this Primary result should change";
  } else if (requestMode === "help") {
    placeholder = selectedReport
      ? "Ask about report variables or how to use the application"
      : "Select a report before using Help mode";
  } else if (requestMode === "metadata") {
    placeholder = selectedReport
      ? "Ask what a term or column means in this report"
      : "Select a report before using Report Terms mode";
  } else if (selectedReport) {
    placeholder = "Ask about this report, or type / to insert a variable";
  } else {
    placeholder = "Say hello, or ask a question. Data queries will ask you to select a report.";
  }

  function handleCursorChange(event) {
    onChatCursorChange(
      event.currentTarget.value,
      event.currentTarget.selectionStart
    );
  }

  return (
    <div className="input-area">
      <ReplyBanner />

      <div className="request-mode-row" aria-label="Request mode">
        {MODES.map((mode) => (
          <button
            key={mode.value}
            type="button"
            className={
              "request-mode-btn" +
              (requestMode === mode.value && !replyingTo ? " active" : "")
            }
            disabled={Boolean(replyingTo) || sending}
            onClick={() => setRequestMode(mode.value)}
          >
            {mode.label}
          </button>
        ))}
        {replyingTo && (
          <span className="request-mode-feedback">Feedback / Modify</span>
        )}
      </div>

      {!replyingTo && requestMode === "normal_message" && <LookupPopover />}

      <div className="input-row">
        <input
          ref={chatInputRef}
          id="chatInput"
          className="chat-input"
          type="text"
          value={chatInput}
          placeholder={placeholder}
          disabled={chatDisabled}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            onChatInputChange(
              event.target.value,
              event.target.selectionStart
            );
          }}
          onClick={handleCursorChange}
          onSelect={handleCursorChange}
          onKeyUp={(event) => {
            if (
              event.key === "ArrowLeft" ||
              event.key === "ArrowRight" ||
              event.key === "Home" ||
              event.key === "End"
            ) {
              handleCursorChange(event);
            }
          }}
          onKeyDown={onChatInputKeyDown}
        />

        <button
          type="button"
          className="send-btn"
          disabled={chatDisabled || !chatInput.trim()}
          onClick={sendMessage}
        >
          {sending ? "…" : "➤"}
        </button>
      </div>

      <div className="hint">
        {replyingTo
          ? "This message will refine and rerun the selected data request."
          : requestMode === "help"
            ? "Ask about how the application works or what a report variable means."
            : requestMode === "metadata"
              ? "Learn what a business term or report column means. No filters are needed."
              : selectedReport
                ? "Sidebar filters are optional. Type / to insert a variable."
                : "You can say hello without selecting a report. Select a report for data questions."}
      </div>
    </div>
  );
}
