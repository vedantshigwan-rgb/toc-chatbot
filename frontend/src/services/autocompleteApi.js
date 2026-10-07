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

  const parameters =
    new URLSearchParams({
      report_name: cleanedReportName,
      variable_name: cleanedVariableName,
      limit: String(limit),
    });

  if (cleanedQuery) {
    parameters.set("q", cleanedQuery);
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

  if (!Array.isArray(responseData?.values)) {
    return [];
  }

  return responseData.values.map(
    (value) => String(value)
  );
}
