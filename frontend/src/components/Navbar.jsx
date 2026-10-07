import { useChat } from "../context/ChatContext";

export default function Navbar() {
  const { setSidebarCollapsed } = useChat();

  return (
    <div className="navbar">
      <div className="navbar-left">
        <button
          className="sidebar-toggle"
          onClick={() => setSidebarCollapsed((v) => !v)}
          title="Show or hide sidebar"
        >
          ☰
        </button>
        {/* <div className="navbar-brand">
          <svg width="24" height="24" viewBox="0 0 26 26">
            <polygon points="13,1 25,13 13,25 1,13" fill="#19988b" />
            <polygon points="13,7 19,13 13,19 7,13" fill="#c37c5b" />
          </svg>
          <div>
            <div className="navbar-brand-name">Aeris</div>
            <div className="navbar-brand-sub">Report assistant</div>
          </div>
        </div> */}
      </div>

      <div className="navbar-right">
        <span className="company-name">Kirloskar Pneumatic Co Ltd</span>
        <div className="company-logo">KPCL</div>
      </div>
    </div>
  );
}
