import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  reportData,
} from "../data/mockData";

import {
  fetchNavigationHierarchy,
} from "../services/navigationApi";

import {
  fetchAutocompleteValues,
  fetchReportVariables,
} from "../services/autocompleteApi";
import { buildAutocompleteFilters } from "../utils/autocompleteContext";

import {
  deleteChatSession,
  editChatMessage,
  fetchChatMessages,
  fetchChats,
  sendAskRequest,
  submitMessageFeedback,
} from "../services/chatApi";

import {
  classifyResponseAction,
} from "../utils/responseActions";


const ChatContext = createContext(null);


function useIdCounter(prefix) {
  const counterRef = useRef(0);

  return () => {
    counterRef.current += 1;

    return `${prefix}-${counterRef.current}`;
  };
}


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


const DATE_VARIABLE_NAME = "date";


function isDateVariable(variableName) {
  return (
    normalizeIdentifier(variableName) ===
    normalizeIdentifier(DATE_VARIABLE_NAME)
  );
}


function formatDateForChat(isoDate) {
  const value = String(isoDate || "");

  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return "";
  }

  const [year, month, day] = value.split("-");

  return `${day}/${month}/${year}`;
}


export function ChatProvider({ children }) {
  // ==========================================================
  // Layout
  // ==========================================================

  const [
    sidebarCollapsed,
    setSidebarCollapsed,
  ] = useState(false);

  const [
    sidebarWidth,
    setSidebarWidth,
  ] = useState(280);



  // ==========================================================
  // Navigation
  // ==========================================================

  const [
    navigationData,
    setNavigationData,
  ] = useState({
    base_tabs: [],
    warnings: [],
  });

  const [
    navigationLoading,
    setNavigationLoading,
  ] = useState(true);

  const [
    navigationError,
    setNavigationError,
  ] = useState(null);

  const [
    selectedBaseTabId,
    setSelectedBaseTabId,
  ] = useState(null);

  const [
    selectedSubTabId,
    setSelectedSubTabId,
  ] = useState(null);

const [
  selectedReportId,
  setSelectedReportId,
] = useState(null);


const [
  selectedOptionId,
  setSelectedOptionId,
] = useState(null);


const [
  selectedOptionValue,
  setSelectedOptionValue,
] = useState("");


/*
 * Multi-option state for the selected report.
 *
 * Example for Docket Employee Report:
 * {
 *   employee_org: "ORG-A",
 *   period_type: "MONTH",
 *   period_name: "NOV-2026"
 * }
 *
 * Keys are normalized logical variable names from Roles and options.xlsx.
 */
const [
  selectedOptionValues,
  setSelectedOptionValues,
] = useState({});

  /*
   * Complete variable list for the selected report.
   * This comes from redis mapping.xlsx through GET /variables.
   * The endpoint returns both Redis and non-Redis variables.
   */
  const [
    reportVariableDetails,
    setReportVariableDetails,
  ] = useState([]);

  const [
    reportAutocompleteController,
    setReportAutocompleteController,
  ] = useState(null);

  const [
    reportVariablesLoading,
    setReportVariablesLoading,
  ] = useState(false);

  const [
    reportVariablesError,
    setReportVariablesError,
  ] = useState(null);

  /*
   * Protect report-variable loading from stale responses.
   * History restore and navigation can update report state very quickly;
   * only the newest request is allowed to update the variable list.
   */
  const reportVariablesRequestIdRef =
    useRef(0);


  useEffect(() => {
    const abortController =
      new AbortController();

    async function loadNavigation() {
      setNavigationLoading(true);
      setNavigationError(null);

      try {
        const result =
          await fetchNavigationHierarchy({
            signal:
              abortController.signal,
          });

        setNavigationData(result);

        setSelectedBaseTabId(null);
        setSelectedSubTabId(null);
        setSelectedReportId(null);
        setSelectedOptionId(null);
        setSelectedOptionValues({});
      } catch (error) {
        if (
          error.name === "AbortError"
        ) {
          return;
        }

        console.error(
          "Navigation loading failed:",
          error
        );

        setNavigationData({
          base_tabs: [],
          warnings: [],
        });

        setNavigationError(
          error.message ||
            "Could not load navigation."
        );
      } finally {
        if (
          !abortController.signal.aborted
        ) {
          setNavigationLoading(false);
        }
      }
    }

    loadNavigation();

    return () => {
      abortController.abort();
    };
  }, []);


  const baseTabs =
    navigationData.base_tabs || [];


  const selectedBaseTab =
    useMemo(() => {
      return (
        baseTabs.find(
          (baseTab) =>
            baseTab.id ===
            selectedBaseTabId
        ) || null
      );
    }, [
      baseTabs,
      selectedBaseTabId,
    ]);


  const subTabs =
    selectedBaseTab?.sub_tabs || [];


  const selectedSubTab =
    useMemo(() => {
      return (
        subTabs.find(
          (subTab) =>
            subTab.id ===
            selectedSubTabId
        ) || null
      );
    }, [
      subTabs,
      selectedSubTabId,
    ]);


  const reports =
    selectedSubTab?.reports || [];


  const selectedReport =
    useMemo(() => {
      return (
        reports.find(
          (reportItem) =>
            reportItem.id ===
            selectedReportId
        ) || null
      );
    }, [
      reports,
      selectedReportId,
    ]);


  /*
   * These are ONLY the report-specific options from
   * Roles and options.xlsx.
   *
   * Examples:
   * PO Board Report -> ORG NAME
   * Docket Report -> ORG NAME, PERIOD TYPE, PERIOD NAME
   */
  const reportOptions =
    useMemo(() => {
      if (!selectedReport) {
        return [];
      }

      const selectedName =
        normalizeReportName(
          selectedReport.report_name ||
          selectedReport.label ||
          ""
        );

      const optionsByVariable =
        new Map();

      /*
       * Collect options report-wise across the whole navigation tree.
       * This makes the UI independent of Base tab / Sub tab grouping and
       * also handles worksheets where the same report appears on multiple
       * rows while its options continue on following rows.
       */
      for (const baseTab of baseTabs) {
        for (const subTab of baseTab.sub_tabs || []) {
          for (const reportItem of subTab.reports || []) {
            const reportName =
              normalizeReportName(
                reportItem.report_name ||
                reportItem.label ||
                ""
              );

            if (reportName !== selectedName) {
              continue;
            }

            for (const option of reportItem.options || []) {
              const variableId =
                normalizeIdentifier(
                  option?.variable_name
                );

              if (!variableId) {
                continue;
              }

              if (!optionsByVariable.has(variableId)) {
                optionsByVariable.set(
                  variableId,
                  option
                );
              }
            }
          }
        }
      }

      return Array.from(
        optionsByVariable.values()
      );
    }, [
      baseTabs,
      selectedReport,
    ]);


  const selectedOption =
    useMemo(() => {
      return (
        reportOptions.find(
          (option) =>
            option.id ===
            selectedOptionId
        ) || null
      );
    }, [
      reportOptions,
      selectedOptionId,
    ]);


  const autocompleteControllerVariableName =
    String(
      reportAutocompleteController?.variable_name || ""
    ).trim();


  const autocompleteControllerOption =
    useMemo(() => {
      const controllerId = normalizeIdentifier(
        autocompleteControllerVariableName
      );

      if (!controllerId) {
        return null;
      }

      return (
        reportOptions.find(
          (option) =>
            normalizeIdentifier(
              option?.variable_name
            ) === controllerId
        ) || null
      );
    }, [
      reportOptions,
      autocompleteControllerVariableName,
    ]);


  const selectedControllerValue =
    useMemo(() => {
      const controllerId =
        normalizeIdentifier(
          autocompleteControllerVariableName
        );

      if (!controllerId) {
        return "";
      }

      return String(
        selectedOptionValues[controllerId] ||
        ""
      ).trim();
    }, [
      autocompleteControllerVariableName,
      selectedOptionValues,
    ]);


  /*
   * Load ALL variables for the selected report from
   * redis mapping.xlsx.
   *
   * This includes:
   * - Redis variables
   * - Non-Redis variables
   *
   * A request id is used in addition to AbortController so a stale
   * response can never overwrite the variables for the current report.
   * A single retry covers short Redis/API startup hiccups.
   */
  useEffect(() => {
    if (!selectedReport) {
      reportVariablesRequestIdRef.current += 1;
      setReportVariableDetails([]);
      setReportAutocompleteController(null);
      setReportVariablesLoading(false);
      setReportVariablesError(null);

      return undefined;
    }

    const reportName =
      String(
        selectedReport.autocomplete_report_name ||
        selectedReport.report_name ||
        ""
      ).trim();

    if (!reportName) {
      setReportVariableDetails([]);
      setReportAutocompleteController(null);
      setReportVariablesLoading(false);
      setReportVariablesError(
        "Could not determine the report name for variables."
      );
      return undefined;
    }

    const abortController =
      new AbortController();

    const requestId =
      reportVariablesRequestIdRef.current + 1;

    reportVariablesRequestIdRef.current =
      requestId;

    /*
     * Clear the previous report only when this effect genuinely runs for
     * a different selected report. Do not clear from history-sync helpers.
     */
    setReportVariableDetails([]);
    setReportAutocompleteController(null);
    setReportVariablesLoading(true);
    setReportVariablesError(null);

    async function waitBeforeRetry(milliseconds) {
      await new Promise((resolve, reject) => {
        const timer =
          window.setTimeout(resolve, milliseconds);

        abortController.signal.addEventListener(
          "abort",
          () => {
            window.clearTimeout(timer);

            const error =
              new Error("Request aborted");

            error.name = "AbortError";
            reject(error);
          },
          { once: true }
        );
      });
    }

    async function loadReportVariables() {
      let lastError = null;

      for (let attempt = 1; attempt <= 2; attempt += 1) {
        try {
          const result =
            await fetchReportVariables({
              reportName,
              signal:
                abortController.signal,
            });

          if (
            abortController.signal.aborted ||
            reportVariablesRequestIdRef.current !== requestId
          ) {
            return;
          }

          setReportVariableDetails(
            Array.isArray(result.variableDetails)
              ? result.variableDetails
              : []
          );

          setReportAutocompleteController(
            result.autocompleteController || null
          );

          setReportVariablesError(null);
          return;
        } catch (error) {
          if (error.name === "AbortError") {
            return;
          }

          lastError = error;

          if (attempt < 2) {
            try {
              await waitBeforeRetry(250);
            } catch (retryError) {
              if (retryError.name === "AbortError") {
                return;
              }
              throw retryError;
            }
          }
        }
      }

      if (
        abortController.signal.aborted ||
        reportVariablesRequestIdRef.current !== requestId
      ) {
        return;
      }

      console.error(
        "Report variable loading failed:",
        lastError
      );

      setReportVariableDetails([]);
      setReportAutocompleteController(null);

      setReportVariablesError(
        lastError?.message ||
          "Could not load report variables."
      );
    }

    loadReportVariables().finally(() => {
      if (
        !abortController.signal.aborted &&
        reportVariablesRequestIdRef.current === requestId
      ) {
        setReportVariablesLoading(false);
      }
    });

    return () => {
      abortController.abort();
    };
  }, [
    selectedReport?.id,
    selectedReport
      ?.autocomplete_report_name,
    selectedReport?.report_name,
  ]);


  /*
   * Variable plate list.
   *
   * Primary source:
   *   redis mapping.xlsx through /variables.
   *
   * Fallback:
   *   associated variables from Roles and options.xlsx are
   *   added only when the mapping endpoint does not contain them.
   */
  const reportVariables =
    useMemo(() => {
      if (!selectedReport) {
        return [];
      }

      const variablesById =
        new Map();

      for (
        const variable
        of reportVariableDetails
      ) {
        const variableName =
          String(
            variable?.variable_name ||
            ""
          ).trim();

        const variableId =
          normalizeIdentifier(
            variableName
          );

        if (!variableId) {
          continue;
        }

        variablesById.set(
          variableId,
          {
            id:
              `${selectedReport.id}__variable__${variableId}`,

            label:
              variableName,

            variable_name:
              variableName,

            column_name:
              String(
                variable?.column_name ||
                variableName
              ),

            storage_type:
              String(
                variable?.storage_type ||
                ""
              ),

            autocomplete_enabled:
              variable
                ?.autocomplete_enabled ===
              true,

            source:
              "redis_mapping",
          }
        );
      }

      /*
       * Keep a Roles-sheet variable available even when a
       * development report is missing from redis mapping.xlsx.
       */
      for (
        const option
        of reportOptions
      ) {
        const variableName =
          String(
            option?.variable_name ||
            ""
          ).trim();

        const variableId =
          normalizeIdentifier(
            variableName
          );

        if (
          !variableId ||
          variablesById.has(
            variableId
          )
        ) {
          continue;
        }

        variablesById.set(
          variableId,
          {
            id:
              `${selectedReport.id}__sheet_variable__${variableId}`,

            label:
              variableName,

            variable_name:
              variableName,

            column_name:
              String(
                option?.column_name ||
                variableName
              ),

            storage_type:
              String(
                option?.storage_type ||
                ""
              ),

            autocomplete_enabled:
              option
                ?.autocomplete_enabled ===
              true,

            source:
              "roles_sheet_fallback",
          }
        );
      }

      return Array.from(
        variablesById.values()
      ).sort(
        (first, second) =>
          first.variable_name.localeCompare(
            second.variable_name,
            undefined,
            {
              sensitivity: "base",
            }
          )
      );
    }, [
      selectedReport,
      reportVariableDetails,
      reportOptions,
    ]);


  const report =
    useMemo(() => {
      if (!selectedReport) {
        return {
          id: "",
          name: "",
          autocompleteReportName: "",
          label: "",
          options: [],
          variableDetails: [],
          variables: [],
        };
      }

      return {
        id:
          selectedReport.id,

        label:
          selectedReport.label,

        /* Sent to /ask. */
        name:
          selectedReport.report_name,

        /* Used by /variables and /autocomplete. */
        autocompleteReportName:
          selectedReport
            .autocomplete_report_name ||
          selectedReport.report_name,

        /* Roles and options.xlsx entries only. */
        options:
          reportOptions,

        /* Complete redis mapping.xlsx variable metadata. */
        variableDetails:
          reportVariables,

        variables:
          reportVariables.map(
            (variable) =>
              variable.variable_name
          ),
      };
    }, [
      selectedReport,
      reportOptions,
      reportVariables,
    ]);


  const division = {
    id:
      selectedBaseTab?.id || "",

    name:
      selectedBaseTab?.label ||
      "No base tab selected",
  };



    function findSubTabPath(
    subTabId
  ) {
    for (
      const baseTab
      of baseTabs
    ) {
      const subTab =
        (
          baseTab.sub_tabs || []
        ).find(
          (item) =>
            item.id === subTabId
        );

      if (subTab) {
        return {
          baseTab,
          subTab,
        };
      }
    }

    return null;
  }


  function findReportPath(
    reportId
  ) {
    for (
      const baseTab
      of baseTabs
    ) {
      for (
        const subTab
        of baseTab.sub_tabs || []
      ) {
        const reportItem =
          (
            subTab.reports || []
          ).find(
            (item) =>
              item.id === reportId
          );

        if (reportItem) {
          return {
            baseTab,
            subTab,
            reportItem,
          };
        }
      }
    }

    return null;
  }


  function findReportPathByName(reportName) {
    const requestedName = String(reportName || "")
      .trim()
      .toLowerCase();

    if (!requestedName) {
      return null;
    }

    for (const baseTab of baseTabs) {
      for (const subTab of baseTab.sub_tabs || []) {
        for (const reportItem of subTab.reports || []) {
          const names = [
            reportItem.report_name,
            reportItem.autocomplete_report_name,
            reportItem.label,
          ]
            .filter(Boolean)
            .map((value) => String(value).trim().toLowerCase());

          if (names.includes(requestedName)) {
            return { baseTab, subTab, reportItem };
          }
        }
      }
    }

    return null;
  }


  function syncReportSelectionByName(reportName) {
    const cleanedName = String(reportName || "").trim();

    if (!cleanedName) {
      return false;
    }

    const path = findReportPathByName(cleanedName);

    if (!path) {
      setSelectedBaseTabId(null);
      setSelectedSubTabId(null);
      setSelectedReportId(null);
      setSelectedOptionId(null);
      setSelectedOptionValue("");
      setSelectedOptionValues({});
      setReportVariableDetails([]);
      setReportVariablesError(null);
      closeLookup();
      return false;
    }

    const reportChanged =
      path.reportItem.id !==
      selectedReportId;

    setSelectedBaseTabId(path.baseTab.id);
    setSelectedSubTabId(path.subTab.id);
    setSelectedReportId(path.reportItem.id);
    // Sending a message refreshes history, but does NOT change the report.
    // Keep the user's current sidebar selections in that case.
    if (reportChanged) {
      setSelectedOptionId(null);
      setSelectedOptionValue("");
      setSelectedOptionValues({});
    }

    /*
     * IMPORTANT: do not wipe variables when history synchronizes the
     * report that is already selected. If we clear them while the report
     * id stays the same, the variable-loading effect has no dependency
     * change to trigger another fetch. That was the intermittent
     * "1 variable available" bug.
     */
    if (reportChanged) {
      setReportVariableDetails([]);
      setReportAutocompleteController(null);
      setReportVariablesError(null);
    }

    closeLookup();
    return true;
  }


  function selectBaseTab(
    baseTabId
  ) {
    const baseTab =
      baseTabs.find(
        (item) =>
          item.id === baseTabId
      );

    if (!baseTab) {
      return false;
    }

    if (reportLocked) {
      return baseTab.id === selectedBaseTabId;
    }

    setSelectedBaseTabId(
      baseTab.id
    );

    setReplyingTo(null);
    setSelectedSubTabId(null);
    setSelectedReportId(null);
    setSelectedOptionId(null);
    setSelectedOptionValue("");
    setSelectedOptionValues({});

    setReportVariableDetails([]);
    setReportVariablesError(null);

    setChatInput("");

    closeLookup();
    return true;
  }


  function selectSubTab(
    subTabId
  ) {
    const path =
      findSubTabPath(
        subTabId
      );

    if (!path) {
      return false;
    }

    if (reportLocked) {
      return (
        path.baseTab.id === selectedBaseTabId &&
        path.subTab.id === selectedSubTabId
      );
    }

    setSelectedBaseTabId(
      path.baseTab.id
    );

    setReplyingTo(null);
    setSelectedSubTabId(
      path.subTab.id
    );

    setSelectedReportId(null);
    setSelectedOptionId(null);
    setSelectedOptionValue("");
    setSelectedOptionValues({});

    setReportVariableDetails([]);
    setReportVariablesError(null);

    setChatInput("");

    closeLookup();
    return true;
  }


  function selectReport(
    reportId
  ) {
    const path =
      findReportPath(
        reportId
      );

    if (!path) {
      return false;
    }

    const targetReportName =
      path.reportItem.report_name ||
      path.reportItem.label ||
      "";

    if (reportLocked) {
      return (
        normalizeReportName(targetReportName) ===
        normalizeReportName(conversationReportName)
      );
    }

    setSelectedBaseTabId(
      path.baseTab.id
    );

    setReplyingTo(null);
    setSelectedSubTabId(
      path.subTab.id
    );

    setSelectedReportId(
      path.reportItem.id
    );

    setSelectedOptionId(null);
    setSelectedOptionValue("");
    setSelectedOptionValues({});

    setReportVariableDetails([]);
    setReportVariablesError(null);

    setChatInput("");

    closeLookup();
    return true;
  }


  function selectOption(optionId) {
    /*
     * null means "None / No filter".
     * In that state no sidebar option variable is added
     * to the /ask payload.
     */
    if (optionId == null) {
      setSelectedOptionId(null);
      setSelectedOptionValue("");

      closeLookup();
      return;
    }

    const exists =
      reportOptions.some(
        (option) =>
          option.id === optionId
      );

    if (!exists) {
      return;
    }

    setSelectedOptionId(
      optionId
    );

    setSelectedOptionValue("");

    closeLookup();
  }


  function setReportOptionValue(
    variableName,
    value
  ) {
    const variableId =
      normalizeIdentifier(
        variableName
      );

    if (!variableId) {
      return;
    }

    const cleanValue =
      String(value || "").trim();

    setSelectedOptionValues(
      (currentValues) => {
        const controllerId =
          normalizeIdentifier(
            autocompleteControllerVariableName
          );

        /*
         * Changing the controlling option invalidates dependent option
         * selections because their available values are scoped by it.
         */
        if (
          controllerId &&
          variableId === controllerId
        ) {
          return cleanValue
            ? {
                [variableId]:
                  cleanValue,
              }
            : {};
        }

        const nextValues = {
          ...currentValues,
        };

        // Changing PERIOD TYPE invalidates PERIOD NAME for the Docket reports.
        const isDocketReport = [
          "Docket Report", "Docket Employee Report"
        ].includes(String(selectedReport?.report_name || "").trim());
        if (isDocketReport && variableId === "period_type" &&
            currentValues.period_type !== cleanValue) {
          delete nextValues.period_name;
        }

        if (cleanValue) {
          nextValues[variableId] =
            cleanValue;
        } else {
          delete nextValues[variableId];
        }

        return nextValues;
      }
    );

    closeLookup();
  }


  function getReportOptionValue(
    variableName
  ) {
    const variableId =
      normalizeIdentifier(
        variableName
      );

    if (!variableId) {
      return "";
    }

    return String(
      selectedOptionValues[variableId] ||
      ""
    );
  }


  function clearReportOptionValues() {
    setSelectedOptionValues({});
  }


  // ==========================================================
  // Persistent chat history
  // ==========================================================

  const [
    historyItems,
    setHistoryItems,
  ] = useState([]);

  const [
    activeHistoryId,
    setActiveHistoryId,
  ] = useState(null);


  /*
   * One conversation = one report.
   * A new chat is unlocked until its first report-bound request succeeds.
   * Opening an existing chat restores its persisted report and locks it.
   */
  const [
    conversationReportName,
    setConversationReportName,
  ] = useState(null);

  const reportLocked = Boolean(
    String(conversationReportName || "").trim()
  );

  const [
    historyLoading,
    setHistoryLoading,
  ] = useState(false);

  const [
    historyError,
    setHistoryError,
  ] = useState(null);

  const [
    deletingHistoryId,
    setDeletingHistoryId,
  ] = useState(null);


  async function loadChatHistory() {
    setHistoryLoading(true);
    setHistoryError(null);

    try {
      const chats = await fetchChats();

      setHistoryItems(
        chats.map((chat) => ({
          id: chat.chat_id,
          label:
            chat.title ||
            "New chat",
          time: chat.updated_at,
          reportName:
            chat.report_name || null,
          messageCount:
            chat.message_count || 0,
        }))
      );
    } catch (error) {
      console.error(
        "Chat history loading failed:",
        error
      );

      setHistoryError(
        error.message ||
          "Could not load chat history."
      );
    } finally {
      setHistoryLoading(false);
    }
  }


  useEffect(() => {
    loadChatHistory();
  }, []);


  useEffect(() => {
    if (navigationLoading || !activeHistoryId) {
      return;
    }

    const historyReportName =
      historyItems.find((item) => item.id === activeHistoryId)?.reportName ||
      null;

    if (historyReportName) {
      setConversationReportName(historyReportName);
      syncReportSelectionByName(historyReportName);
    }
  }, [
    navigationLoading,
    activeHistoryId,
    historyItems,
    baseTabs.length,
  ]);


  async function deleteHistoryItem(id) {
    if (!id || sending || deletingHistoryId) {
      return false;
    }

    setHistoryError(null);
    setDeletingHistoryId(id);

    try {
      await deleteChatSession({
        chatId: id,
      });

      setHistoryItems((currentItems) =>
        currentItems.filter((item) => item.id !== id)
      );

      if (activeHistoryId === id) {
        setActiveHistoryId(null);
        setConversationReportName(null);
        setMessages([]);
        setChatInput("");
        setReplyingTo(null);
        setRequestModeState("normal_message");
        setPendingSuggestId(null);
        closeLookup();
      }

      return true;
    } catch (error) {
      console.error(
        "Chat deletion failed:",
        error
      );

      setHistoryError(
        error.message ||
          "Could not delete this chat."
      );

      return false;
    } finally {
      setDeletingHistoryId(null);
    }
  }


  async function selectHistoryItem(id) {
    if (!id || sending) {
      return;
    }

    setHistoryError(null);

    try {
      const storedMessages =
        await fetchChatMessages({
          chatId: id,
        });

      const messagesById = new Map(
        storedMessages.map((message) => [
          message.message_id,
          message,
        ])
      );

      const restoredMessages =
        storedMessages.map((message) => {
          const repliedMessage =
            message.reply_to_message_id
              ? messagesById.get(
                  message.reply_to_message_id
                )
              : null;

          if (message.role === "user") {
            return {
              id: message.message_id,
              type: "user",
              parts: toParts(
                message.content || ""
              ),
              replyingToText:
                repliedMessage?.content ||
                null,
              persisted: true,
              isEdited: message.is_edited === true,
              payload: {
                input: message.content,
                report_name:
                  message.report_name,
                variables:
                  message.variables || [],
                message_type: 
                  message.request_mode || "normal_message",
              },
            };
          }

          const tableRows =
            Array.isArray(
              message.table_rows
            )
              ? message.table_rows
              : [];

          return {
            id: message.message_id,
            type: "bot",
            text: message.content || "",
            tableRows,
            hasTable:
              tableRows.length > 0,
            tableTitle:
              `${
                message.report_name ||
                "Report"
              } - Query Results`,
            feedback:
              message.feedback || null,
            persisted: true,
            backendResponse: {
              workflow_used:
                message.workflow_used,
              workflow_id:
                message.workflow_id,
              status: message.status,
              stage: message.stage,
              result_count:
                message.result_count,
              sql: message.sql,
              report_name:
                message.report_name || null,
              variables:
                message.variables || [],
              resolved_user_input:
                message.resolved_user_input || null,
            },
          };
        });

      const historyReportName =
        historyItems.find((item) => item.id === id)?.reportName ||
        storedMessages.find((message) => message.report_name)?.report_name ||
        null;

      if (historyReportName) {
        setConversationReportName(historyReportName);
        syncReportSelectionByName(historyReportName);
      } else {
        setConversationReportName(null);
      }

      setActiveHistoryId(id);
      setMessages(restoredMessages);
      setReplyingTo(null);
      setRequestModeState("normal_message");
      setChatInput("");
      closeLookup();
    } catch (error) {
      console.error(
        "Chat loading failed:",
        error
      );

      setHistoryError(
        error.message ||
          "Could not load this chat."
      );
    }
  }


  // ==========================================================
  // Messages
  // ==========================================================

  const nextMessageId =
    useIdCounter("msg");

  const [
    messages,
    setMessages,
  ] = useState([]);

  const [
    replyingTo,
    setReplyingTo,
  ] = useState(null);

  const [
    chatInput,
    setChatInput,
  ] = useState("");

  const [editingMessageId, setEditingMessageId] = useState(null);

  const [
    requestMode,
    setRequestModeState,
  ] = useState("normal_message");

  function setRequestMode(nextMode) {
    if (!["normal_message", "help", "metadata"].includes(nextMode)) {
      return;
    }

    setRequestModeState(nextMode);
    setReplyingTo(null);
    closeLookup();
  }

  const [
    inputDisabled,
  ] = useState(false);

  const [
    sending,
    setSending,
  ] = useState(false);

  /*
   * Retained for compatibility with existing suggestion
   * components. The live /ask flow does not currently use it.
   */
  const [
    pendingSuggestId,
    setPendingSuggestId,
  ] = useState(null);


  function addMessage(message) {
    const id =
      message.id || nextMessageId();

    setMessages((currentMessages) => [
      ...currentMessages,
      {
        ...message,
        id,
      },
    ]);

    return id;
  }


  function removeMessage(messageId) {
    setMessages(
      (currentMessages) =>
        currentMessages.filter(
          (message) =>
            message.id !== messageId
        )
    );
  }


  /*
   * Convert variable expressions into pills in user messages.
   */
  function toParts(text) {
    const parts = [];

    const variablePattern =
      /\/([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)\}/g;

    let lastIndex = 0;
    let match;

    while (
      (match =
        variablePattern.exec(text)) !== null
    ) {
      if (match.index > lastIndex) {
        parts.push({
          text: text.slice(
            lastIndex,
            match.index
          ),
        });
      }

      parts.push({
        pill: match[0],
        variableName: match[1],
        value: match[2],
      });

      lastIndex =
        match.index +
        match[0].length;
    }

    if (lastIndex < text.length) {
      parts.push({
        text: text.slice(lastIndex),
      });
    }

    return parts.length > 0
      ? parts
      : [{ text }];
  }


  function findReportVariable(
    variableName
  ) {
    const requestedVariableId =
      normalizeIdentifier(
        variableName
      );

    if (!requestedVariableId) {
      return null;
    }

    return (
      reportVariables.find(
        (variable) =>
          normalizeIdentifier(
            variable.variable_name
          ) ===
          requestedVariableId
      ) || null
    );
  }


  /*
   * Extract variables in the exact order they appear.
   * Repeated variables remain repeated.
   */
  function extractVariablesFromInput(
    input
  ) {
    const variables = [];
    const invalidVariables = [];
    const emptyVariables = [];

    const variablePattern =
      /\/([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)\}/g;

    let match;

    while (
      (match =
        variablePattern.exec(input)) !== null
    ) {
      const typedVariableName =
        match[1];

      const option =
        findReportVariable(
          typedVariableName
        );

      if (!option) {
        invalidVariables.push(
          typedVariableName
        );

        continue;
      }

      /*
       * Trim only outside whitespace.
       * Internal double spaces remain unchanged.
       */
      const value =
        match[2].trim();

      if (!value) {
        emptyVariables.push(
          option.variable_name
        );

        continue;
      }

      variables.push({
        name:
          option.variable_name,

        value,
      });
    }

    return {
      variables,
      invalidVariables:
        [...new Set(
          invalidVariables
        )],

      emptyVariables:
        [...new Set(
          emptyVariables
        )],
    };
  }


  /*
   * Convert the current backend response contract into one of two UI
   * shapes: a normal result bubble or a controlled action message.
   * The Primary-only backend no longer exposes resumable clarification
   * or Secondary workflow responses.
   */
  function messageFromResponse(responseData) {
    const classification =
      classifyResponseAction(responseData);

    const responseText =
      responseData.message ||
      responseData.output ||
      responseData.answer ||
      "The request completed successfully.";

    if (classification.kind !== "result") {
      return {
        id:
          responseData.assistant_message_id ||
          undefined,
        type: "action",
        kind: classification.kind,
        action: classification.action,
        text: responseText,
        backendResponse: responseData,
        persisted: Boolean(
          responseData.assistant_message_id
        ),
      };
    }

    const tableRows =
      Array.isArray(responseData.results)
        ? responseData.results
        : [];

    const responseReportName =
      responseData.report_name ||
      report.label ||
      report.name ||
      "Report";

    return {
      id:
        responseData.assistant_message_id ||
        undefined,
      type: "bot",
      text: responseText,
      tableRows,
      hasTable: tableRows.length > 0,
      tableTitle:
        `${responseReportName} - Query Results`,
      backendResponse: responseData,
      feedback: null,
      persisted: Boolean(
        responseData.assistant_message_id
      ),
    };
  }

  
  async function sendMessage() {
    const text = chatInput.trim();

    if (!text || sending || inputDisabled) {
      return;
    }

    // Handle simple greetings locally without calling the backend.
    const normalizedGreeting = text
      .toLowerCase()
      .replace(/[!.?,]+$/g, "")
      .trim();

    const greetingReplies = {
      hi: "Hello! I’m Lighthouse Assistant, here to help you !",
      hello: "Hello! I’m Lighthouse Assistant, here to help you !",
      hey: "Hi! I’m Lighthouse Assistant, here to help you !",
      "good morning": "Good morning! I’m Lighthouse Assistant, here to help you !",
      "good afternoon": "Good afternoon! I’m Lighthouse Assistant, here to help you !",
      "good evening": "Good evening! I’m Lighthouse Assistant, here to help you !",
      "how are you": "I’m here and ready to help! What would you like to know?",
      "who are you": "I’m Lighthouse Assistant, designed to help you !",
      "thank you": "You’re welcome! I’m happy to help.",
      thanks: "You’re welcome! Let me know if you need anything else.",
      "good bye": "Goodbye! Feel free to return whenever you need assistance.",
      bye: "Goodbye! Have a great day."
    };

    // Only intercept standalone greetings in normal chat mode.
    if (
      !replyingTo &&
      requestMode === "normal_message" &&
      Object.prototype.hasOwnProperty.call(
        greetingReplies,
        normalizedGreeting
      )
    ) {
      addMessage({
        type: "user",
        parts: toParts(text),
        persisted: false,
      });

      addMessage({
        type: "bot",
        text: greetingReplies[normalizedGreeting],
        persisted: false,
      });

      setChatInput("");
      closeLookup();
      return;
    }

    const currentReply = replyingTo;

    const messageType = currentReply ? "feedback" : requestMode;
    const reportName =
      currentReply?.reportName ||
      conversationReportName ||
      report.name ||
      null;

    const lockCreatedForRequest = Boolean(
      !reportLocked && reportName
    );

    if (lockCreatedForRequest) {
      // Lock immediately when the first report-bound request is sent so
      // the user cannot switch reports while that request is in flight.
      setConversationReportName(reportName);
    }

    if (
      (messageType === "help" || messageType === "metadata") &&
      !reportName
    ) {
      addMessage({
        type: "action",
        kind: "report_name_required",
        action: "report_name_required",
        text: "Select a report before using this mode.",
        persisted: false,
      });
      return;
    }

    if (messageType === "feedback") {
      if (
        !currentReply?.persisted ||
        currentReply.workflowUsed !== "primary" ||
        !activeHistoryId
      ) {
        addMessage({
          type: "action",
          kind: "feedback_error",
          action: "invalid_reply_target",
          text: "Feedback can only modify a persisted Primary result from the current chat.",
          persisted: false,
        });
        setReplyingTo(null);
        return;
      }
    }

    let inlineVariables = [];
    let invalidVariables = [];
    let emptyVariables = [];

    if ((messageType === "normal_message" || messageType === "feedback") && selectedReport) {
      const extracted = extractVariablesFromInput(text);
      inlineVariables = extracted.variables;
      invalidVariables = extracted.invalidVariables;
      emptyVariables = extracted.emptyVariables;
    }

    if (invalidVariables.length > 0) {
      addMessage({
        type: "action",
        kind: "variable_error",
        action: "fix_variable_syntax",
        text:
          "These variables are not available for " +
          `${report.label}: ` +
          invalidVariables.map((name) => `/${name}`).join(", "),
        persisted: false,
      });
      return;
    }

    if (emptyVariables.length > 0) {
      addMessage({
        type: "action",
        kind: "variable_error",
        action: "fix_variable_syntax",
        text:
          "Enter a value inside the curly braces for: " +
          emptyVariables.map((name) => `/${name}`).join(", "),
        persisted: false,
      });
      return;
    }

    const variables = inlineVariables.map((variable) => ({
      ...variable,
      source: "input",
    }));

    const selectedReportName = report.name || null;
    const optionMatchesRequestReport =
      !reportName ||
      !selectedReportName ||
      reportName === selectedReportName;

    if ((messageType === "normal_message" || messageType === "feedback") && optionMatchesRequestReport) {
      /*
       * Add every populated report option to the request.
       * Inline /variable {value} input always wins over the sidebar value
       * for the same logical variable.
       */
      for (const option of reportOptions) {
        const variableName =
          String(
            option?.variable_name ||
            ""
          ).trim();

        const variableId =
          normalizeIdentifier(
            variableName
          );

        if (!variableId) {
          continue;
        }

        const optionValue =
          String(
            selectedOptionValues[variableId] ||
            ""
          ).trim();

        if (!optionValue) {
          continue;
        }

        const alreadyProvidedInline =
          variables.some(
            (variable) =>
              normalizeIdentifier(
                variable.name
              ) === variableId
          );

        if (alreadyProvidedInline) {
          continue;
        }

        variables.push({
          name: variableName,
          value: optionValue,
          source: "option",
        });
      }
    }

    const payload = {
      message_type: messageType,
      input: text,
      report_name: reportName,
      variables,
      chat_id: activeHistoryId,
      reply_to_message_id:
        messageType === "feedback"
          ? currentReply?.messageId || null
          : null,
    };

    const temporaryUserMessageId = addMessage({
      type: "user",
      parts: toParts(text),
      replyingToText: currentReply?.text ?? null,
      payload,
      persisted: false,
    });

    setChatInput("");
    setReplyingTo(null);
    closeLookup();
    setSending(true);

    try {
      const result = await sendAskRequest({
        input: text,
        messageType,
        reportName,
        variables,
        chatId: activeHistoryId,
        replyToMessageId:
          messageType === "feedback"
            ? currentReply.messageId
            : null,
      });

      const responseData = result.response || {};

      if (responseData.chat_id) {
        setActiveHistoryId(responseData.chat_id);

        const responseReportName = String(
          responseData.report_name || reportName || ""
        ).trim();

        if (responseReportName) {
          setConversationReportName(
            (currentReportName) =>
              currentReportName || responseReportName
          );
        }
      }

      if (responseData.user_message_id) {
        setMessages((currentMessages) =>
          currentMessages.map((message) =>
            message.id === temporaryUserMessageId
              ? {
                  ...message,
                  id: responseData.user_message_id,
                  persisted: true,
                }
              : message
          )
        );
      }

      addMessage(messageFromResponse(responseData));
      await loadChatHistory();
    } catch (error) {
      console.error("The /ask request failed:", error);

      if (
        lockCreatedForRequest &&
        !activeHistoryId &&
        !error.responseData?.chat_id
      ) {
        // Nothing was persisted, so this did not become a real
        // conversation yet. Let the user choose a report again.
        setConversationReportName(null);
      }

      if (error.responseData?.action) {
        addMessage(
          messageFromResponse({
            ...error.responseData,
            message: error.message,
          })
        );
      } else {
        addMessage({
          type: "bot",
          text:
            error.message ||
            "Could not connect to the backend.",
          persisted: false,
        });
      }
    } finally {
      setSending(false);
    }
  }

  async function feedback(
    messageId,
    feedbackType
  ) {
    const targetMessage =
      messages.find(
        (message) =>
          message.id === messageId
      );

    if (!targetMessage) {
      return;
    }

    const previousFeedback =
      targetMessage.feedback || null;

    setMessages(
      (currentMessages) =>
        currentMessages.map(
          (message) =>
            message.id === messageId
              ? {
                  ...message,
                  feedback:
                    feedbackType,
                }
              : message
        )
    );

    // Local validation/error messages are not stored in Mongo.
    if (!targetMessage.persisted) {
      return;
    }

    try {
      await submitMessageFeedback({
        messageId,
        rating: feedbackType,
      });
    } catch (error) {
      console.error(
        "Feedback storage failed:",
        error
      );

      setMessages(
        (currentMessages) =>
          currentMessages.map(
            (message) =>
              message.id === messageId
                ? {
                    ...message,
                    feedback:
                      previousFeedback,
                  }
                : message
          )
      );
    }
  }
  
  function startEditingMessage(message) {
    if (
      !message ||
      message.type !== "user" ||
      message.persisted !== true ||
      message.payload?.message_type === "feedback" ||
      message.payload?.reply_to_message_id ||
      sending
    ) {
      return;
    }

    const originalText =
      message.payload?.input ??
      (message.parts || [])
        .map((part) => part.text ?? part.pill ?? "")
        .join("");

    setEditingMessageId(message.id);
    setChatInput(originalText);
    setReplyingTo(null);
    setRequestModeState("normal_message");
    closeLookup();
  }

  function cancelEditingMessage() {
    setEditingMessageId(null);
    setChatInput("");
    closeLookup();
  }

  async function submitChatInput() {
    if (editingMessageId) {
      const saved = await editMessage(editingMessageId, chatInput);

      if (saved) {
        setEditingMessageId(null);
        setChatInput("");
        closeLookup();
      }

      return;
    }

    await sendMessage();
  }

  
  async function editMessage(messageId, editedText) {
    const cleanedText = String(editedText || "").trim();

    if (!messageId || !cleanedText || sending) {
      return false;
    }

    const targetMessage = messages.find(
      (message) => message.id === messageId
    );

    if (
      !targetMessage ||
      targetMessage.type !== "user" ||
      targetMessage.persisted !== true ||
      targetMessage.payload?.message_type === "feedback" ||
      targetMessage.replyingToText
    ) {
      return false;
    }

    if (!activeHistoryId) {
      return false;
    }

    // Extract variables from the edited question.
    const extracted = extractVariablesFromInput(cleanedText);

    if (
      extracted.invalidVariables.length > 0 ||
      extracted.emptyVariables.length > 0
    ) {
      addMessage({
        type: "action",
        kind: "variable_error",
        action: "fix_variable_syntax",
        text:
          extracted.invalidVariables.length > 0
            ? "These variables are not available: " +
              extracted.invalidVariables
                .map((name) => `/${name}`)
                .join(", ")
            : "Enter a value inside the curly braces for: " +
              extracted.emptyVariables
                .map((name) => `/${name}`)
                .join(", "),
        persisted: false,
      });

      return false;
    }

    const editVariables = extracted.variables.map((variable) => ({
      ...variable,
      source: "input",
    }));

    setSending(true);

    try {
      const responseData = await editChatMessage({
        messageId,
        input: cleanedText,
        variables: editVariables,
        reportName:
          targetMessage.payload?.report_name ||
          conversationReportName ||
          report.name ||
          null,
      });

      const newAssistantMessage = messageFromResponse(responseData);

      setMessages((currentMessages) => {
        const editedUserIndex = currentMessages.findIndex(
          (message) => message.id === messageId
        );

        if (editedUserIndex < 0) {
          return currentMessages;
        }

        const updatedMessages = [...currentMessages];

        // Update the existing question while preserving its ID.
        updatedMessages[editedUserIndex] = {
          ...updatedMessages[editedUserIndex],
          parts: toParts(cleanedText),
          isEdited: true,
          payload: {
            ...updatedMessages[editedUserIndex].payload,
            input: cleanedText,
            variables: editVariables,
          },
        };

        // Find the next user question.
        let nextUserIndex = updatedMessages.findIndex(
          (message, index) =>
            index > editedUserIndex &&
            message.type === "user"
        );

        if (nextUserIndex < 0) {
          nextUserIndex = updatedMessages.length;
        }

        // Remove the old response(s) associated with this question.
        const removedIds = new Set();

        for (
          let index = editedUserIndex + 1;
          index < nextUserIndex;
          index += 1
        ) {
          const message = updatedMessages[index];

          if (
            message.type === "bot" ||
            message.type === "action"
          ) {
            removedIds.add(message.id);
          }
        }

        const resultMessages = updatedMessages.filter(
          (message) => !removedIds.has(message.id)
        );

        // Insert the regenerated response after the edited question.
        const currentEditedIndex = resultMessages.findIndex(
          (message) => message.id === messageId
        );

        if (currentEditedIndex >= 0) {
          resultMessages.splice(
            currentEditedIndex + 1,
            0,
            newAssistantMessage
          );
        }

        return resultMessages;
      });

      await loadChatHistory();

      return true;
    } catch (error) {
      console.error("Editing the message failed:", error);

      addMessage({
        type: "bot",
        text: error.message || "Could not edit this message.",
        persisted: false,
      });

      return false;
    } finally {
      setSending(false);
    }
  }


  function replyTo(messageId) {
    const message = messages.find(
      (item) => item.id === messageId
    );

    if (
      !message ||
      message.persisted !== true ||
      message.backendResponse?.workflow_used !== "primary" ||
      message.backendResponse?.status === "failed"
    ) {
      return;
    }

    setReplyingTo({
      messageId: message.id,
      text: message.text || "",
      persisted: true,
      workflowUsed: message.backendResponse.workflow_used,
      reportName:
        message.backendResponse.report_name ||
        report.name ||
        null,
    });

    setRequestModeState("normal_message");
    closeLookup();
  }

  function cancelReply() {
    setReplyingTo(null);
  }


  function newChat() {
    setActiveHistoryId(null);
    setConversationReportName(null);
    setSelectedOptionValues({});
    setSelectedOptionId(null);
    setSelectedOptionValue("");
    setMessages([]);
    setChatInput("");
    setReplyingTo(null);
    setRequestModeState("normal_message");
    setPendingSuggestId(null);
    closeLookup();
  }


  function pickSuggestOption(
    suggestId,
    option
  ) {
    removeMessage(suggestId);
    setPendingSuggestId(null);
    setChatInput(option);
  }


  function pickOther(suggestId) {
    removeMessage(suggestId);
    setPendingSuggestId(null);
  }


  // ==========================================================
  // Variable autocomplete
  // ==========================================================

  const [
    lookup,
    setLookup,
  ] = useState({
    open: false,
    mode: null,
    activeVar: null,
    options: [],
    highlightIndex: -1,
    replaceStart: null,
    replaceEnd: null,
    loading: false,
    loadingMore: false,
    hasMore: false,
    nextOffset: null,
    pageError: null,
    error: null,
    message: null,
  });


  const [
    requestedCaretPosition,
    setRequestedCaretPosition,
  ] = useState(null);


  const autocompleteTimerRef =
    useRef(null);

  const autocompleteAbortRef =
    useRef(null);

  const autocompleteRequestIdRef =
    useRef(0);

  const lookupPageRef = useRef(null);
  const lookupMoreAbortRef = useRef(null);
  const lookupMoreBusyRef = useRef(false);


  function clearRequestedCaretPosition() {
    setRequestedCaretPosition(null);
  }


  function cancelAutocompleteRequest() {
    lookupPageRef.current = null;
    lookupMoreBusyRef.current = false;
    if (lookupMoreAbortRef.current) {
      lookupMoreAbortRef.current.abort();
      lookupMoreAbortRef.current = null;
    }
    if (
      autocompleteTimerRef.current
    ) {
      clearTimeout(
        autocompleteTimerRef.current
      );

      autocompleteTimerRef.current =
        null;
    }

    if (
      autocompleteAbortRef.current
    ) {
      autocompleteAbortRef.current.abort();

      autocompleteAbortRef.current =
        null;
    }
  }


  function closeLookup() {
    cancelAutocompleteRequest();

    autocompleteRequestIdRef.current += 1;

    setLookup({
      open: false,
      mode: null,
      activeVar: null,
      options: [],
      highlightIndex: -1,
      replaceStart: null,
      replaceEnd: null,
      loading: false,
      loadingMore: false,
      hasMore: false,
      nextOffset: null,
      pageError: null,
      error: null,
      message: null,
    });
  }


  function openDateLookup({
    variableName,
    replaceStart,
    replaceEnd,
  }) {
    cancelAutocompleteRequest();

    autocompleteRequestIdRef.current += 1;

    setLookup({
      open: true,
      mode: "date",
      activeVar: variableName,
      options: [],
      highlightIndex: -1,
      replaceStart,
      replaceEnd,
      loading: false,
      loadingMore: false,
      hasMore: false,
      nextOffset: null,
      pageError: null,
      error: null,
      message: null,
    });
  }


  function scheduleValueAutocomplete({
    variableName,
    query = "",
    replaceStart,
    replaceEnd,
  }) {
    const variableOption =
      findReportVariable(
        variableName
      );

    /*
     * Non-Redis variables permit manual values,
     * but never call /autocomplete.
     */
    if (
      !variableOption ||
      !variableOption
        .autocomplete_enabled
    ) {
      closeLookup();
      return;
    }

    const targetVariableId =
      normalizeIdentifier(
        variableOption.variable_name
      );

    const controllerVariableId =
      normalizeIdentifier(
        autocompleteControllerVariableName
      );

    const targetIsController =
      Boolean(
        controllerVariableId &&
        targetVariableId === controllerVariableId
      );

    /*
     * For a controller-scoped report, every OTHER Redis variable requires
     * the controlling option value first.
     *
     * Example:
     *   Inventory Report controller = org_name
     *   /buyer_name {} requires selected ORG NAME first.
     *   /org_name {} itself remains globally searchable.
     */
    if (
      controllerVariableId &&
      !targetIsController &&
      !selectedControllerValue
    ) {
      cancelAutocompleteRequest();
      autocompleteRequestIdRef.current += 1;

      setLookup({
        open: true,
        mode: "value",
        activeVar:
          variableOption.variable_name,
        options: [],
        highlightIndex: -1,
        replaceStart,
        replaceEnd,
        loading: false,
        error: null,
        message:
          `Select ${
            autocompleteControllerOption?.label ||
            autocompleteControllerVariableName
          } first`,
      });

      return;
    }

    cancelAutocompleteRequest();

    const cleanedQuery =
      String(query || "").trim();

    // Load enough values on first open to make the list scrollable.
    // Every subsequent page uses the same page size.
    const resultLimit = 20;

    const requestDelay =
      cleanedQuery
        ? 250
        : 0;

    const requestId =
      autocompleteRequestIdRef.current +
      1;

    autocompleteRequestIdRef.current =
      requestId;

    const pageArgs = {
      reportName: report.autocompleteReportName || report.name,
      variableName: variableOption.variable_name,
      query: cleanedQuery,
      limit: resultLimit,
      controllerVariableName: targetIsController
        ? "" : autocompleteControllerVariableName,
      controllerValue: targetIsController ? "" : selectedControllerValue,
      periodType: ["Docket Report", "Docket Employee Report"].includes(
        String(report?.name || "").trim()
      ) && normalizeIdentifier(variableOption.variable_name) === "period_name"
        ? String(selectedOptionValues.period_type || "") : "",
      contextFilters: buildAutocompleteFilters({
        text: chatInput,
        optionValues: selectedOptionValues,
        reportVariables,
        targetVariable: variableOption.variable_name,
        reportName: report.name,
      }),
    };

    lookupPageRef.current = { requestId, pageArgs };

    setLookup({
      open: true,
      mode: "value",
      activeVar: variableOption.variable_name,
      options: [],
      highlightIndex: -1,
      replaceStart,
      replaceEnd,
      loading: true,
      loadingMore: false,
      hasMore: false,
      nextOffset: null,
      pageError: null,
      error: null,
      message: null,
    });

    autocompleteTimerRef.current =
      setTimeout(async () => {
        const abortController =
          new AbortController();

        autocompleteAbortRef.current =
          abortController;

        try {
          const page = await fetchAutocompleteValues({
            ...pageArgs,
            offset: 0,
            signal: abortController.signal,
          });

          if (
            requestId !==
            autocompleteRequestIdRef
              .current
          ) {
            return;
          }

          setLookup({
            open: true,
            mode: "value",
            activeVar:
              variableOption
                .variable_name,
            options: page.values,
            highlightIndex: page.values.length > 0 ? 0 : -1,
            hasMore: page.hasMore,
            nextOffset: page.nextOffset,
            loadingMore: false,
            pageError: null,
            replaceStart,
            replaceEnd,
            loading: false,
            error: null,
            message:
              page.values.length === 0
                ? cleanedQuery
                  ? "No matching values"
                  : "No values are available for the selected filter"
                : null,
          });
        } catch (error) {
          if (
            error.name ===
            "AbortError"
          ) {
            return;
          }

          console.error(
            "Variable autocomplete failed:",
            error
          );

          if (
            requestId !==
            autocompleteRequestIdRef
              .current
          ) {
            return;
          }

          setLookup({
            open: true,
            mode: "value",
            activeVar:
              variableOption
                .variable_name,
            options: [],
            highlightIndex: -1,
            replaceStart,
            replaceEnd,
            loading: false,
            error:
              error.message ||
              "Could not load values.",
            message: null,
          });
        }
      }, requestDelay);
  }


  async function loadMoreLookup() {
    const paging = lookupPageRef.current;
    if (!paging || lookupMoreBusyRef.current || !lookup.open ||
        lookup.mode !== "value" || lookup.loading || lookup.loadingMore ||
        !lookup.hasMore || lookup.nextOffset == null || lookup.pageError) {
      return;
    }
    if (paging.requestId !== autocompleteRequestIdRef.current) return;

    lookupMoreBusyRef.current = true;
    const controller = new AbortController();
    lookupMoreAbortRef.current = controller;
    const requestedOffset = lookup.nextOffset;
    setLookup((current) => ({ ...current, loadingMore: true, pageError: null }));

    try {
      const page = await fetchAutocompleteValues({
        ...paging.pageArgs,
        offset: requestedOffset,
        signal: controller.signal,
      });
      if (controller.signal.aborted ||
          paging.requestId !== autocompleteRequestIdRef.current) return;
      setLookup((current) => {
        // Keep keyboard highlight and existing suggestions stable on append.
        const nextOptions = [...new Set([...current.options, ...page.values])];
        return {
          ...current,
          options: nextOptions,
          loadingMore: false,
          hasMore: page.hasMore,
          nextOffset: page.nextOffset,
          pageError: null,
        };
      });
    } catch (error) {
      if (error.name !== "AbortError" &&
          paging.requestId === autocompleteRequestIdRef.current) {
        setLookup((current) => ({
          ...current,
          loadingMore: false,
          pageError: error.message || "Could not load more values.",
        }));
      }
    } finally {
      if (lookupMoreAbortRef.current === controller) {
        lookupMoreAbortRef.current = null;
        lookupMoreBusyRef.current = false;
      }
    }
  }

  function retryMoreLookup() {
    setLookup((current) => ({ ...current, pageError: null }));
  }

  function refreshLookup(
    value,
    cursorPosition
  ) {
    if (!selectedReport || requestMode !== "normal_message" || replyingTo) {
      closeLookup();
      return;
    }

    const cursor =
      typeof cursorPosition ===
      "number"
        ? cursorPosition
        : value.length;

    const textBeforeCursor =
      value.slice(0, cursor);


    // --------------------------------------------------------
    // Value lookup inside curly braces
    // --------------------------------------------------------

    const valueContextMatch =
      textBeforeCursor.match(
        /\/([A-Za-z_][A-Za-z0-9_]*)\s*\{([^{}]*)$/
      );

    if (valueContextMatch) {
      const typedVariableName =
        valueContextMatch[1];

      const variableOption =
        findReportVariable(
          typedVariableName
        );

      if (!variableOption) {
        closeLookup();
        return;
      }

      const openBraceIndex =
        textBeforeCursor.lastIndexOf(
          "{"
        );

      const closingBraceIndex =
        value.indexOf(
          "}",
          cursor
        );

      const replaceStart =
        openBraceIndex + 1;

      const replaceEnd =
        closingBraceIndex >= 0
          ? closingBraceIndex
          : cursor;

      /*
       * /date {} is the only variable that uses
       * the calendar picker. It never calls Redis
       * autocomplete, regardless of storage type.
       */
      if (
        isDateVariable(
          variableOption.variable_name
        )
      ) {
        openDateLookup({
          variableName:
            variableOption
              .variable_name,
          replaceStart,
          replaceEnd,
        });

        return;
      }

      /*
       * Manual entry only for non-Redis variables.
       */
      if (
        !variableOption
          .autocomplete_enabled
      ) {
        closeLookup();
        return;
      }

      const typedValue =
        value.slice(
          replaceStart,
          cursor
        );

      scheduleValueAutocomplete({
        variableName:
          variableOption
            .variable_name,

        query:
          typedValue,

        replaceStart,
        replaceEnd,
      });

      return;
    }


    // --------------------------------------------------------
    // Variable name search after /
    // --------------------------------------------------------

    const variableContextMatch =
      textBeforeCursor.match(
        /\/([A-Za-z_]*)$/
      );

    if (variableContextMatch) {
      cancelAutocompleteRequest();

      autocompleteRequestIdRef.current +=
        1;

      const typedVariable =
        variableContextMatch[1]
          .toLowerCase();

      const availableVariables =
        reportVariables
          .map(
            (variable) =>
              variable.variable_name
          )
          .filter(Boolean);

      const filteredVariables =
        availableVariables.filter(
          (variableName) =>
            variableName
              .toLowerCase()
              .includes(
                typedVariable
              )
        );

      const replaceStart =
        cursor -
        variableContextMatch[0]
          .length;

      setLookup({
        open: true,
        mode: "variable",
        activeVar: null,
        options:
          filteredVariables,
        highlightIndex:
          filteredVariables.length >
          0
            ? 0
            : -1,
        replaceStart,
        replaceEnd: cursor,
        loading: false,
        error: null,
        message:
          filteredVariables.length ===
          0
            ? "No matching variables"
            : null,
      });

      return;
    }

    closeLookup();
  }


  function onChatInputChange(
    value,
    cursorPosition
  ) {
    setChatInput(value);

    refreshLookup(
      value,
      cursorPosition
    );
  }


  function onChatCursorChange(
    value,
    cursorPosition
  ) {
    refreshLookup(
      value,
      cursorPosition
    );
  }


  function selectLookupValue(
    selectedOption
  ) {
    if (
      lookup.replaceStart === null ||
      lookup.replaceEnd === null
    ) {
      return;
    }


    // --------------------------------------------------------
    // Variable selected
    // --------------------------------------------------------

    if (
      lookup.mode === "variable"
    ) {
      const variableOption =
        findReportVariable(
          selectedOption
        );

      if (!variableOption) {
        closeLookup();
        return;
      }

      const beforeSelection =
        chatInput.slice(
          0,
          lookup.replaceStart
        );

      const afterSelection =
        chatInput.slice(
          lookup.replaceEnd
        );

      const insertedVariable =
        `/${variableOption.variable_name} {}`;

      const nextValue =
        beforeSelection +
        insertedVariable +
        afterSelection;

      const caretPosition =
        beforeSelection.length +
        insertedVariable.length -
        1;

      setChatInput(nextValue);

      setRequestedCaretPosition(
        caretPosition
      );

      if (
        isDateVariable(
          variableOption.variable_name
        )
      ) {
        openDateLookup({
          variableName:
            variableOption
              .variable_name,
          replaceStart:
            caretPosition,
          replaceEnd:
            caretPosition,
        });
      } else if (
        variableOption
          .autocomplete_enabled
      ) {
        scheduleValueAutocomplete({
          variableName:
            variableOption
              .variable_name,
          query: "",
          replaceStart:
            caretPosition,
          replaceEnd:
            caretPosition,
        });
      } else {
        /*
         * The cursor remains inside {}.
         * User enters the value manually.
         */
        closeLookup();
      }

      return;
    }


    // --------------------------------------------------------
    // Value selected
    // --------------------------------------------------------

    if (
      lookup.mode === "value" &&
      lookup.activeVar
    ) {
      const beforeValue =
        chatInput.slice(
          0,
          lookup.replaceStart
        );

      const afterValue =
        chatInput.slice(
          lookup.replaceEnd
        );

      const suffixWithoutOldBrace =
        afterValue.startsWith("}")
          ? afterValue.slice(1)
          : afterValue;

      const nextValue =
        beforeValue +
        selectedOption +
        "}" +
        suffixWithoutOldBrace;

      const caretPosition =
        beforeValue.length +
        selectedOption.length +
        1;

      setChatInput(nextValue);

      setRequestedCaretPosition(
        caretPosition
      );

      closeLookup();
    }
  }


  function selectDateValue(isoDate) {
    if (
      lookup.mode !== "date" ||
      lookup.replaceStart === null ||
      lookup.replaceEnd === null
    ) {
      return;
    }

    const formattedDate =
      formatDateForChat(isoDate);

    if (!formattedDate) {
      return;
    }

    const beforeValue =
      chatInput.slice(
        0,
        lookup.replaceStart
      );

    const afterValue =
      chatInput.slice(
        lookup.replaceEnd
      );

    const suffixWithoutOldBrace =
      afterValue.startsWith("}")
        ? afterValue.slice(1)
        : afterValue;

    const nextValue =
      beforeValue +
      formattedDate +
      "}" +
      suffixWithoutOldBrace;

    const caretPosition =
      beforeValue.length +
      formattedDate.length +
      1;

    setChatInput(nextValue);

    setRequestedCaretPosition(
      caretPosition
    );

    closeLookup();
  }


  function onChatInputKeyDown(
    event
  ) {
    if (lookup.open) {
      if (lookup.mode === "date") {
        if (
          event.key === "Enter" ||
          event.key === "Tab"
        ) {
          event.preventDefault();
          return;
        }

        if (event.key === "Escape") {
          event.preventDefault();
          closeLookup();
          return;
        }
      }
      if (
        event.key ===
        "ArrowDown"
      ) {
        event.preventDefault();

        if (
          lookup.options.length ===
          0
        ) {
          return;
        }

        setLookup(
          (currentLookup) => ({
            ...currentLookup,
            highlightIndex: Math.min(
              currentLookup.highlightIndex + 1,
              currentLookup.options.length - 1
            ),
          })
        );
        if (lookup.mode === "value" &&
            lookup.highlightIndex >= lookup.options.length - 2) {
          loadMoreLookup();
        }

        return;
      }

      if (
        event.key ===
        "ArrowUp"
      ) {
        event.preventDefault();

        if (
          lookup.options.length ===
          0
        ) {
          return;
        }

        setLookup(
          (currentLookup) => ({
            ...currentLookup,

            highlightIndex:
              Math.max(
                currentLookup
                  .highlightIndex - 1,
                0
              ),
          })
        );

        return;
      }

      if (
        event.key === "Enter" ||
        event.key === "Tab"
      ) {
        if (
          lookup.highlightIndex >= 0 &&
          lookup.options[
            lookup.highlightIndex
          ]
        ) {
          event.preventDefault();

          selectLookupValue(
            lookup.options[
              lookup.highlightIndex
            ]
          );

          return;
        }

        if (
          lookup.mode === "value"
        ) {
          event.preventDefault();
          return;
        }
      }

      if (
        event.key === "Escape"
      ) {
        event.preventDefault();

        closeLookup();
        return;
      }
    }

    if (
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();

      submitChatInput();
    }
  }


  function insertVar(variableName) {
    const variableOption =
      findReportVariable(
        variableName
      );

    if (!variableOption) {
      return;
    }

    const needsSpace =
      chatInput.length > 0 &&
      !chatInput.endsWith(" ");

    const prefix =
      chatInput +
      (needsSpace ? " " : "");

    const insertedVariable =
      `/${variableOption.variable_name} {}`;

    const nextValue =
      prefix +
      insertedVariable;

    const caretPosition =
      nextValue.length - 1;

    setChatInput(nextValue);

    setRequestedCaretPosition(
      caretPosition
    );

    if (
      isDateVariable(
        variableOption.variable_name
      )
    ) {
      openDateLookup({
        variableName:
          variableOption
            .variable_name,
        replaceStart:
          caretPosition,
        replaceEnd:
          caretPosition,
      });
    } else if (
      variableOption
        .autocomplete_enabled
    ) {
      scheduleValueAutocomplete({
        variableName:
          variableOption
            .variable_name,
        query: "",
        replaceStart:
          caretPosition,
        replaceEnd:
          caretPosition,
      });
    } else {
      closeLookup();
    }
  }


  useEffect(() => {
    return () => {
      cancelAutocompleteRequest();
    };
  }, []);


  // ==========================================================
  // Report modal
  // ==========================================================

  const [
    reportModal,
    setReportModal,
  ] = useState({
    open: false,
    data: null,
  });


function openReport(source) {
  /*
   * Existing mock/history report.
   */
  if (
    typeof source === "string"
  ) {
    const data =
      reportData[source];

    if (!data) {
      return;
    }

    setReportModal({
      open: true,
      data,
    });

    return;
  }

  /*
   * Live SQL result table.
   */
  if (
    source &&
    Array.isArray(source.rows) &&
    source.rows.length > 0
  ) {
    const columnSet =
      new Set();

    source.rows.forEach((row) => {
      if (
        row &&
        typeof row === "object"
      ) {
        Object.keys(row).forEach(
          (column) =>
            columnSet.add(column)
        );
      }
    });

    const columns =
      Array.from(columnSet);

    const rows =
      source.rows.map((row) =>
        columns.map((column) => {
          const value =
            row?.[column];

          if (
            value === null ||
            value === undefined
          ) {
            return null;
          }

          if (
            typeof value === "object"
          ) {
            return JSON.stringify(
              value
            );
          }

          return value;
        })
      );

    setReportModal({
      open: true,

      data: {
        title: source.title || "Query Results",
        columns,
        rows,
        messageId: source.messageId || null,
      },
    });
  }
}


  function closeReport() {
    setReportModal({
      open: false,
      data: null,
    });
  }


  // ==========================================================
  // Help
  // ==========================================================

  const [
    helpOpen,
    setHelpOpen,
  ] = useState(false);


  function toggleHelp() {
    setHelpOpen(
      (currentValue) =>
        !currentValue
    );
  }


  const value = {
    // Layout
    sidebarCollapsed,
    setSidebarCollapsed,
    sidebarWidth,
    setSidebarWidth,

    // Navigation
    navigationData,
    navigationLoading,
    navigationError,

    baseTabs,
    selectedBaseTab,
    selectedBaseTabId,
    selectBaseTab,

    subTabs,
    selectedSubTab,
    selectedSubTabId,
    selectSubTab,

    reports,
    selectedReport,
    selectedReportId,
    selectReport,
    conversationReportName,
    reportLocked,

    reportOptions,

    reportVariables,
    reportVariablesLoading,
    reportVariablesError,
    reportAutocompleteController,
    autocompleteControllerOption,
    selectedControllerValue,

    selectedOption,
    selectedOptionId,
    selectOption,

    selectedOptionValue,
    setSelectedOptionValue,

    selectedOptionValues,
    setReportOptionValue,
    getReportOptionValue,
    clearReportOptionValues,

    report,
    division,

    // History
    historyItems,
    historyLoading,
    historyError,
    activeHistoryId,
    deletingHistoryId,
    selectHistoryItem,
    deleteHistoryItem,

    // Messages
    messages,
    sendMessage,
    editMessage,
    submitChatInput,
    editingMessageId,
    startEditingMessage,
    cancelEditingMessage,
    chatInput,
    inputDisabled,
    sending,
    requestMode,
    setRequestMode,

    feedback,
    replyTo,
    replyingTo,
    cancelReply,
    newChat,

    pickSuggestOption,
    pickOther,
    pendingSuggestId,

    // Lookup
    lookup,
    loadMoreLookup,
    retryMoreLookup,
    onChatInputChange,
    onChatCursorChange,
    onChatInputKeyDown,
    selectLookupValue,
    selectDateValue,
    insertVar,
    closeLookup,

    requestedCaretPosition,
    clearRequestedCaretPosition,

    // Report modal
    reportModal,
    openReport,
    closeReport,

    // Help
    helpOpen,
    toggleHelp,
  };


  return (
    <ChatContext.Provider
      value={value}
    >
      {children}
    </ChatContext.Provider>
  );
}


export function useChat() {
  const context =
    useContext(ChatContext);

  if (!context) {
    throw new Error(
      "useChat must be used inside a ChatProvider"
    );
  }

  return context;
}