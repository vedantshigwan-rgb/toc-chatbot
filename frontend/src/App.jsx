import { AuthProvider, useAuth } from "./context/AuthContext";
import { ChatProvider } from "./context/ChatContext";
import AuthScreen from "./components/AuthScreen";
import Navbar from "./components/Navbar";
import Sidebar from "./components/Sidebar/Sidebar";
import MainPanel from "./components/MainPanel/MainPanel";
import HelpPanel from "./components/HelpPanel";
import ReportModal from "./components/ReportModal";

function AuthenticatedApp() {
  const { checkingAuth, isAuthenticated } = useAuth();

  if (checkingAuth) {
    return (
      <div className="auth-page">
        <div className="auth-card auth-loading">Checking session…</div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <AuthScreen />;
  }

  return (
    <ChatProvider>
      <Navbar />
      <div className="app">
        <Sidebar />
        <MainPanel />
      </div>
      <HelpPanel />
      <ReportModal />
    </ChatProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <AuthenticatedApp />
    </AuthProvider>
  );
}
