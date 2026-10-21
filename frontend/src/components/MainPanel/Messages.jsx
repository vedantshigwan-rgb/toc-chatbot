import { useEffect, useRef, useState } from "react";
import { useChat } from "../../context/ChatContext";
import {
  UserMessage,
  BotMessage,
  SuggestBlock,
  ConfirmMessage,
} from "./MessageBubbles";
import ActionMessage from "./ActionMessage";

export default function Messages() {
  const { messages } = useChat();
  const latestUserMessageId = [...messages]
    .reverse()
    .find((message) => message.type === "user")?.id;
  const messagesRef = useRef(null);
  const [showScrollBtn, setShowScrollBtn] = useState(false);

  function isNearBottom() {
    const el = messagesRef.current;
    if (!el) return true;
    return el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  function updateScrollBtn() {
    setShowScrollBtn(!isNearBottom());
  }

  function scrollToBottom() {
    messagesRef.current?.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: "smooth",
    });
  }

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
    updateScrollBtn();
  }, [messages]);

  return (
    <div className="messages-wrap">
      <div
        className="messages"
        id="messages"
        ref={messagesRef}
        onScroll={updateScrollBtn}
      >
        {messages.map((message) => {
          switch (message.type) {
            case "user":
              return (<UserMessage key={message.id} message={message} isLatest={message.id === latestUserMessageId} />);
            case "bot":
              return <BotMessage key={message.id} message={message} />;
            case "suggest":
              return <SuggestBlock key={message.id} message={message} />;
            case "confirm":
              return <ConfirmMessage key={message.id} message={message} />;
            case "action":
              return <ActionMessage key={message.id} message={message} />;
            default:
              return null;
          }
        })}
      </div>
      <button
        className={"scroll-bottom-btn" + (showScrollBtn ? " show" : "")}
        onClick={scrollToBottom}
        aria-label="Jump to latest message"
      >
        ↓
      </button>
    </div>
  );
}
