import {
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  useChat,
} from "../../context/ChatContext";

import {
  fetchAutocompleteValues,
  fetchPeriodOptions,
} from "../../services/autocompleteApi";

import Dropdown from "./Dropdown";
import VariableChips from "./VariableChips";
import HistoryList from "./HistoryList";
import UserProfile from "./UserProfile";


function normalizeIdentifier(value) {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}


function normalizeReportName(value) {
  return String(value || "")
    .trim()
    .toLowerCase();
}


function getLabel(item) {
  return (
    item?.label ||
    item?.report_name ||
    item?.name ||
    item?.id ||
    ""
  );
}


function buildReportChoices(baseTabs) {
  const reportsByName = new Map();

  for (const baseTab of baseTabs || []) {
    for (const subTab of baseTab.sub_tabs || []) {
      for (const reportItem of subTab.reports || []) {
        const reportName =
          String(
            reportItem?.report_name ||
            reportItem?.label ||
            ""
          ).trim();

        const reportKey =
          normalizeReportName(reportName);

        if (!reportKey) {
          continue;
        }

        /*
         * One logical report is shown once even when Roles and options.xlsx
         * places the same report under multiple old Base/Sub-tab paths.
         * selectReport() still receives the original report id and restores
         * the hidden path internally for the rest of the application.
         */
        if (!reportsByName.has(reportKey)) {
          reportsByName.set(
            reportKey,
            {
              ...reportItem,
              label:
                getLabel(reportItem) ||
                reportName,
            }
          );
        }
      }
    }
  }

  return Array.from(
    reportsByName.values()
  ).sort((a, b) => {
    const nameA = String(
      a.label || a.report_name || ""
    ).trim();

    const nameB = String(
      b.label || b.report_name || ""
    ).trim();

    return nameA.localeCompare(
      nameB,
      "en",
      {
        sensitivity: "base",
        numeric: true,
      }
    );
  });
}


