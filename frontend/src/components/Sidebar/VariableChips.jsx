import {
  useEffect,
  useMemo,
  useState,
} from "react";

import {
  useChat,
} from "../../context/ChatContext";


export default function VariableChips() {
  const {
    report,
    reportVariables,
    reportVariablesLoading,
    reportVariablesError,
    insertVar,
  } = useChat();


  const [
    plateOpen,
    setPlateOpen,
  ] = useState(false);

  const [
    variableSearch,
    setVariableSearch,
  ] = useState("");


  const variables =
    Array.isArray(reportVariables)
      ? reportVariables
      : [];


  const filteredVariables =
    useMemo(() => {
      const search =
        variableSearch
          .trim()
          .toLowerCase();

      if (!search) {
        return variables;
      }

      return variables.filter(
        (variable) => {
          const searchableText = [
            variable.variable_name,
            variable.column_name,
            variable.storage_type,
          ]
            .join(" ")
            .toLowerCase();

          return searchableText.includes(
            search
          );
        }
      );
    }, [
      variables,
      variableSearch,
    ]);


  useEffect(() => {
    setPlateOpen(false);
    setVariableSearch("");
  }, [
    report.id,
  ]);


  useEffect(() => {
    if (!plateOpen) {
      return undefined;
    }

    function handleEscape(event) {
      if (event.key === "Escape") {
        setPlateOpen(false);
        setVariableSearch("");
      }
    }

    document.addEventListener(
      "keydown",
      handleEscape
    );

    return () => {
      document.removeEventListener(
        "keydown",
        handleEscape
      );
    };
  }, [
    plateOpen,
  ]);


  function closePlate() {
    setPlateOpen(false);
    setVariableSearch("");
  }


  function selectVariable(variable) {
    insertVar(
      variable.variable_name
    );

    closePlate();
  }


  if (!report.id) {
    return null;
  }


  return (
    <section className="variable-sidebar-block">
      <div className="variable-sidebar-heading">
        <div>
          <div className="section-label variable-section-label">
            Variables for this report
          </div>

          <div className="variable-sidebar-summary">
            {reportVariablesLoading
              ? "Loading variables..."
              : reportVariablesError
                ? "Variables unavailable"
                : variables.length === 0
                  ? "No variables available"
                  : `${variables.length} ${
                      variables.length === 1
                        ? "variable"
                        : "variables"
                    } available`}
          </div>
        </div>


        <button
          type="button"
          className="variable-plate-trigger"
          aria-label="View all report variables"
          title="View all report variables"
          disabled={
            reportVariablesLoading ||
            variables.length === 0
          }
          onClick={() => {
            setPlateOpen(true);
          }}
        >
          <svg
            width="18"
            height="18"
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <rect
              x="3"
              y="3"
              width="7"
              height="7"
              rx="1.5"
            />

            <rect
              x="14"
              y="3"
              width="7"
              height="7"
              rx="1.5"
            />

            <rect
              x="3"
              y="14"
              width="7"
              height="7"
              rx="1.5"
            />

            <rect
              x="14"
              y="14"
              width="7"
              height="7"
              rx="1.5"
            />
          </svg>

          <span className="variable-trigger-count">
            {variables.length}
          </span>
        </button>
      </div>


      {reportVariablesError && (
        <div className="report-variable-warning">
          {reportVariablesError}
        </div>
      )}


      {plateOpen && (
        <div
          className="variable-plate-overlay"
          onMouseDown={(event) => {
            if (
              event.target ===
              event.currentTarget
            ) {
              closePlate();
            }
          }}
        >
          <section
            className="variable-plate-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="report-variables-title"
          >
            <header className="variable-plate-header">
              <div>
                <h2
                  id="report-variables-title"
                  className="variable-plate-title"
                >
                  Report variables
                </h2>

                <p className="variable-plate-subtitle">
                  {report.name}
                  {" · "}
                  {variables.length}
                  {" "}
                  {variables.length === 1
                    ? "variable"
                    : "variables"}
                </p>
              </div>

              <button
                type="button"
                className="variable-plate-close"
                aria-label="Close report variables"
                onClick={closePlate}
              >
                ×
              </button>
            </header>


            <div className="variable-plate-search-wrap">
              <svg
                className="variable-search-icon"
                width="17"
                height="17"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <circle
                  cx="11"
                  cy="11"
                  r="7"
                />

                <path
                  d="M16.5 16.5L21 21"
                />
              </svg>

              <input
                type="text"
                className="variable-plate-search"
                value={variableSearch}
                autoFocus
                autoComplete="off"
                placeholder="Search variables..."
                onChange={(event) => {
                  setVariableSearch(
                    event.target.value
                  );
                }}
              />
            </div>


            <div className="variable-plate-body">
              {filteredVariables.length === 0 ? (
                <div className="variable-plate-empty">
                  No matching variables
                </div>
              ) : (
                <div className="variable-plate-grid">
                  {filteredVariables.map(
                    (variable) => (
                      <button
                        key={variable.id}
                        type="button"
                        className="variable-plate-item"
                        onClick={() => {
                          selectVariable(
                            variable
                          );
                        }}
                      >
                        <div className="variable-option-content">
                          <span className="variable-plate-mapping">
                            /{variable.variable_name}
                          </span>

                          <span className="variable-column-name">
                            {variable.column_name}
                          </span>

                          <span
                            className={
                              variable.autocomplete_enabled
                                ? "variable-storage redis"
                                : "variable-storage manual"
                            }
                          >
                            {variable.autocomplete_enabled
                              ? "Redis autocomplete"
                              : "Non-Redis · manual value"}
                          </span>
                        </div>

                        <span className="variable-plate-add">
                          +
                        </span>
                      </button>
                    )
                  )}
                </div>
              )}
            </div>


            <footer className="variable-plate-footer">
              All variables come from redis mapping.xlsx.
              Redis variables show suggestions; non-Redis
              variables accept manual values.
            </footer>
          </section>
        </div>
      )}
    </section>
  );
}
