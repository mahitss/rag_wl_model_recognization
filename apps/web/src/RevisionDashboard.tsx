/**
 * Revision dashboard - M56 Intelligent Revision & Memory Retention Engine.
 *
 * Displays the revision queue, upcoming schedule, overdue topics, and topic
 * mastery. Integrates with the backend revision scheduler API.
 */

import { useCallback, useEffect, useState } from "react";

type RevisionItem = {
  itemId: string;
  itemType: string;
  nextDueDate: string;
  intervalDays: number;
  easeFactor: number;
  repetitionCount: number;
  lastResult: string;
  lastReviewedAt: string | null;
};

interface RevisionDashboardProps {
  conceptConcepts?: Array<{ conceptId: string; name: string }>;
  onConceptSelect?: (conceptId: string) => void;
  onAskTutor?: (conceptId: string, name: string) => void;
  onGenerateQuiz?: (topic: string) => void;
}

export default function RevisionDashboard(_props: RevisionDashboardProps) {
  const [upcomingItems, setUpcomingItems] = useState<RevisionItem[]>([]);
  const [overdueTopics, setOverdueTopics] = useState<
    Array<{ topicId: string; earliestDue: string; overdueCount: number }>
  >([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [scheduleResp, upcomingResp, overdueResp] = await Promise.all([
        fetch("/api/v1/revision/schedule?limit=20"),
        fetch("/api/v1/revision/upcoming?days_ahead=7"),
        fetch("/revision/overdue"),
      ]);

      if (scheduleResp.ok) {
        const scheduleData = await scheduleResp.json();
        const items = scheduleData.items || [];
        setUpcomingItems(
          items.filter((i: RevisionItem) => new Date(i.nextDueDate) > new Date())
        );
      }
      if (upcomingResp.ok) {
        setUpcomingItems(await upcomingResp.json());
      }
      if (overdueResp.ok) {
        setOverdueTopics(await overdueResp.json());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load revision data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchData();
  }, [fetchData]);

  const handleReview = async (itemType: string, itemId: string, result: string) => {
    try {
      const response = await fetch(
        `/api/v1/revision/review/${itemType}/${itemId}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ result, confidenceRating: 3 }),
        },
      );
      if (response.ok) {
        await fetchData();
      } else {
        setError("Failed to record review");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to record review");
    }
  };

  const handleReschedule = async (itemType: string, itemId: string, newDueDate: string) => {
    try {
      const response = await fetch(
        `/api/v1/revision/reschedule/${itemType}/${itemId}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ new_due_date: newDueDate }),
        },
      );
      if (response.ok) {
        await fetchData();
      } else {
        setError("Failed to reschedule");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reschedule");
    }
  };

  return (
    <section className="revision-dashboard" aria-labelledby="revision-dashboard-title">
      <div className="explorer-header">
        <div>
          <p className="eyebrow">Revision Dashboard</p>
          <h2 id="revision-dashboard-title">Revision Dashboard</h2>
          <p className="muted">
            Track your revision schedule, review due items, and monitor mastery across topics.
          </p>
        </div>
        <div className="explorer-controls">
          <div className="explorer-controls">
            {loading && <span className="muted">Loading…</span>}
            {error && (
              <span className="notice notice--error" role="alert">{error}</span>
            )}
          </div>
        </div>
      </div>

      <div className="explorer-body">
        <div className="revision-grid">
          {overdueTopics.length > 0 && (
            <section className="revision-section" aria-labelledby="overdue-title">
              <h3 id="overdue-title">Overdue Topics</h3>
              <p className="muted">
                These topics have overdue review items. Review them as soon as possible.
              </p>
              <ul className="topic-list">
                {overdueTopics.map((topic) => (
                  <li key={topic.topicId}>
                    <span>{topic.topicId}</span>
                    <span>{topic.overdueCount} overdue item(s)</span>
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => {
                        // Topic detail view not yet implemented
                      }}
                    >
                      Review
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {upcomingItems.length > 0 && (
            <section className="revision-section" aria-labelledby="upcoming-title">
              <h3 id="upcoming-title">Upcoming Reviews</h3>
              <p className="muted">
                Scheduled reviews for the next 7 days.
              </p>
              <ul className="revision-list">
                {upcomingItems.map((item) => (
                  <li key={`${item.itemType}-${item.itemId}`}>
                    <span>{item.itemType}: {item.itemId}</span>
                    <span>{new Date(item.nextDueDate).toLocaleDateString()}</span>
                    <span>{item.lastResult}</span>
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => void handleReview(item.itemType, item.itemId, item.lastResult)}
                    >
                      Review
                    </button>
                    <button
                      type="button"
                      className="text-action"
                      onClick={() => {
                        const newDate = new Date(
                          Date.parse(item.nextDueDate) + 24 * 60 * 60 * 1000
                        ).toISOString();
                        void handleReschedule(item.itemType, item.itemId, newDate);
                      }}
                    >
                      Postpone 1 day
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </div>
    </section>
  );
}
