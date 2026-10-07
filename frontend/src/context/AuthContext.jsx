import {
  createContext,
  useContext,
  useEffect,
  useState,
} from "react";

import {
  clearAccessToken,
  fetchCurrentUser,
  getAccessToken,
  login as loginRequest,
  register as registerRequest,
  resendRegistrationOtp,
  storeAccessToken,
  verifyRegistration,
} from "../services/authApi";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);

  async function restoreSession() {
    const token = getAccessToken();
    if (!token) {
      setUser(null);
      setCheckingAuth(false);
      return;
    }

    try {
      const response = await fetchCurrentUser();
      setUser(response?.user || null);
    } catch {
      clearAccessToken();
      setUser(null);
    } finally {
      setCheckingAuth(false);
    }
  }

  useEffect(() => {
    restoreSession();

    const handleExpired = () => {
      clearAccessToken();
      setUser(null);
      setCheckingAuth(false);
    };

    window.addEventListener("toc-auth-expired", handleExpired);
    return () => {
      window.removeEventListener("toc-auth-expired", handleExpired);
    };
  }, []);

  async function login(credentials) {
    const response = await loginRequest(credentials);
    storeAccessToken(response.access_token);
    setUser(response.user || null);
    return response;
  }

  function logout() {
    clearAccessToken();
    setUser(null);
  }

  const value = {
    user,
    checkingAuth,
    isAuthenticated: Boolean(user && getAccessToken()),
    login,
    logout,
    register: registerRequest,
    verifyRegistration,
    resendRegistrationOtp,
  };

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used inside an AuthProvider");
  }
  return context;
}
