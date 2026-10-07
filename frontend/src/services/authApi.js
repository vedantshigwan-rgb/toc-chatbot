const CHAT_API_BASE_URL =
  import.meta.env.VITE_CHAT_API_BASE_URL ||
  "http://localhost:8002";

const TOKEN_STORAGE_KEY = "toc_access_token";

export function getAccessToken() {
  return window.localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function storeAccessToken(token) {
  if (!token) {
    window.localStorage.removeItem(TOKEN_STORAGE_KEY);
    return;
  }
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

export function clearAccessToken() {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY);
}

function formatApiError(responseData, status) {
  const detail = responseData?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          return item.msg || item.message || JSON.stringify(item);
        }
        return String(item);
      })
      .join(" | ");
  }

  if (detail && typeof detail === "object") {
    return JSON.stringify(detail);
  }

  return responseData?.message || `Request failed with status ${status}.`;
}

export async function readApiJson(response) {
  let responseData = null;

  try {
    responseData = await response.json();
  } catch {
    responseData = null;
  }

  if (!response.ok) {
    const error = new Error(formatApiError(responseData, response.status));
    error.status = response.status;
    error.action = responseData?.action || null;
    error.code = responseData?.error?.code || null;
    error.responseData = responseData;
    throw error;
  }

  return responseData;
}

export async function authenticatedFetch(path, options = {}) {
  const token = getAccessToken();
  const headers = new Headers(options.headers || {});

  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  const response = await fetch(`${CHAT_API_BASE_URL}${path}`, {
    ...options,
    headers,
  });

  if (response.status === 401) {
    clearAccessToken();
    window.dispatchEvent(new Event("toc-auth-expired"));
  }

  return response;
}

async function publicPost(path, payload, signal) {
  const response = await fetch(`${CHAT_API_BASE_URL}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  return readApiJson(response);
}

export async function login({ email, password, signal }) {
  return publicPost("/auth/login", { email, password }, signal);
}

export async function register({ email, password, name, signal }) {
  return publicPost(
    "/auth/register",
    { email, password, name: name?.trim() || null },
    signal
  );
}

export async function verifyRegistration({ email, otp, signal }) {
  return publicPost("/auth/register/verify", { email, otp }, signal);
}

export async function resendRegistrationOtp({ email, signal }) {
  return publicPost("/auth/register/resend-otp", { email }, signal);
}

export async function fetchCurrentUser({ signal } = {}) {
  const response = await authenticatedFetch("/auth/me", {
    method: "GET",
    headers: { Accept: "application/json" },
    signal,
  });

  return readApiJson(response);
}
