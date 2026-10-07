const AUTOCOMPLETE_API_BASE_URL =
  import.meta.env.VITE_AUTOCOMPLETE_API_BASE_URL ||
  "http://127.0.0.1:6969";


function formatApiError(responseData, status) {
  const detail = responseData?.detail;

  if (typeof detail === "string") {
    return detail;
  }

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") {
          return item;
        }

        if (item && typeof item === "object") {
          return (
            item.msg ||
            item.message ||
            JSON.stringify(item)
          );
        }

        return String(item);
      })
      .join(" | ");
  }

  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }

  return (
    responseData?.message ||
    `Navigation request failed with status ${status}.`
  );
}


export async function fetchNavigationHierarchy({
  signal,
} = {}) {
  const response = await fetch(
    `${AUTOCOMPLETE_API_BASE_URL}/navigation`,
    {
      method: "GET",

      headers: {
        Accept: "application/json",
      },

      signal,
    }
  );

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

  if (
    !responseData ||
    !Array.isArray(responseData.base_tabs)
  ) {
    throw new Error(
      "Navigation API returned an invalid response."
    );
  }

  return responseData;
}