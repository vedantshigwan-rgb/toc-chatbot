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


function getLabel(item) {
  return (
    item?.label ||
    item?.report_name ||
    item?.name ||
    item?.id ||
    ""
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

    selectedBaseTabId,
    selectBaseTab,

    selectedSubTabId,
    selectSubTab,

    selectedReport,
    selectedReportId,
    selectReport,
    conversationReportName,
    reportLocked,

    selectedOption,
    selectedOptionId,
    selectOption,

    reportVariables,

    selectedOptionValue,
    setSelectedOptionValue,

    newChat,
  } = useChat();


  // ==========================================================
  // Refs
  // ==========================================================

  const resizingRef =
    useRef(false);

  const treeZoneRef =
    useRef(null);

  const flyoutCloseTimerRef =
    useRef(null);


  // ==========================================================
  // Flyout navigation state
  // ==========================================================

  const [
    flyoutOpen,
    setFlyoutOpen,
  ] = useState(false);


  const [
    flyoutTop,
    setFlyoutTop,
  ] = useState(0);


  const [
    hoveredBaseId,
    setHoveredBaseId,
  ] = useState(null);


  const [
    hoveredSubId,
    setHoveredSubId,
  ] = useState(null);


  // ==========================================================
  // Flyout data
  // ==========================================================

  const previewBase =
    useMemo(() => {
      if (!hoveredBaseId) {
        return null;
      }

      return (
        baseTabs.find(
          (item) =>
            item.id === hoveredBaseId
        ) || null
      );
    }, [
      hoveredBaseId,
      baseTabs,
    ]);


  const previewSubTabs =
    previewBase?.sub_tabs || [];


  const previewSub =
    useMemo(() => {
      if (!hoveredSubId) {
        return null;
      }

      return (
        previewSubTabs.find(
          (item) =>
            item.id === hoveredSubId
        ) || null
      );
    }, [
      hoveredSubId,
      previewSubTabs,
    ]);


  const previewReports =
    previewSub?.reports || [];


  // ==========================================================
  // Option value selector
  // ==========================================================

  const [
    optionSearch,
    setOptionSearch,
  ] = useState("");


  const [
    optionValues,
    setOptionValues,
  ] = useState([]);


  const [
    optionValuesLoading,
    setOptionValuesLoading,
  ] = useState(false);


  const [
    optionValuesError,
    setOptionValuesError,
  ] = useState(null);


  const [
    optionValueFocused,
    setOptionValueFocused,
  ] = useState(false);


  /*
   * Match the option selected from Roles and options.xlsx
   * against the complete variable metadata returned for
   * the selected report.
   */
  const selectedOptionVariable =
    useMemo(() => {
      if (!selectedOption) {
        return null;
      }

      const selectedVariableId =
        normalizeIdentifier(
          selectedOption.variable_name
        );

      return (
        reportVariables.find(
          (variable) =>
            normalizeIdentifier(
              variable.variable_name
            ) === selectedVariableId
        ) || null
      );
    }, [
      selectedOption,
      reportVariables,
    ]);


  const optionAutocompleteEnabled =
    selectedOptionVariable
      ?.autocomplete_enabled === true ||
    selectedOption
      ?.autocomplete_enabled === true;


  const autocompleteVariableName =
    selectedOptionVariable
      ?.variable_name ||
    selectedOption
      ?.variable_name ||
    "";


  /*
   * When a different option is selected:
   *
   * ORG NAME
   * PERIOD TYPE
   * PERIOD NAME
   *
   * clear the previous value and immediately allow
   * its values to be displayed.
   */
  useEffect(() => {
    setOptionSearch("");
    setOptionValues([]);
    setOptionValuesError(null);

    setOptionValueFocused(
      Boolean(selectedOption)
    );
  }, [
    selectedOption?.id,
    selectedReport?.id,
  ]);


  // ==========================================================
  // Load option values from Redis
  // ==========================================================

  useEffect(() => {
    if (
      !selectedReport ||
      !selectedOption ||
      !autocompleteVariableName ||
      !optionAutocompleteEnabled
    ) {
      setOptionValues([]);
      setOptionValuesLoading(false);
      setOptionValuesError(null);

      return undefined;
    }


    const controller =
      new AbortController();


    const query =
      optionSearch.trim();


    const delay =
      query ? 220 : 0;


    const timer =
      window.setTimeout(
        async () => {
          setOptionValuesLoading(true);
          setOptionValuesError(null);

          try {
            const values =
              await fetchAutocompleteValues({
                reportName:
                  selectedReport
                    .autocomplete_report_name ||
                  selectedReport
                    .report_name,

                variableName:
                  autocompleteVariableName,

                query,

                limit: 40,

                signal:
                  controller.signal,
              });


            setOptionValues(
              Array.isArray(values)
                ? values
                : []
            );
          } catch (error) {
            if (
              error.name === "AbortError"
            ) {
              return;
            }

            console.error(
              "Sidebar option value error:",
              error
            );

            setOptionValues([]);

            setOptionValuesError(
              error.message ||
              "Could not load values."
            );
          } finally {
            if (
              !controller.signal.aborted
            ) {
              setOptionValuesLoading(
                false
              );
            }
          }
        },
        delay
      );


    return () => {
      window.clearTimeout(timer);

      controller.abort();
    };
  }, [
    selectedReport?.id,
    selectedReport?.report_name,
    selectedReport
      ?.autocomplete_report_name,

    selectedOption?.id,

    autocompleteVariableName,
    optionAutocompleteEnabled,

    optionSearch,
  ]);


  // ==========================================================
  // Flyout close timer cleanup
  // ==========================================================

  useEffect(() => {
    return () => {
      if (
        flyoutCloseTimerRef.current
      ) {
        window.clearTimeout(
          flyoutCloseTimerRef.current
        );
      }
    };
  }, []);


  // ==========================================================
  // Flyout helpers
  // ==========================================================

  function cancelFlyoutClose() {
    if (
      flyoutCloseTimerRef.current
    ) {
      window.clearTimeout(
        flyoutCloseTimerRef.current
      );

      flyoutCloseTimerRef.current =
        null;
    }
  }


  function closeFlyout() {
    cancelFlyoutClose();

    setFlyoutOpen(false);
    setHoveredBaseId(null);
    setHoveredSubId(null);
  }


  function scheduleFlyoutClose() {
    cancelFlyoutClose();

    flyoutCloseTimerRef.current =
      window.setTimeout(() => {
        setFlyoutOpen(false);

        setHoveredBaseId(null);
        setHoveredSubId(null);

        flyoutCloseTimerRef.current =
          null;
      }, 300);
  }


  /*
   * This fixes the issue where PRODUCTION was opening
   * its flyout beside PLANNING.
   *
   * We calculate the top position from the actual
   * Base-tab row currently being hovered.
   */
  function openBaseFlyout(
    baseTabId,
    element
  ) {
    if (
      !treeZoneRef.current ||
      !element
    ) {
      return;
    }


    const zoneRect =
      treeZoneRef.current
        .getBoundingClientRect();


    const itemRect =
      element
        .getBoundingClientRect();


    const top =
      itemRect.top -
      zoneRect.top;


    setFlyoutTop(
      Math.max(0, top)
    );


    setFlyoutOpen(true);

    setHoveredBaseId(
      baseTabId
    );

    /*
     * Do not show a report from the previously
     * hovered Base tab.
     */
    setHoveredSubId(null);
  }


  // ==========================================================
  // Sidebar resize
  // ==========================================================

  function handleResizeStart() {
    resizingRef.current =
      true;

    document.body.style.userSelect =
      "none";


    function handleMouseMove(event) {
      if (
        !resizingRef.current
      ) {
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

      setSidebarWidth(
        nextWidth
      );
    }


    function handleMouseUp() {
      resizingRef.current =
        false;

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


  // ==========================================================
  // Option value helpers
  // ==========================================================

  function chooseOptionValue(value) {
    const cleanValue =
      String(value || "").trim();

    if (!cleanValue) {
      return;
    }

    setSelectedOptionValue(
      cleanValue
    );

    setOptionSearch(
      cleanValue
    );

    setOptionValueFocused(
      false
    );
  }


  function chooseManualValue() {
    const cleanValue =
      optionSearch.trim();

    if (!cleanValue) {
      return;
    }

    chooseOptionValue(
      cleanValue
    );
  }


  // ==========================================================
  // Render
  // ==========================================================

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

        onMouseDown={
          handleResizeStart
        }
      />


      <button
        type="button"

        className="new-chat"

        onClick={newChat}
      >
        ＋ New chat
      </button>


      {navigationLoading && (
        <div className="navigation-state">
          Loading navigation...
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
            {/* =================================================
                NAVIGATION

                Permanent:
                    Base tabs

                Flyout:
                    Sub tab → Report

                OPTION DOES NOT GO INTO THE FLYOUT.
            ================================================= */}

            <div
              ref={treeZoneRef}

              className="sidebar-tree-zone"

              onMouseEnter={() => {
                cancelFlyoutClose();
              }}

              onMouseLeave={() => {
                scheduleFlyoutClose();
              }}
            >
              <div className="sidebar-tree-title">
                Navigation
              </div>

              {reportLocked && (
                <div className="sidebar-report-lock">
                  Report locked for this chat: {conversationReportName}
                </div>
              )}


              {/* =============================================
                  BASE TABS
              ============================================== */}

              <div className="sidebar-base-menu">
                {baseTabs.map(
                  (baseTab) => {
                    const active =
                      baseTab.id ===
                      selectedBaseTabId;


                    return (
                      <button
                        key={
                          baseTab.id
                        }

                        type="button"

                        disabled={
                          reportLocked && !active
                        }

                        title={
                          reportLocked && !active
                            ? `This chat is locked to ${conversationReportName}. Start a new chat to change reports.`
                            : undefined
                        }

                        className={
                          "sidebar-base-item" +
                          (
                            active
                              ? " active"
                              : ""
                          )
                        }

                        onMouseEnter={(event) => {
                          cancelFlyoutClose();

                          openBaseFlyout(
                            baseTab.id,
                            event.currentTarget
                          );
                        }}

                        onFocus={(event) => {
                          cancelFlyoutClose();

                          openBaseFlyout(
                            baseTab.id,
                            event.currentTarget
                          );
                        }}

                        onClick={(event) => {
                          selectBaseTab(
                            baseTab.id
                          );

                          cancelFlyoutClose();

                          openBaseFlyout(
                            baseTab.id,
                            event.currentTarget
                          );
                        }}
                      >
                        <span className="sidebar-base-name">
                          {getLabel(
                            baseTab
                          )}
                        </span>

                        <span className="sidebar-base-arrow">
                          ›
                        </span>
                      </button>
                    );
                  }
                )}
              </div>


              {/* =============================================
                  FLYOUT

                  ONLY:
                      SUB TAB
                      REPORT
              ============================================== */}

              {flyoutOpen &&
                previewBase && (
                  <div
                    className="sidebar-nav-flyout"

                    style={{
                      top: `${flyoutTop}px`,
                    }}

                    onMouseEnter={() => {
                      cancelFlyoutClose();
                    }}

                    onMouseLeave={() => {
                      scheduleFlyoutClose();
                    }}
                  >
                    {/* =======================================
                        SUB TAB COLUMN
                    ======================================== */}

                    <div className="sidebar-nav-column">
                      <div className="sidebar-nav-column-title">
                        Sub tab
                      </div>


                      {previewSubTabs.map(
                        (subTab) => {
                          const active =
                            subTab.id ===
                            selectedSubTabId;


                          return (
                            <button
                              key={
                                subTab.id
                              }

                              type="button"

                              disabled={
                                reportLocked && !active
                              }

                              title={
                                reportLocked && !active
                                  ? `This chat is locked to ${conversationReportName}. Start a new chat to change reports.`
                                  : undefined
                              }

                              className={
                                "sidebar-nav-flyout-item" +
                                (
                                  active
                                    ? " active"
                                    : ""
                                )
                              }

                              onMouseEnter={() => {
                                cancelFlyoutClose();

                                setHoveredSubId(
                                  subTab.id
                                );
                              }}

                              onFocus={() => {
                                cancelFlyoutClose();

                                setHoveredSubId(
                                  subTab.id
                                );
                              }}

                              onClick={() => {
                                selectSubTab(
                                  subTab.id
                                );

                                setHoveredSubId(
                                  subTab.id
                                );
                              }}
                            >
                              <span>
                                {getLabel(
                                  subTab
                                )}
                              </span>

                              <span>
                                ›
                              </span>
                            </button>
                          );
                        }
                      )}
                    </div>


                    {/* =======================================
                        REPORT COLUMN

                        THIS IS THE LAST FLYOUT COLUMN.
                    ======================================== */}

                    {previewSub && (
                      <div className="sidebar-nav-column">
                        <div className="sidebar-nav-column-title">
                          Report
                        </div>


                        {previewReports.length ===
                        0 ? (
                          <div className="sidebar-nav-empty">
                            No reports
                          </div>
                        ) : (
                          previewReports.map(
                            (reportItem) => {
                              const active =
                                reportItem.id ===
                                selectedReportId;


                              return (
                                <button
                                  key={
                                    reportItem.id
                                  }

                                  type="button"

                                  disabled={
                                    reportLocked && !active
                                  }

                                  title={
                                    reportLocked && !active
                                      ? `This chat is locked to ${conversationReportName}. Start a new chat to change reports.`
                                      : undefined
                                  }

                                  className={
                                    "sidebar-nav-flyout-item" +
                                    (
                                      active
                                        ? " active"
                                        : ""
                                    )
                                  }

                                  onClick={() => {
                                    /*
                                     * Clicking the report commits the
                                     * currently hovered navigation path.
                                     */
                                    if (
                                      previewBase.id !==
                                      selectedBaseTabId
                                    ) {
                                      selectBaseTab(
                                        previewBase.id
                                      );
                                    }


                                    if (
                                      previewSub.id !==
                                      selectedSubTabId
                                    ) {
                                      selectSubTab(
                                        previewSub.id
                                      );
                                    }


                                    selectReport(
                                      reportItem.id
                                    );


                                    /*
                                     * OPTION is not shown here.
                                     * It will appear as the permanent
                                     * dropdown below Navigation.
                                     */
                                    closeFlyout();
                                  }}
                                >
                                  <span>
                                    {getLabel(
                                      reportItem
                                    )}
                                  </span>
                                </button>
                              );
                            }
                          )
                        )}
                      </div>
                    )}
                  </div>
                )}
            </div>


            {/* =================================================
                STATIC OPTION DROPDOWN

                Comes from:
                    Roles and options.xlsx

                Example:
                    Docket Report
                        ORG NAME
                        PERIOD TYPE
                        PERIOD NAME
            ================================================= */}

            {selectedReport && (
              <Dropdown
                label="Option"

                options={
                  selectedReport.options ||
                  []
                }

                selectedId={
                  selectedOptionId
                }

                placeholder="Select an option"

                allowNone

                noneLabel="None / No filter"

                onSelect={(optionId) => {
                  selectOption(
                    optionId
                  );

                  setOptionValueFocused(
                    Boolean(optionId)
                  );
                }}
              />
            )}


            {/* =================================================
                SELECTED OPTION VALUE

                Redis variable:
                    show Redis values

                Non-Redis variable:
                    manual input
            ================================================= */}

            {selectedOption && (
              <section className="sidebar-option-value-section">
                <div className="sidebar-option-value-header">
                  <span className="sidebar-option-value-label">
                    {selectedOption.label}
                  </span>


                  {selectedOptionValue && (
                    <button
                      type="button"

                      className="sidebar-option-clear"

                      onClick={() => {
                        setSelectedOptionValue(
                          ""
                        );

                        setOptionSearch(
                          ""
                        );

                        setOptionValueFocused(
                          true
                        );
                      }}
                    >
                      Clear
                    </button>
                  )}
                </div>


                <div className="sidebar-option-search-wrap">
                  <input
                    type="text"

                    className="sidebar-option-search"

                    value={
                      optionSearch
                    }

                    autoComplete="off"

                    placeholder={
                      optionAutocompleteEnabled
                        ? `Search ${selectedOption.label}...`
                        : `Enter ${selectedOption.label}...`
                    }

                    onFocus={() => {
                      setOptionValueFocused(
                        true
                      );
                    }}

                    onChange={(event) => {
                      setOptionSearch(
                        event.target.value
                      );

                      setSelectedOptionValue(
                        ""
                      );

                      setOptionValueFocused(
                        true
                      );
                    }}

                    onKeyDown={(event) => {
                      if (
                        event.key ===
                          "Enter" &&
                        !optionAutocompleteEnabled
                      ) {
                        event.preventDefault();

                        chooseManualValue();
                      }
                    }}
                  />


                  {selectedOptionValue && (
                    <span className="sidebar-option-selected-check">
                      ✓
                    </span>
                  )}


                  {optionValueFocused &&
                    optionAutocompleteEnabled && (
                      <div className="sidebar-option-results">
                        {optionValuesLoading && (
                          <div className="sidebar-option-state">
                            Loading values...
                          </div>
                        )}


                        {!optionValuesLoading &&
                          optionValuesError && (
                            <div className="sidebar-option-state error">
                              {optionValuesError}
                            </div>
                          )}


                        {!optionValuesLoading &&
                          !optionValuesError &&
                          optionValues.length ===
                            0 && (
                            <div className="sidebar-option-state">
                              No matching values
                            </div>
                          )}


                        {!optionValuesLoading &&
                          !optionValuesError &&
                          optionValues.map(
                            (value) => (
                              <button
                                key={
                                  value
                                }

                                type="button"

                                className="sidebar-option-result"

                                onMouseDown={(event) => {
                                  /*
                                   * Prevent input blur before
                                   * the value is selected.
                                   */
                                  event.preventDefault();
                                }}

                                onClick={() => {
                                  chooseOptionValue(
                                    value
                                  );
                                }}
                              >
                                {value}
                              </button>
                            )
                          )}
                      </div>
                    )}
                </div>


                {!optionAutocompleteEnabled && (
                  <div className="sidebar-option-manual-hint">
                    Manual value — press Enter to select.
                  </div>
                )}
              </section>
            )}


            {/* =================================================
                COMPLETE VARIABLE LIST ICON / PLATE
            ================================================= */}

            {selectedReport && (
              <VariableChips />
            )}
          </>
        )}


      <HistoryList />

      <UserProfile />
    </div>
  );
}