function ReportOptionControl({
  option,
  selectedReport,
  reportVariables,
  controllerVariableName,
  controllerValue,
  periodTypeValue,
  value,
  onValueChange,
}) {
  const [search, setSearch] =
    useState(value || "");

  const [values, setValues] =
    useState([]);

  const [hasMore, setHasMore] = useState(false);
  const [nextOffset, setNextOffset] = useState(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [pageError, setPageError] = useState(null);
  const pageVersionRef = useRef(0);
  const moreAbortRef = useRef(null);
  const moreBusyRef = useRef(false);

  const [loading, setLoading] =
    useState(false);

  const [error, setError] =
    useState(null);

  const [focused, setFocused] =
    useState(false);

  const pickerRef = useRef(null);

  // Unlike the Report picker, filter options previously remained open
  // when a user clicked anywhere outside the options list.
  useEffect(() => {
    if (!focused) {
      return undefined;
    }

    function handleOutsidePointerDown(event) {
      if (
        pickerRef.current &&
        !pickerRef.current.contains(event.target)
      ) {
        setFocused(false);
      }
    }

    function handleEscape(event) {
      if (event.key === "Escape") {
        setFocused(false);
      }
    }

    document.addEventListener("pointerdown", handleOutsidePointerDown);
    document.addEventListener("keydown", handleEscape);

    return () => {
      document.removeEventListener("pointerdown", handleOutsidePointerDown);
      document.removeEventListener("keydown", handleEscape);
    };
  }, [focused]);


  const optionVariable =
    useMemo(() => {
      const optionVariableId =
        normalizeIdentifier(
          option?.variable_name
        );

      return (
        reportVariables.find(
          (variable) =>
            normalizeIdentifier(
              variable?.variable_name
            ) === optionVariableId
        ) || null
      );
    }, [
      option,
      reportVariables,
    ]);


  const reportName = String(selectedReport?.report_name || "").trim();
  const isPeriodReport = [
    "Docket Report", "Docket Employee Report"
  ].includes(reportName);
  const isPeriodTypeOption =
    isPeriodReport && normalizeIdentifier(option?.variable_name) === "period_type";
  const isPeriodNameOption =
    isPeriodReport && normalizeIdentifier(option?.variable_name) === "period_name";
  const autocompleteEnabled =
    isPeriodTypeOption || isPeriodNameOption ||
    optionVariable?.autocomplete_enabled === true ||
    option?.autocomplete_enabled === true;


  const variableName =
    String(
      optionVariable?.variable_name ||
      option?.variable_name ||
      ""
    ).trim();


  const controllerId =
    normalizeIdentifier(
      controllerVariableName
    );

  const variableId =
    normalizeIdentifier(
      variableName
    );

  const isController =
    Boolean(
      controllerId &&
      variableId === controllerId
    );

  const requiresController = Boolean(
    (controllerId && !isController && !String(controllerValue || "").trim()) ||
    (isPeriodNameOption && !String(periodTypeValue || "").trim())
  );


  useEffect(() => {
    setSearch(value || "");
  }, [value]);


  // Both org-wise lists and Docket period lists support offset paging.
  async function requestOptionsPage(offset, signal) {
    const resolvedReportName =
      selectedReport.autocomplete_report_name || selectedReport.report_name;
    const query = String(search || "").trim();
    return (isPeriodTypeOption || isPeriodNameOption)
      ? fetchPeriodOptions({
          reportName: resolvedReportName,
          orgValue: controllerValue,
          periodType: isPeriodNameOption ? periodTypeValue : "",
          query,
          limit: 30,
          offset,
          signal,
        })
      : fetchAutocompleteValues({
          reportName: resolvedReportName,
          variableName,
          query,
          limit: 30,
          offset,
          controllerVariableName: isController ? "" : controllerVariableName,
          controllerValue: isController ? "" : controllerValue,
          signal,
        });
  }

  useEffect(() => {
    const version = ++pageVersionRef.current;
    moreAbortRef.current?.abort();
    moreAbortRef.current = null;
    moreBusyRef.current = false;
    setValues([]);
    setHasMore(false);
    setNextOffset(null);
    setLoadingMore(false);
    setPageError(null);
    setError(null);

    if (!selectedReport || !variableName || !autocompleteEnabled ||
        !focused || requiresController) {
      setLoading(false);
      return undefined;
    }

    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setLoading(true);
      try {
        const page = await requestOptionsPage(0, controller.signal);
        if (controller.signal.aborted || version !== pageVersionRef.current) return;
        setValues(page.values);
        setHasMore(page.hasMore);
        setNextOffset(page.nextOffset);
      } catch (requestError) {
        if (requestError.name !== "AbortError" &&
            version === pageVersionRef.current) {
          console.error("Report option autocomplete failed:", requestError);
          setError(requestError.message || "Could not load values.");
        }
      } finally {
        if (!controller.signal.aborted && version === pageVersionRef.current) {
          setLoading(false);
        }
      }
    }, String(search || "").trim() ? 220 : 0);

    return () => {
      window.clearTimeout(timer);
      controller.abort();
      moreAbortRef.current?.abort();
    };
  }, [
    selectedReport, variableName, autocompleteEnabled, focused,
    requiresController, search, isController, controllerVariableName,
    controllerValue, isPeriodTypeOption, isPeriodNameOption, periodTypeValue,
  ]);

  async function loadMoreOptions() {
    if (!hasMore || nextOffset == null || loading || loadingMore ||
        moreBusyRef.current || !focused || error || pageError) return;
    moreBusyRef.current = true;
    const version = pageVersionRef.current;
    const controller = new AbortController();
    moreAbortRef.current = controller;
    setLoadingMore(true);
    try {
      const page = await requestOptionsPage(nextOffset, controller.signal);
      if (controller.signal.aborted || version !== pageVersionRef.current) return;
      setValues((current) => [...new Set([...current, ...page.values])]);
      setHasMore(page.hasMore);
      setNextOffset(page.nextOffset);
    } catch (requestError) {
      if (requestError.name !== "AbortError" && version === pageVersionRef.current) {
        setPageError(requestError.message || "Could not load more values.");
      }
    } finally {
      if (moreAbortRef.current === controller) {
        moreAbortRef.current = null;
        moreBusyRef.current = false;
      }
      if (version === pageVersionRef.current) setLoadingMore(false);
    }
  }

  function chooseValue(nextValue) {
    const cleanValue =
      String(nextValue || "").trim();

    if (!cleanValue) {
      return;
    }

    onValueChange(
      variableName,
      cleanValue
    );

    setSearch(cleanValue);
    setFocused(false);
  }


  function clearValue() {
    onValueChange(
      variableName,
      ""
    );

    setSearch("");
    setFocused(false);
  }


  function chooseManualValue() {
    if (
      !autocompleteEnabled &&
      search.trim()
    ) {
      chooseValue(search);
    }
  }


  return (
    <section
      className="sidebar-option-value-section"
      ref={pickerRef}
      onBlur={(event) => {
        // Keep the popup open when focus moves to a suggestion inside it.
        if (
          event.relatedTarget &&
          !event.currentTarget.contains(event.relatedTarget)
        ) {
          setFocused(false);
        }
      }}
    >
      <div className="sidebar-option-value-header">
        <span className="sidebar-option-value-label">
          {option.label || variableName}
        </span>

        {value && (
          <button
            type="button"
            className="sidebar-option-clear"
            onClick={clearValue}
          >
            Clear
          </button>
        )}
      </div>


      <div className="sidebar-option-search-wrap">
        <input
          type="text"
          className="sidebar-option-search"
          value={search}
          autoComplete="off"
          disabled={requiresController}
          placeholder={
            requiresController
              ? (isPeriodNameOption && !periodTypeValue
                  ? "Select PERIOD TYPE first"
                  : `Select ${controllerVariableName} first`)
              : autocompleteEnabled
                ? `Search ${option.label || variableName}...`
                : `Enter ${option.label || variableName}...`
          }
          onFocus={() => {
            if (!requiresController) {
              setFocused(true);
            }
          }}
          onChange={(event) => {
            const nextSearch =
              event.target.value;

            setSearch(nextSearch);

            /*
             * Typing means the previously committed value is no longer
             * authoritative until a suggestion/manual value is selected.
             */
            if (value) {
              onValueChange(
                variableName,
                ""
              );
            }

            if (!requiresController) {
              setFocused(true);
            }
          }}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !autocompleteEnabled &&
              !requiresController
            ) {
              event.preventDefault();
              chooseManualValue();
            }
          }}
        />


        {value && (
          <span className="sidebar-option-selected-check">
            ✓
          </span>
        )}


        {focused &&
          autocompleteEnabled &&
          !requiresController && (
            <div className="sidebar-option-results"
              onScroll={(event) => {
                const element = event.currentTarget;
                if (hasMore && element.scrollTop + element.clientHeight >=
                    element.scrollHeight - 60) loadMoreOptions();
              }}>
              {loading && (
                <div className="sidebar-option-state">
                  Loading values...
                </div>
              )}

              {!loading &&
                error && (
                  <div className="sidebar-option-state error">
                    {error}
                  </div>
                )}

              {!loading &&
                !error &&
                values.length === 0 && (
                  <div className="sidebar-option-state">
                    No matching values
                  </div>
                )}

              {!loading &&
                !error &&
                values.map(
                  (item) => (
                    <button
                      key={item}
                      type="button"
                      className="sidebar-option-result"
                      onMouseDown={(event) => {
                        event.preventDefault();
                      }}
                      onClick={() => {
                        chooseValue(item);
                      }}
                    >
                      {item}
                    </button>
                  )
                )}
              {!loading && loadingMore && (
                <div className="sidebar-option-state">Loading more...</div>
              )}
              {!loading && pageError && (
                <button type="button" className="sidebar-option-load-more"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => setPageError(null)}>
                  {pageError} — Retry
                </button>
              )}
              {!loading && !loadingMore && !pageError && hasMore && (
                <button type="button" className="sidebar-option-load-more"
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={loadMoreOptions}>
                  Load more values
                </button>
              )}
            </div>
          )}
      </div>


      {requiresController && (
        <div className="sidebar-option-manual-hint">
          {isPeriodNameOption && !periodTypeValue
            ? "Select PERIOD TYPE first."
            : `Select ${controllerVariableName} first.`}
        </div>
      )}

      {!requiresController &&
        !autocompleteEnabled && (
          <div className="sidebar-option-manual-hint">
            Manual value — press Enter to select.
          </div>
        )}
    </section>
  );
}


