/**
 * Classroom announcement feed - M72 Classroom Communication & Learning Progress Portal.
 *
 * Displays announcements for a classroom session. Educators can create new
 * announcements. All access is server-side authorized.
 */

import { useCallback, useEffect, useState } from "react";

type ClassroomAnnouncement = {
  announcementId: string;
  sessionId: string;
  educatorId: string;
  message: string;
  createdAt: string;
  updatedAt: string;
};

interface ClassroomAnnouncementFeedProps {
  sessionId: string;
  sessionTitle: string;
  currentUserId: string;
  currentUserRole: string;
}

export default function ClassroomAnnouncementFeed({
  sessionId,
  sessionTitle,
  currentUserId,
  currentUserRole,
}: ClassroomAnnouncementFeedProps) {
  const [announcements, setAnnouncements] = useState<ClassroomAnnouncement[]>([]);
  const [composeText, setComposeText] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const fetchAnnouncements = useCallback(async () => {
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(sessionId)}/announcements`
      );
      if (!response.ok) {
        setError("Could not load announcements.");
        return;
      }
      const data = (await response.json()) as { announcements: ClassroomAnnouncement[] };
      setAnnouncements(data.announcements ?? []);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not load announcements.");
    }
  }, [sessionId]);

  useEffect(() => {
    void fetchAnnouncements();
  }, [sessionId, fetchAnnouncements]);

  const handleCreate = async (event: React.FormEvent) => {
    event.preventDefault();
    const text = composeText.trim();
    if (!text) return;
    setCreating(true);
    setError(null);
    try {
      const response = await fetch(
        `/api/v1/classrooms/${encodeURIComponent(sessionId)}/announcements`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            sessionId,
            senderId: currentUserId,
            content: text,
            messageType: "classroom",
          }),
        },
      );
      if (!response.ok) {
        const body = (await response.json()) as { error?: { message?: string } };
        throw new Error(
          body.error?.message ?? "Could not post announcement.",
        );
      }
      setComposeText("");
      setStatusMessage("Announcement posted.");
      setTimeout(() => setStatusMessage(null), 3000);
      void fetchAnnouncements();
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not post announcement.");
    } finally {
      setCreating(false);
    }
  };

  const isEducator = currentUserRole === "educator";

  return (
    <section className="classroom-announcement-feed" aria-labelledby="classroom-announcement-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Classroom announcements</p>
          <h2 id="classroom-announcement-title">{sessionTitle}</h2>
          <p className="muted">
            Official announcements from the classroom educator.
            {announcements.length === 0 ? "No announcements yet." : `${announcements.length} announcement(s).`}
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
        <div className="announcement-list">
          {announcements.length === 0 ? (
            <p className="muted">No announcements yet.</p>
          ) : (
            <ul className="announcement-thread" role="list" aria-label="Classroom announcements">
              {announcements.map((announcement) => (
                <li key={announcement.announcementId} className="announcement-item">
                  <div className="announcement-header">
                    <span className="announcement-author">
                      {announcement.educatorId === currentUserId ? "You" : announcement.educatorId.slice(0, 12)}
                    </span>
                    <span className="announcement-meta">
                      {new Date(announcement.createdAt).toLocaleString()}
                    </span>
                  </div>
                  <p className="announcement-message">{announcement.message}</p>
                </li>
              ))}
            </ul>
          )}
        </div>

        {isEducator && (
          <div className="announcement-composer">
            <form onSubmit={handleCreate} className="composer-form">
              <label htmlFor="announcement-message" className="sr-only">
                Announcement
              </label>
              <textarea
                id="announcement-message"
                value={composeText}
                onChange={(event) => setComposeText(event.currentTarget.value)}
                placeholder="Write an announcement for the classroom…"
                rows={2}
                maxLength={2000}
                disabled={creating}
              />
              <button
                type="submit"
                className="camera-action camera-action--primary"
                disabled={creating || !composeText.trim()}
              >
                {creating ? "Posting…" : "Post announcement"}
              </button>
            </form>
          </div>
        )}
      </div>
    </section>
  );
}
