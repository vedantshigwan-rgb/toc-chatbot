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
  { value: "metadata", label: "Metadata" },
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
      ? "Ask how to use this report"
      : "Select a report before using Help mode";
  } else if (requestMode === "metadata") {
    placeholder = selectedReport
      ? "Ask about a table, column, or report field"
      : "Select a report before using Metadata mode";
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

      <LookupPopover />

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
          ? "This message will reconstruct and rerun the selected Primary request."
          : selectedReport
            ? "Sidebar options are optional filters. Type / to insert any variable available for this report."
            : "Ask mode can handle greetings without a report. Help, Metadata, and data queries require a report."}
      </div>
    </div>
  );
}
