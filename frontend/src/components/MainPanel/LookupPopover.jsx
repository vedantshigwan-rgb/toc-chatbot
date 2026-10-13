import {
  useEffect,
  useRef,
} from "react";

import {
  useChat,
} from "../../context/ChatContext";


export default function LookupPopover() {
  const {
    lookup,
    requestMode,
    replyingTo,
    selectLookupValue,
    selectDateValue,
  } = useChat();

  const dateInputRef =
    useRef(null);


  useEffect(() => {
    if (
      !lookup.open ||
      lookup.mode !== "date"
    ) {
      return undefined;
    }

    const frameId =
      requestAnimationFrame(() => {
        const input =
          dateInputRef.current;

        if (!input) {
          return;
        }

        input.focus();

        try {
          if (
            typeof input.showPicker ===
            "function"
          ) {
            input.showPicker();
          }
        } catch {
          /*
           * Some browsers require a direct user
           * gesture before showPicker(). The date
           * field remains visible and clickable.
           */
        }
      });

    return () => {
      cancelAnimationFrame(frameId);
    };
  }, [
    lookup.open,
    lookup.mode,
    lookup.activeVar,
  ]);


  if (!lookup.open || requestMode !== "normal_message" || replyingTo) {
    return null;
  }


  const title =
    lookup.mode === "variable"
      ? "Select a variable"
      : lookup.mode === "date"
        ? "Select a date"
        : `Select a value for /${lookup.activeVar}`;


  function openNativeDatePicker(event) {
    try {
      event.currentTarget
        .showPicker?.();
    } catch {
      // Native browser fallback.
    }
  }


  function renderContent() {
    if (lookup.mode === "date") {
      return (
        <div className="date-picker-container">
          <input
            ref={dateInputRef}
            className="date-picker-input"
            type="date"
            aria-label="Select date"
            onFocus={openNativeDatePicker}
            onClick={openNativeDatePicker}
            onChange={(event) => {
              const selectedDate =
                event.target.value;

              if (selectedDate) {
                selectDateValue(
                  selectedDate
                );
              }
            }}
          />

          <div className="date-picker-hint">
            Date will be inserted as DD/MM/YYYY
          </div>
        </div>
      );
    }

    if (lookup.loading) {
      return (
        <div className="lookup-status">
          Searching values...
        </div>
      );
    }

    if (lookup.error) {
      return (
        <div className="lookup-status lookup-error">
          {lookup.error}
        </div>
      );
    }

    if (lookup.message) {
      return (
        <div className="lookup-status">
          {lookup.message}
        </div>
      );
    }

    if (lookup.options.length === 0) {
      return (
        <div className="lookup-status">
          {lookup.mode === "variable"
            ? "No matching variables"
            : "No matching values"}
        </div>
      );
    }

    return lookup.options.map(
      (option, index) => (
        <div
          key={`${option}-${index}`}
          className={
            "lookup-option" +
            (
              index ===
              lookup.highlightIndex
                ? " highlight"
                : ""
            )
          }
          onMouseDown={(event) => {
            event.preventDefault();

            selectLookupValue(option);
          }}
        >
          {lookup.mode === "variable"
            ? `/${option}`
            : option}
        </div>
      )
    );
  }


  return (
    <div className="lookup-popover show">
      <div className="lookup-title">
        {title}
      </div>

      <div className="lookup-results">
        {renderContent()}
      </div>
    </div>
  );
}
