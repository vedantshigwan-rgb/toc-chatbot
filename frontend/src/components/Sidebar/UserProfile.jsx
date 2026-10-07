import { useAuth } from "../../context/AuthContext";
import { useChat } from "../../context/ChatContext";

function initials(user) {
  const source = String(user?.name || user?.email || "U").trim();
  const words = source.split(/\s+/).filter(Boolean);
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase();
  }
  return source.slice(0, 2).toUpperCase();
}

export default function UserProfile() {
  const { user, logout } = useAuth();
  const { division } = useChat();

  return (
    <div className="user-profile">
      <div className="user-avatar">{initials(user)}</div>
      <div className="user-profile-copy">
        <div className="user-name">{user?.name || user?.email || "User"}</div>
        <div className="user-role">{division.name}</div>
      </div>
      <button type="button" className="logout-btn" onClick={logout} title="Sign out">
        Sign out
      </button>
    </div>
  );
}
