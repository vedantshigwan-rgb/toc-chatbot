import { getAccessToken } from "./authApi";
import { normaliseVariable } from "../utils/autocompleteContext";
import { normaliseSuggestionPage } from "../utils/suggestionPaging";

const AUTOCOMPLETE_API_BASE_URL =
  import.meta.env.VITE_AUTOCOMPLETE_API_BASE_URL ||
  "http://127.0.0.1:6969";


function formatApiError(responseData, status) {
  const detail = responseData?.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }

        if (item && typeof item === "object") {
          const location = Array.isArray(item.loc)
            ? item.loc.join(".")
            : "";

          const message =
            item.msg ||
            item.message ||
            "Validation error";

          return location
            ? `${location}: ${message}`
            : message;
        }

        return String(item);
      })
      .filter(Boolean);

    if (messages.length > 0) {
      return messages.join(" | ");
    }
  }

  if (detail && typeof detail === "object") {
    try {
      return JSON.stringify(detail);
    } catch {
      return "API validation error";
    }
  }

  return (
    responseData?.message ||
    `Request failed with status ${status}.`
  );
}


async function readJsonResponse(response) {
  let responseData = null;

  try {
    responseData = await response.json();
  } catch {
    responseData = null;
  }

  if (!response.ok) {
    throw new Error(
      formatApiError(
        responseData,
        response.status
      )
    );
  }

  return responseData;
}

function filterItemCodeSuggestions(page, variableName) {
  const normalizedName = normaliseVariable(variableName);

  // Apply only to the item-code variable.
  if (normalizedName !== "item") {
    return page;
  }

  const values = page.values.filter(
    (value) => !String(value).trim().toUpperCase().startsWith("N")
  );

  return {
    ...page,
    values,
  };
}

/**
 * Load every mapped variable for one report.
 *
 * The backend returns both:
 * - Redis variables
 * - Non-Redis variables
 */
