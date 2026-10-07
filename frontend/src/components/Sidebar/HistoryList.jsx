import {
  useChat,
} from "../../context/ChatContext";


function formatHistoryTime(value) {
  if (!value) {
    return "";
  }

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return "";
  }

  const now = new Date();
  const diffMs = now.getTime() - date.getTime();

  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;

  if (diffMs < minute) {
    return "Just now";
  }

  if (diffMs < hour) {
    return `${Math.floor(diffMs / minute)}m ago`;
  }

  if (diffMs < day) {
    return `${Math.floor(diffMs / hour)}h ago`;
  }

  if (diffMs < 2 * day) {
    return "Yesterday";
  }

  return date.toLocaleDateString(
    undefined,
    {
      month: "short",
      day: "numeric",
    }
  );
}


function DeleteIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden="true"
      focusable="false"
    >
      <path
        d="M9 3h6m-9 4h12m-10 0 .6 12h6.8L16 7M10 10v6m4-6v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}


export default function HistoryList() {
  const {
    historyItems,
    historyLoading,
    historyError,
    activeHistoryId,
    deletingHistoryId,
    selectHistoryItem,
    deleteHistoryItem,
  } = useChat();


  async function handleDelete(event, item) {
    event.preventDefault();
    event.stopPropagation();

    if (deletingHistoryId) {
      return;
    }

    const confirmed = window.confirm(
      `Delete this chat?\n\n${item.label}\n\nThis cannot be undone.`
    );

    if (!confirmed) {
      return;
    }

    await deleteHistoryItem(item.id);
  }


  return (
    <section className="history-section">
      <div className="section-label">
        Recent
      </div>

      <div
        className="history"
        aria-label="Recent chats"
      >
        {historyLoading && (
          <div className="history-empty">
            Loading chats...
          </div>
        )}

        {!historyLoading &&
          historyError && (
            <div className="history-empty history-error">
              {historyError}
            </div>
          )}

        {!historyLoading &&
          !historyError &&
          historyItems.length === 0 && (
            <div className="history-empty">
              No chats yet
            </div>
          )}

        {!historyLoading &&
          historyItems.map((item) => {
            const isActive =
              item.id === activeHistoryId;

            const isDeleting =
              item.id === deletingHistoryId;

            return (
              <div
                key={item.id}
                className={
                  "history-row" +
                  (isActive ? " active" : "")
                }
              >
                <button
                  type="button"
                  className="history-item"
                  onClick={() =>
                    selectHistoryItem(item.id)
                  }
                  title={item.label}
                  disabled={isDeleting}
                >
                  <div className="history-item-content">
                    <div className="history-item-title">
                      {item.label}
                    </div>

                    <div className="history-item-meta">
                      <span className="history-time">
                        {formatHistoryTime(item.time)}
                      </span>

                      {item.reportName && (
                        <span
                          className="history-report-name"
                          title={item.reportName}
                        >
                          {item.reportName}
                        </span>
                      )}
                    </div>
                  </div>
                </button>

                <button
                  type="button"
                  className="history-delete-btn"
                  onClick={(event) =>
                    handleDelete(event, item)
                  }
                  disabled={Boolean(deletingHistoryId)}
                  aria-label={`Delete ${item.label}`}
                  title="Delete chat"
                >
                  {isDeleting ? (
                    <span className="history-delete-spinner">
                      ...
                    </span>
                  ) : (
                    <DeleteIcon />
                  )}
                </button>
              </div>
            );
          })}
      </div>
    </section>
  );
}
