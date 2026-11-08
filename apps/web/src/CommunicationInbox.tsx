/**
 * Communication inbox - M72 Classroom Communication & Learning Progress Portal.
 *
 * Displays classroom messages for the current session, supports sending new
 * messages (classroom broadcast or direct to a participant), and tracks
 * read/unread status. All access is server-side authorized; this component
 * only renders what the backend permits for the current user.
 */

import { useCallback, useEffect, useState } from "react";

type ClassroomMessage = {
  messageId: string;
  sessionId: string;
  senderId: string;
  recipientId: string | null;
  messageType: "classroom" | "direct" | "feedback" | "progress_update";
  content: string;
  createdAt: string;
  updatedAt: string;
  isRead: boolean;
  readAt: string | null;
};

type UnreadCount = {
  sessionId: string;
  unreadCount: number;
};

type Participant = {
  userId: string;
  username: string | null;
  role: string;
};

interface CommunicationInboxProps {
  sessionId: string;
  sessionTitle: string;
  participants: Participant[];
  currentUserId: string;
  currentUserRole: string;
  onUnreadChange?: (counts: UnreadCount[]) => void;
}

export default function CommunicationInbox({
  sessionId,
  sessionTitle,
  participants,
  currentUserId,
  onUnreadChange,
}: CommunicationInboxProps) {
  const [messages, setMessages] = useState<ClassroomMessage[]>([]);
  const [unreadCounts, setUnreadCounts] = useState<UnreadCount[]>([]);
  const [composeText, setComposeText] = useState("");
  const [composeType, setComposeType] = useState<"classroom" | "direct">("classroom");
  const [composeRecipientId, setComposeRecipientId] = useState<string | null>(null);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const fetchMessages = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(sessionId)}/messages`
      );
      if (!response.ok) {
        setError("Could not load classroom messages.");
        return;
      }
      const data = (await response.json()) as { messages: ClassroomMessage[] };
      setMessages(data.messages ?? []);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not load messages.");
    }
  }, [sessionId]);

  const fetchUnreadCounts = useCallback(async () => {
    try {
      const response = await fetch("/api/v1/messages/unread-counts");
      if (!response.ok) return;
      const data = (await response.json()) as { unreadCounts: UnreadCount[] };
      const counts = data.unreadCounts ?? [];
      setUnreadCounts(counts);
      onUnreadChange?.(counts);
    } catch {
      // Non-blocking: unread counts are a convenience, not critical.
    }
  }, [onUnreadChange]);

  useEffect(() => {
    void fetchMessages();
    void fetchUnreadCounts();
    const interval = setInterval(() => {
      void fetchMessages();
      void fetchUnreadCounts();
    }, 15000);
    return () => clearInterval(interval);
  }, [sessionId, fetchMessages, fetchUnreadCounts]);

  const handleSend = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = composeText.trim();
    if (!text) return;
    setSending(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(sessionId)}/messages`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sessionId,
            senderId: currentUserId,
            content: text,
            messageType: composeType,
            recipientId:
              composeType === "direct" ? composeRecipientId : undefined,
          }),
        },
      );
      if (!response.ok) {
        const body = (await response.json()) as { error?: { message?: string } };
        throw new Error(
          body.error?.message ?? "Could not send message.",
        );
      }
      setComposeText("");
      setComposeType("classroom");
      setComposeRecipientId(null);
      setStatusMessage("Message sent.");
      setTimeout(() => setStatusMessage(null), 3000);
      void fetchMessages();
      void fetchUnreadCounts();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not send message.");
    } finally {
      setSending(false);
    }
  };

  const handleMarkRead = async (messageId: string) => {
    try {
      const response = await fetch(
        `/api/v1/messages/${encodeURIComponent(messageId)}/read`,
        { method: "PATCH" },
      );
      if (!response.ok) return;
      setMessages((prev) =>
        prev.map((m) =>
          m.messageId === messageId
            ? { ...m, isRead: true, readAt: new Date().toISOString() }
            : m,
        ),
      );
      void fetchUnreadCounts();
    } catch {
      // Non-blocking.
    }
  };

  const directParticipants = participants.filter(
    (p) => p.userId !== currentUserId,
  );

  const totalUnread = unreadCounts.reduce(
    (sum, c) => sum + c.unreadCount,
    0,
  );

  return (
    <section className="communication-inbox" aria-labelledby="communication-inbox-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Classroom communication</p>
          <h2 id="communication-inbox-title">{sessionTitle}</h2>
          <p className="muted">
            Messages and read/unread status for this classroom.
            {totalUnread > 0 ? `${totalUnread} unread message(s).` : ""}
          </p>
        </div>
        <div className="explorer-controls">
          {statusMessage && (
            <span className="notice" role="status">{statusMessage}</span>
          )}
          {error && (
            <span className="notice notice--error" role="alert">{error}</span>
          )}
        </div>
      </div>

      <div className="explorer-body">
        <div className="message-list">
          {messages.length === 0 ? (
            <p className="muted">No messages yet. Start the conversation.</p>
          ) : (
            <ul className="message-thread" role="list" aria-label="Classroom messages">
              {messages.map((message) => (
                <li
                  key={message.messageId}
                  className={`message-item ${message.senderId === currentUserId ? "message-item--own" : ""} ${message.isRead ? "" : "message-item--unread"}`}
                >
                  <div className="message-header">
                    <span
                      className="message-sender"
                      title={message.senderId}
                    >
                      {message.senderId === currentUserId
                        ? "You"
                        : message.senderId.slice(0, 12)}
                    </span>
                    {message.messageType === "direct" && message.recipientId && (
                      <span className="message-type-badge">Direct</span>
                    )}
                    {!message.isRead && (
                      <span className="message-type-badge message-type-badge--unread">Unread</span>
                    )}
                  </div>
                  <p className="message-content">{message.content}</p>
                  <p className="message-meta">
                    {new Date(message.createdAt).toLocaleString()}
                    {message.isRead
                      ? ` · read ${new Date(message.readAt ?? message.createdAt).toLocaleString()}`
                      : ""}
                  </p>
                  {!message.isRead && message.senderId !== currentUserId && (
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => void handleMarkRead(message.messageId)}
                    >
                      Mark as read
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="compose-form">
          <form onSubmit={handleSend} className="compose-form-inner">
            <label htmlFor="compose-message" className="sr-only">
              Message
            </label>
            <textarea
              id="compose-message"
              value={composeText}
              onChange={(event) => setComposeText(event.currentTarget.value)}
              placeholder="Write a message…"
              rows={2}
              maxLength={4000}
              disabled={sending}
            />
            <div className="compose-controls">
              <label className="document-control" htmlFor="compose-type">
                Message type
                <select
                  id="compose-type"
                  value={composeType}
                  onChange={(event) =>
                    setComposeType(
                      event.currentTarget.value as "classroom" | "direct",
                    )}
                  disabled={sending}
                >
                  <option value="classroom">Classroom (everyone)</option>
                  <option value="direct">Direct message</option>
                </select>
              </label>
              {composeType === "direct" && directParticipants.length > 0 && (
                <label className="document-control" htmlFor="compose-recipient">
                  To
                  <select
                    id="compose-recipient"
                    value={composeRecipientId ?? ""}
                    onChange={(event) =>
                      setComposeRecipientId(
                        event.currentTarget.value || null,
                      )}
                    disabled={sending}
                  >
                    <option value="">Select a participant</option>
                    {directParticipants.map((p) => (
                      <option key={p.userId} value={p.userId}>
                        {p.username ?? p.userId.slice(0, 12)}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <button
                type="submit"
                className="camera-action camera-action--primary"
                disabled={sending || !composeText.trim() || (composeType === "direct" && !composeRecipientId)}
              >
                {sending ? "Sending…" : "Send"}
              </button>
            </div>
          </form>
        </div>
      </div>
    </section>
  );
}