export default function Sidebar() {
  const {
    sidebarCollapsed,
    sidebarWidth,
    setSidebarWidth,

    navigationLoading,
    navigationError,

    baseTabs,

    selectedReport,
    selectReport,
    conversationReportName,
    reportLocked,

    requestMode,
    reportOptions,
    reportVariables,
    reportAutocompleteController,
    selectedControllerValue,

    selectedOptionValues,
    setReportOptionValue,

    newChat,
  } = useChat();


  const resizingRef =
    useRef(false);


  const allReports =
    useMemo(
      () => buildReportChoices(
        baseTabs
      ),
      [baseTabs]
    );


  const selectedReportChoice =
    useMemo(() => {
      if (!selectedReport) {
        return null;
      }

      const selectedName =
        normalizeReportName(
          selectedReport.report_name ||
          selectedReport.label ||
          ""
        );

      return (
        allReports.find(
          (reportItem) =>
            normalizeReportName(
              reportItem.report_name ||
              reportItem.label ||
              ""
            ) === selectedName
        ) || null
      );
    }, [
      allReports,
      selectedReport,
    ]);


  const reportSelectorOptions =
    reportLocked &&
    selectedReportChoice
      ? [selectedReportChoice]
      : allReports;


  function handleResizeStart() {
    resizingRef.current = true;
    document.body.style.userSelect =
      "none";

    function handleMouseMove(event) {
      if (!resizingRef.current) {
        return;
      }

      const nextWidth =
        Math.min(
          Math.max(
            event.clientX,
            220
          ),
          450
        );

      setSidebarWidth(nextWidth);
    }

    function handleMouseUp() {
      resizingRef.current = false;
      document.body.style.userSelect =
        "";

      document.removeEventListener(
        "mousemove",
        handleMouseMove
      );

      document.removeEventListener(
        "mouseup",
        handleMouseUp
      );
    }

    document.addEventListener(
      "mousemove",
      handleMouseMove
    );

    document.addEventListener(
      "mouseup",
      handleMouseUp
    );
  }


  return (
    <div
      className={
        "sidebar" +
        (
          sidebarCollapsed
            ? " collapsed"
            : ""
        )
      }
      style={{
        width:
          sidebarCollapsed
            ? 0
            : sidebarWidth,
      }}
    >
      <div
        className="resize-handle"
        onMouseDown={handleResizeStart}
      />


      <button
        type="button"
        className="new-chat"
        onClick={newChat}
      >
        ＋ New chat
      </button>

      <div className="sidebar-content-scroll">
        {navigationLoading && (
        <div className="navigation-state">
          Loading reports...
        </div>
      )}


      {navigationError && (
        <div className="navigation-state navigation-error">
          {navigationError}
        </div>
      )}


      {!navigationLoading &&
        !navigationError && (
          <>
            {reportLocked && (
              <div className="sidebar-report-lock">
                Report locked for this chat: {conversationReportName}
              </div>
            )}


            <Dropdown
              label="Report"
              options={reportSelectorOptions}
              selectedId={
                selectedReportChoice?.id ||
                null
              }
              placeholder="Select a report"
              allowNone={false}
              onSelect={(reportId) => {
                if (!reportId) {
                  return;
                }

                selectReport(reportId);
              }}
            />


            {selectedReport && requestMode === "normal_message" &&
              reportOptions.length > 0 && (
                <>
                  <div className="sidebar-tree-title">
                    Filters
                  </div>

                  {reportOptions.map(
                    (option) => {
                      const variableId =
                        normalizeIdentifier(
                          option.variable_name
                        );

                      return (
                        <ReportOptionControl
                          key={
                            option.id ||
                            variableId
                          }
                          option={option}
                          selectedReport={
                            selectedReport
                          }
                          reportVariables={
                            reportVariables
                          }
                          controllerVariableName={
                            reportAutocompleteController
                              ?.variable_name ||
                            ""
                          }
                          controllerValue={
                            selectedControllerValue
                          }
                          periodTypeValue={
                            selectedOptionValues.period_type || ""
                          }
                          value={
                            selectedOptionValues[
                              variableId
                            ] || ""
                          }
                          onValueChange={
                            setReportOptionValue
                          }
                        />
                      );
                    }
                  )}
                </>
              )}


            {selectedReport && requestMode === "normal_message" &&
              reportOptions.length === 0 && (
                <div className="navigation-state">
                  This report has no sidebar filters.
                </div>
              )}


            {selectedReport && requestMode === "normal_message" && (
              <VariableChips />
            )}
          </>
        )}


        <HistoryList />
      </div>

      <UserProfile />
    </div>
  );
}
