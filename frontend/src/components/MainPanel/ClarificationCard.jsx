import { useState } from "react";
import { useChat } from "../../context/ChatContext";

/*
 * Renders a backend structured clarification (Step 37) as an
 * interactive workflow card - never as plain assistant text. Submits
 * through the SAME /ask contract as any other message
 * (message_type=clarification_response, clarification_id, values),
 * via ChatContext.submitClarification(), which uses the dedicated
 * resume path - never a raw text message. Only the field key/label/
 * input_type/options the backend actually sent are rendered; no
 * internal resume_action/handler name is ever shown.
 */

function defaultValueForField(field) {
  switch (field.input_type) {
    case "multi_select":
      return [];
    case "boolean":
      return null;
    case "date_range":
      return { start: "", end: "" };
    default:
      return "";
  }
}

function ClarificationField({ field, value, onChange, disabled }) {
  switch (field.input_type) {
    case "text":
      return (
        <input
          type="text"
          className="clarification-input"
          value={value ?? ""}
          disabled={disabled}
          placeholder={field.placeholder || ""}
          onChange={(event) => onChange(event.target.value)}
        />
      );

    case "number":
      return (
        <input
          type="number"
          className="clarification-input"
          value={value ?? ""}
          disabled={disabled}
          placeholder={field.placeholder || ""}
          onChange={(event) => onChange(event.target.value)}
        />
      );

    case "date":
      return (
        <input
          type="date"
          className="clarification-input"
          value={value ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      );

    case "date_range":
      return (
        <div className="clarification-date-range">
          <input
            type="date"
            className="clarification-input"
            value={value?.start ?? ""}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, start: event.target.value })
            }
          />
          <span>to</span>
          <input
            type="date"
            className="clarification-input"
            value={value?.end ?? ""}
            disabled={disabled}
            onChange={(event) =>
              onChange({ ...value, end: event.target.value })
            }
          />
        </div>
      );

    case "boolean":
      return (
        <div className="clarification-boolean">
          <button
            type="button"
            className={
              "clarification-chip" +
              (value === true ? " selected" : "")
            }
            disabled={disabled}
            onClick={() => onChange(true)}
          >
            Yes
          </button>
          <button
            type="button"
            className={
              "clarification-chip" +
              (value === false ? " selected" : "")
            }
            disabled={disabled}
            onClick={() => onChange(false)}
          >
            No
          </button>
        </div>
      );

    case "single_select":
      return (
        <div className="clarification-options">
          {(field.options || []).map((option) => (
            <button
              type="button"
              key={String(option.value)}
              className={
                "clarification-chip" +
                (value === option.value ? " selected" : "")
              }
              disabled={disabled}
              title={option.description || undefined}
              onClick={() => onChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      );

    case "multi_select": {
      const selected = Array.isArray(value) ? value : [];

      return (
        <div className="clarification-options">
          {(field.options || []).map((option) => {
            const isSelected = selected.includes(option.value);

            return (
              <button
                type="button"
                key={String(option.value)}
                className={
                  "clarification-chip" +
                  (isSelected ? " selected" : "")
                }
                disabled={disabled}
                title={option.description || undefined}
                onClick={() =>
                  onChange(
                    isSelected
                      ? selected.filter(
                          (item) => item !== option.value
                        )
                      : [...selected, option.value]
                  )
                }
              >
                {option.label}
              </button>
            );
          })}
        </div>
      );
    }

    default:
      // An input type the frontend does not yet recognise - fall
      // back to free text rather than silently dropping the field.
      return (
        <input
          type="text"
          className="clarification-input"
          value={value ?? ""}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
        />
      );
  }
}

function isFieldFilled(field, value) {
  if (!field.required) {
    return true;
  }

  switch (field.input_type) {
    case "multi_select":
      return Array.isArray(value) && value.length > 0;
    case "boolean":
      return value === true || value === false;
    case "date_range":
      return Boolean(value?.start) && Boolean(value?.end);
    default:
      return value !== "" && value !== null && value !== undefined;
  }
}

export default function ClarificationCard({ message }) {
  const { submitClarification, cancelClarification, sending } =
    useChat();

  const [draft, setDraft] = useState(() => {
    const initial = {};

    for (const field of message.fields) {
      initial[field.key] = defaultValueForField(field);
    }

    return initial;
  });

  const isPending = message.status === "pending";
  const isBusy =
    message.status === "submitting" ||
    message.status === "cancelling" ||
    sending;

  const canSubmit =
    isPending &&
    !isBusy &&
    message.fields.every((field) =>
      isFieldFilled(field, draft[field.key])
    );

  function updateField(fieldKey, value) {
    setDraft((current) => ({ ...current, [fieldKey]: value }));
  }

  function handleSubmit() {
    if (!canSubmit) {
      return;
    }

    submitClarification(message.id, draft);
  }

  return (
    <div className={"clarification-card status-" + message.status}>
      <div className="clarification-label">
        {message.status === "resolved"
          ? "Clarification answered"
          : message.status === "cancelled"
            ? "Clarification cancelled"
            : message.status === "error"
              ? "Clarification unavailable"
              : "Clarification needed"}
      </div>

      <div className="clarification-question">
        {message.message}
      </div>

      {message.status === "error" && (
        <div className="clarification-error">
          {message.errorMessage}
        </div>
      )}

      {(isPending || isBusy) && (
        <>
          <div className="clarification-fields">
            {message.fields.map((field) => (
              <div className="clarification-field" key={field.key}>
                <label className="clarification-field-label">
                  {field.label}
                  {field.required ? " *" : ""}
                </label>

                {field.description && (
                  <div className="clarification-field-hint">
                    {field.description}
                  </div>
                )}

                <ClarificationField
                  field={field}
                  value={draft[field.key]}
                  disabled={isBusy}
                  onChange={(value) =>
                    updateField(field.key, value)
                  }
                />
              </div>
            ))}
          </div>

          <div className="clarification-actions">
            <button
              type="button"
              className="clarification-submit-btn"
              disabled={!canSubmit}
              onClick={handleSubmit}
            >
              {message.status === "submitting"
                ? "Submitting…"
                : "Submit"}
            </button>

            {message.canCancel && (
              <button
                type="button"
                className="clarification-cancel-btn"
                disabled={isBusy}
                onClick={() => cancelClarification(message.id)}
              >
                {message.status === "cancelling"
                  ? "Cancelling…"
                  : "Cancel"}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
