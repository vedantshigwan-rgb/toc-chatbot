import { ACTION_BADGES } from "../../utils/responseActions";

export default function ActionMessage({ message }) {
  const badgeLabel =
    ACTION_BADGES[message.kind] ||
    ACTION_BADGES[message.action] ||
    "Action needed";

  return (
    <div className={`msg-action kind-${message.kind}`}>
      <div className="msg-action-badge">{badgeLabel}</div>
      <div className="msg-action-text">{message.text}</div>
    </div>
  );
}