export async function fetchReportVariables({
  reportName,
  signal,
}) {
  const cleanedReportName =
    String(reportName || "").trim();

  if (!cleanedReportName) {
    throw new Error(
      "Report name is required to load variables."
    );
  }

  const parameters =
    new URLSearchParams({
      report_name: cleanedReportName,
    });

  const response = await fetch(
    `${AUTOCOMPLETE_API_BASE_URL}/variables?${parameters.toString()}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    }
  );

  const responseData =
    await readJsonResponse(response);

  const details = Array.isArray(
    responseData?.variable_details
  )
    ? responseData.variable_details
    : [];

  return {
    requestedReportName: String(
      responseData?.requested_report_name ||
      cleanedReportName
    ),

    reportName: String(
      responseData?.report_name ||
      cleanedReportName
    ),

    autocompleteController:
      responseData?.autocomplete_controller &&
      typeof responseData.autocomplete_controller === "object"
        ? {
            variable_name: String(
              responseData.autocomplete_controller.variable_name || ""
            ),
            required_for_dependent_variables:
              responseData.autocomplete_controller
                .required_for_dependent_variables === true,
          }
        : null,

    variables: Array.isArray(
      responseData?.variables
    )
      ? responseData.variables.map(
          (variableName) =>
            String(variableName)
        )
      : [],

    variableDetails: details.map(
      (variable) => ({
        variable_name: String(
          variable?.variable_name || ""
        ),

        column_name: String(
          variable?.column_name || ""
        ),

        table_name: String(
          variable?.table_name || ""
        ),

        storage_type: String(
          variable?.storage_type || ""
        ),

        autocomplete_enabled:
          variable?.autocomplete_enabled === true,
      })
    ),
  };
}


/**
 * Load autocomplete values for one Redis variable.
 */
export async function fetchAutocompleteValues({
  reportName,
  variableName,
  query = "",
  limit = 20,
  offset = 0,
  controllerVariableName = "",
  controllerValue = "",
  periodType = "",
  contextFilters = {},
  signal,
}) {
  const cleanedReportName =
    String(reportName || "").trim();

  const cleanedVariableName =
    String(variableName || "").trim();

  const cleanedQuery =
    String(query || "").trim();

  if (!cleanedReportName) {
    throw new Error(
      "Report name is required for autocomplete."
    );
  }

  if (!cleanedVariableName) {
    throw new Error(
      "Variable name is required for autocomplete."
    );
  }

  // Keep the existing Redis organization-scoped autocomplete untouched.
  // The contextual path is ADDITIONAL and activates only when another
  // selected variable restricts the target beyond the main controller.
  const targetId = normaliseVariable(cleanedVariableName);
  const controllerId = normaliseVariable(controllerVariableName);
  const extraFilters = Object.keys(contextFilters || {}).filter((name) => {
    const id = normaliseVariable(name);
    return id && id !== targetId && id !== controllerId;
  });
  if (extraFilters.length > 0) {
    const contextualResponse = await fetch(
      `${AUTOCOMPLETE_API_BASE_URL}/autocomplete/contextual`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${getAccessToken() || ""}` },
        body: JSON.stringify({
          report_name: cleanedReportName,
          target_variable: cleanedVariableName,
          filters: contextFilters,
          search_text: cleanedQuery,
          limit,
          offset,
        }),
        signal,
      }
    );
    const contextualData = await readJsonResponse(contextualResponse);
    return filterItemCodeSuggestions(
      normaliseSuggestionPage(contextualData, offset),
      cleanedVariableName
    );
  }

  const parameters =
    new URLSearchParams({
      report_name: cleanedReportName,
      variable_name: cleanedVariableName,
      limit: String(limit),
      offset: String(offset),
    });

  if (cleanedQuery) {
    parameters.set("q", cleanedQuery);
  }

  const cleanedControllerVariableName =
    String(controllerVariableName || "").trim();

  const cleanedControllerValue =
    String(controllerValue || "").trim();

  if (cleanedControllerVariableName) {
    parameters.set(
      "controller_variable_name",
      cleanedControllerVariableName
    );
  }

  if (cleanedControllerValue) {
    parameters.set(
      "controller_value",
      cleanedControllerValue
    );
  }

  if (String(periodType || "").trim()) {
    parameters.set("period_type", String(periodType).trim());
  }

  const response = await fetch(
    `${AUTOCOMPLETE_API_BASE_URL}/autocomplete?${parameters.toString()}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      signal,
    }
  );

  const responseData =
    await readJsonResponse(response);

  return filterItemCodeSuggestions(
    normaliseSuggestionPage(responseData, offset),
    cleanedVariableName
  );
}


/** Fetch PERIOD_TYPE, or PERIOD_NAME after a type is selected. */
export async function fetchPeriodOptions({
  reportName,
  orgValue,
  periodType = "",
  query = "",
  limit = 40,
  offset = 0,
  contextFilters = {},
  signal,
}) {
  // Keep existing org/type Redis period lists as the fast default.
  // Only an additional variable activates contextual Oracle-on-miss.
  const extra = Object.keys(contextFilters || {}).some((name) =>
    !["org_name", "employee_org", "period_type", "period_name"].includes(
      normaliseVariable(name)
    )
  );
  if (extra) {
    const response = await fetch(`${AUTOCOMPLETE_API_BASE_URL}/autocomplete/contextual`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", Authorization: `Bearer ${getAccessToken() || ""}` },
      body: JSON.stringify({
        report_name: reportName,
        target_variable: periodType ? "period_name" : "period_type",
        filters: contextFilters,
        search_text: query,
        limit,
        offset,
      }),
      signal,
    });
    const data = await readJsonResponse(response);
    return normaliseSuggestionPage(data, offset);
  }
  const params = new URLSearchParams({
    report_name: String(reportName || "").trim(),
    org_value: String(orgValue || "").trim(),
    limit: String(limit),
    offset: String(offset),
  });
  if (periodType) params.set("period_type", String(periodType).trim());
  if (query) params.set("q", String(query).trim());

  const response = await fetch(
    `${AUTOCOMPLETE_API_BASE_URL}/period-options?${params.toString()}`,
    { method: "GET", headers: { Accept: "application/json" }, signal }
  );
  const data = await readJsonResponse(response);
  return normaliseSuggestionPage(data, offset);
}
