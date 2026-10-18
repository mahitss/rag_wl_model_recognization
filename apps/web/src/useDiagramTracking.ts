import { useEffect, useState, type RefObject } from "react";
import { createDiagramTracker, type DiagramTracker } from "./opencvTracker";
import {
  advanceTracking,
  homographyToCssMatrix,
  INITIAL_TRACKING_PROGRESS,
  type TrackingProgress,
  type TrackingStatus,
} from "./tracking";

type TrackingView = {
  status: TrackingStatus;
  transform: string | null;
  inliers: number;
  message: string | null;
};

const OFF_VIEW: TrackingView = { status: "off", transform: null, inliers: 0, message: null };

export function useDiagramTracking(
  videoRef: RefObject<HTMLVideoElement | null>,
  stageRef: RefObject<HTMLDivElement | null>,
  diagramUrl: string,
  enabled: boolean,
): TrackingView {
  const [view, setView] = useState<TrackingView>(OFF_VIEW);

  useEffect(() => {
    if (!enabled) {
      setView(OFF_VIEW);
      return;
    }

    let disposed = false;
    let intervalId: number | null = null;
    let tracker: DiagramTracker | null = null;
    let progress: TrackingProgress = { ...INITIAL_TRACKING_PROGRESS, status: "loading" };
    setView({ status: "loading", transform: null, inliers: 0, message: null });

    const sample = () => {
      const video = videoRef.current;
      const stage = stageRef.current;
      if (!tracker || !video || !stage || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;

      const detection = tracker.detect(video);
      const bounds = detection
        ? {
            width: stage.clientWidth,
            height: stage.clientHeight,
          }
        : null;
      const transform = detection && bounds
        ? homographyToCssMatrix(
            detection.matrix,
            detection.referenceWidth,
            detection.referenceHeight,
            detection.frameWidth,
            detection.frameHeight,
            video.videoWidth,
            video.videoHeight,
            bounds.width,
            bounds.height,
          )
        : null;
      progress = advanceTracking(progress, transform !== null);
      if (!detection || !transform || progress.status !== "tracking") {
        setView({ status: progress.status, transform: null, inliers: 0, message: null });
        return;
      }
      setView({
        status: "tracking",
        transform,
        inliers: detection.inliers,
        message: null,
      });
    };

    void createDiagramTracker(diagramUrl)
      .then((createdTracker) => {
        if (disposed) {
          createdTracker.dispose();
          return;
        }
        tracker = createdTracker;
        progress = { ...INITIAL_TRACKING_PROGRESS, status: "searching" };
        setView({ status: "searching", transform: null, inliers: 0, message: null });
        intervalId = window.setInterval(sample, 180);
      })
      .catch(() => {
        if (!disposed) {
          setView({
            status: "unavailable",
            transform: null,
            inliers: 0,
            message: "Automatic tracking is unavailable. You can switch to manual alignment.",
          });
        }
      });

    return () => {
      disposed = true;
      if (intervalId !== null) window.clearInterval(intervalId);
      tracker?.dispose();
    };
  }, [diagramUrl, enabled, stageRef, videoRef]);

  return enabled ? view : OFF_VIEW;
}
