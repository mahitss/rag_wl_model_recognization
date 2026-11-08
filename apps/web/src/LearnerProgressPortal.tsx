/**
 * Learner progress portal - M72 Classroom Communication & Learning Progress Portal.
 *
 * Displays the current user's observed learning progress: assessment results
 * by topic, revision activity, study sessions, unread message count, and
 * unread notification count. All data comes from persisted records only -
 * no estimates or fabrications. Educators can view a learner's portal when
 * they share a classroom session.
 */

import { useCallback, useEffect, useState } from "react";

type AssessmentSummary = {
  topicId: string;
  topic: string;
  totalAttempts: number;
  correctAttempts: number;
  accuracy: number | null;
  lastUpdated: string | null;
};

type RevisionSummary = {
  topicId: string;
  topic: string;
  lastOutcome: string;
  attemptedAt: string | null;
  dueAt: string | null;
};

type StudySessionSummary = {
  sessionId: string;
  topicId: string;
  topic: string;
  startedAt: string;
  completedAt: string | null;
};

interface LearnerProgressPortalProps {
  currentUserId: string;
  viewerRole?: "learner" | "educator";
  targetUserId?: string; // educator viewing a learner's portal
}

export default function LearnerProgressPortal({
  currentUserId,
  targetUserId,
}: LearnerProgressPortalProps) {
  const fetchUserId = targetUserId ?? currentUserId;
  const [portal, setPortal] = useState<{
    userId: string;
    assignments: Array<{ assignmentId: string; title: string; status: string }>;
    assessments: AssessmentSummary[];
    revisions: RevisionSummary[];
    studySessions: StudySessionSummary[];
    messageCount: number;
    unreadNotificationCount: number;
    dataSources: string[];
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [isOwnView, setIsOwnView] = useState(true);

  const fetchProgress = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const path = targetUserId
        ? `/api/v1/progress/${encodeURIComponent(fetchUserId)}`
        : "/api/v1/progress";
      const response = await fetch(path);
      if (!response.ok) {
        if (response.status === 403) {
          setError("You are not authorized to view this learner's progress.");
          return;
        }
        setError("Could not load progress data.");
        return;
      }
      const data = await response.json();
      setPortal({
        userId: data.userId,
        assignments: data.assignments ?? [],
        assessments: data.assessments ?? [],
        revisions: data.revisions ?? [],
        studySessions: data.studySessions ?? [],
        messageCount: data.messageCount ?? 0,
        unreadNotificationCount: data.unreadNotificationCount ?? 0,
        dataSources: data.dataSources ?? [],
      });
      setIsOwnView(fetchUserId === currentUserId);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Could not load progress data.");
    } finally {
      setLoading(false);
    }
  }, [currentUserId, fetchUserId, targetUserId]);

  useEffect(() => {
    void fetchProgress();
  }, [fetchProgress]);

  const accuracyColor = (value: number | null) => {
    if (value === null) return "#466259";
    if (value >= 0.8) return "#1f6a3d";
    if (value >= 0.6) return "#205e49";
    if (value >= 0.4) return "#805515";
    return "#9d3b31";
  };

  return (
    <section className="learner-progress-portal" aria-labelledby="learner-progress-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Learning progress</p>
          <h2 id="learner-progress-title">Learner progress portal</h2>
          <p className="muted">
            Observed progress from quiz attempts, revision activity, and
            study sessions. No estimates or projections.
            {isOwnView ? "Viewing your own progress." : `Viewing progress for ${fetchUserId.slice(0, 12)}.`}
          </p>
        </div>
        <div className="explorer-controls">
          {loading && <span className="muted">Loading…</span>}
          {error && (
            <span className="notice notice--error" role="alert">{error}</span>
          )}
        </div>
      </div>

      {portal && (
        <div className="explorer-body">
          <div className="progress-summary-grid">
            <div className="summary-card">
              <h3>Assessments</h3>
              <p className="muted">{portal.assessments.length} topic(s) with attempt data</p>
              <ul className="assessment-list" role="list" aria-label="Assessment results">
                {portal.assessments.length === 0 ? (
                  <li className="muted">No assessment data yet.</li>
                ) : (
                  portal.assessments.map((a) => (
                    <li key={a.topicId} className="assessment-item">
                      <div className="assessment-header">
                        <span className="assessment-topic">{a.topic}</span>
                        <span
                          className="assessment-accuracy"
                          style={{ color: accuracyColor(a.accuracy) }}
                        >
                          {a.accuracy === null
                            ? "No scored attempts"
                            : `${(a.accuracy * 100).toFixed(0)}% accuracy`}
                        </span>
                      </div>
                      <p className="assessment-meta">
                        {a.correctAttempts} correct of {a.totalAttempts} attempt(s)
                        {a.lastUpdated ? ` · ${new Date(a.lastUpdated).toLocaleDateString()}` : ""}
                      </p>
                    </li>
                  ))
                )}
              </ul>
            </div>

            <div className="summary-card">
              <h3>Revision activity</h3>
              <p className="muted">{portal.revisions.length} review item(s)</p>
              <ul className="revision-list" role="list" aria-label="Revision activity">
                {portal.revisions.length === 0 ? (
                  <li className="muted">No revision data yet.</li>
                ) : (
                  portal.revisions.map((r) => (
                    <li key={`${r.topicId}-${r.attemptedAt}`} className="revision-item">
                      <div className="revision-header">
                        <span className="revision-topic">{r.topic}</span>
                        <span className={`revision-outcome revision-outcome--${r.lastOutcome}`}>
                          {r.lastOutcome}
                        </span>
                      </div>
                      <p className="revision-meta">
                        {r.attemptedAt ? new Date(r.attemptedAt).toLocaleDateString() : "Not attempted"}
                        {r.dueAt ? ` · due ${new Date(r.dueAt).toLocaleDateString()}` : ""}
                      </p>
                    </li>
                  ))
                )}
              </ul>
            </div>
          </div>

          <div className="summary-card full-width">
            <h3>Study sessions</h3>
            <p className="muted">{portal.studySessions.length} session(s)</p>
            <ul className="study-session-list" role="list" aria-label="Study sessions">
              {portal.studySessions.length === 0 ? (
                <li className="muted">No study sessions yet.</li>
              ) : (
                portal.studySessions.map((s) => (
                  <li key={s.sessionId} className="study-session-item">
                    <div className="study-session-header">
                      <span className="study-session-topic">{s.topic}</span>
                      <span className={`study-session-status ${s.completedAt ? "study-session-status--completed" : "study-session-status--active"}`}>
                        {s.completedAt ? "Completed" : "In progress"}
                      </span>
                    </div>
                    <p className="study-session-meta">
                      Started {new Date(s.startedAt).toLocaleDateString()}
                      {s.completedAt ? ` · completed ${new Date(s.completedAt).toLocaleDateString()}` : ""}
                    </p>
                  </li>
                ))
              )}
            </ul>
          </div>

          <div className="summary-card">
            <h3>Messages & notifications</h3>
            <ul className="message-notification-list">
              <li className="message-notification-item">
                <span className="message-notification-label">Unread messages</span>
                <span className="message-notification-value">{portal.messageCount}</span>
              </li>
              <li className="message-notification-item">
                <span className="message-notification-label">Unread notifications</span>
                <span className="message-notification-value">{portal.unreadNotificationCount}</span>
              </li>
            </ul>
          </div>

          <div className="summary-card full-width">
            <h3>Data sources</h3>
            <p className="muted">
              All figures are observed from persisted records:
              {portal.dataSources.join(", ")}.
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
