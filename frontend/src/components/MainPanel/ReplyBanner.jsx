import { useChat } from "../../context/ChatContext";

export default function ReplyBanner() {
  const { replyingTo, cancelReply } = useChat();
  if (!replyingTo) return null;

  const snippet =
    replyingTo.text.length > 70
      ? replyingTo.text.slice(0, 70) + "…"
      : replyingTo.text;

  return (
    <div className="reply-banner show">
      <div className="reply-banner-text">
        Modifying Primary result: <strong>{snippet}</strong>
      </div>
      <button
        className="reply-banner-close"
        onClick={cancelReply}
        aria-label="Cancel modification"
      >
        ✕
      </button>
    </div>
  );
}
