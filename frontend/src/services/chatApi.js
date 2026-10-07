import {
  authenticatedFetch,
  readApiJson,
} from "./authApi";

export async function sendAskRequest({
  input,
  messageType = "normal_message",
  reportName = null,
  variables = [],
  chatId = null,
  replyToMessageId = null,
  signal,
}) {
  const payload = {
    message_type: messageType,
    input,
    report_name: reportName || null,
    variables,
    chat_id: chatId || null,
  };

  if (messageType === "feedback") {
    payload.reply_to_message_id = replyToMessageId || null;
  }

  const response = await authenticatedFetch("/ask", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
    signal,
  });

  const responseData = await readApiJson(response);

  return {
    payload,
    response: responseData,
  };
}

export async function fetchChats({ signal, limit = 50 } = {}) {
  const response = await authenticatedFetch(
    `/chat/chats?limit=${encodeURIComponent(limit)}`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    }
  );

  const responseData = await readApiJson(response);
  return Array.isArray(responseData?.chats) ? responseData.chats : [];
}

export async function fetchChatMessages({ chatId, signal }) {
  if (!chatId) return [];

  const response = await authenticatedFetch(
    `/chat/chats/${encodeURIComponent(chatId)}/messages`,
    {
      method: "GET",
      headers: { Accept: "application/json" },
      signal,
    }
  );

  const responseData = await readApiJson(response);
  return Array.isArray(responseData?.messages) ? responseData.messages : [];
}

export async function deleteChatSession({ chatId, signal }) {
  if (!chatId) {
    throw new Error("chatId is required to delete a chat.");
  }

  const response = await authenticatedFetch(
    `/chat/chats/${encodeURIComponent(chatId)}`,
    {
      method: "DELETE",
      headers: { Accept: "application/json" },
      signal,
    }
  );

  return readApiJson(response);
}

export async function submitMessageFeedback({
  messageId,
  rating,
  signal,
}) {
  const response = await authenticatedFetch(
    `/chat/messages/${encodeURIComponent(messageId)}/feedback`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify({ rating }),
      signal,
    }
  );

  return readApiJson(response);
}
