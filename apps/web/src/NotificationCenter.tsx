/**
 * Notification center - M72 Classroom Communication & Learning Progress Portal.
 *
 * Displays in-app notifications for the current user, supports marking
 * notifications as read, and shows unread counts. All access is
 * server-side authorized: users only see their own notifications.
 */

import { useCallback, useEffect, useState } from "react";

type Notification = {
  notificationId: string;
  userId: string;
  type:
    | "announcement"
    | "assignment"
    | "feedback"
    | "assessment_result"
    | "scheduled_activity"
    | "progress_update";
  title: string;
  body: string;
  relatedId: string | null;
  relatedType: string | null;
  isRead: boolean;
  readAt: string | null;
  createdAt: string;
  expiresAt: string | null;
};

interface NotificationCenterProps {
  currentUserId: string;
  onUnreadChange?: (count: number) => void;
}

const TYPE_LABELS: Record<string, string> = {
  announcement: "Announcement",
  assignment: "Assignment",
  feedback: "Feedback",
  assessment_result: "Assessment result",
  scheduled_activity: "Scheduled activity",
  progress_update: "Progress update",
};

export default function NotificationCenter({
  onUnreadChange,
}: NotificationCenterProps) {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchNotifications = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = unreadOnly ? "?unread_only=true" : "";
      const response = await fetch(`/api/v1/notifications${params}`);
      if (!response.ok) {
        setError("Could not load notifications.");
        return;
      }
      const data = (await response.json()) as { notifications: Notification[] };
      setNotifications(data.notifications ?? []);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not load notifications.");
    } finally {
      setLoading(false);
    }
  }, [unreadOnly]);

  useEffect(() => {
    void fetchNotifications();
    const interval = setInterval(() => void fetchNotifications(), 30000);
    return () => clearInterval(interval);
  }, [fetchNotifications]);

  const handleMarkRead = async (notificationId: string) => {
    try {
      const response = await fetch(
        `/api/v1/notifications/${encodeURIComponent(notificationId)}/read`,
        { method: "PATCH" },
      );
      if (!response.ok) return;
      setNotifications((prev) =>
        prev.map((n) =>
          n.notificationId === notificationId
            ? { ...n, isRead: true, readAt: new Date().toISOString() }
            : n,
        ),
      );
    } catch {
      // Non-blocking.
    }
  };

  const unreadCount = notifications.filter((n) => !n.isRead).length;

  useEffect(() => {
    onUnreadChange?.(unreadCount);
  }, [unreadCount, onUnreadChange]);

  const typeColor: Record<string, string> = {
    announcement: "#1f6a3d",
    assignment: "#205e49",
    feedback: "#0f766e",
    assessment_result: "#17613d",
    scheduled_activity: "#55766a",
    progress_update: "#347552",
  };

  return (
    <section className="notification-center" aria-labelledby="notification-center-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Notifications</p>
          <h2 id="notification-center-title">Notification center</h2>
          <p className="muted">
            In-app notifications for announcements, assignments, feedback,
            assessment results, and progress updates.
            {unreadCount > 0 ? `${unreadCount} unread.` : "All caught up."}
          </p>
        </div>
        <div className="explorer-controls">
          <label className="document-control" htmlFor="notif-unread-only">
            Unread only
            <input
              id="notif-unread-only"
              type="checkbox"
              checked={unreadOnly}
              onChange={(event) => setUnreadOnly(event.currentTarget.checked)}
            />
          </label>
          {loading && <span className="muted">Loading…</span>}
          {error && (
            <span className="notice notice--error" role="alert">{error}</span>
          )}
        </div>
      </div>

      <div className="explorer-body">
        <div className="notification-list">
          {notifications.length === 0 ? (
            <p className="muted">
              {unreadOnly ? "No unread notifications." : "No notifications."}
            </p>
          ) : (
            <ul className="notification-thread" role="list" aria-label="Notifications">
              {notifications.map((notification) => (
                <li
                  key={notification.notificationId}
                  className={`notification-item ${notification.isRead ? "" : "notification-item--unread"}`}
                >
                  <div className="notification-header">
                    <span
                      className="notification-type"
                      style={{ color: typeColor[notification.type] ?? "#466259" }}
                    >
                      {TYPE_LABELS[notification.type] ?? notification.type}
                    </span>
                    {!notification.isRead && (
                      <span className="notification-badge notification-badge--unread">New</span>
                    )}
                  </div>
                  <h3 className="notification-title">{notification.title}</h3>
                  <p className="notification-body">{notification.body}</p>
                  <p className="notification-meta">
                    {new Date(notification.createdAt).toLocaleString()}
                    {notification.isRead
                      ? ` · read ${new Date(notification.readAt ?? notification.createdAt).toLocaleString()}`
                      : ""}
                    {notification.expiresAt && (
                      ` · expires ${new Date(notification.expiresAt).toLocaleDateString()}`
                    )}
                  </p>
                  {!notification.isRead && (
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => void handleMarkRead(notification.notificationId)}
                    >
                      Mark as read
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </section>
  );
}
