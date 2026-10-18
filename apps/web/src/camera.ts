import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

export type CameraStatus =
  | { kind: "off" }
  | { kind: "requesting" }
  | { kind: "active" }
  | { kind: "error"; message: string };

function stopTracks(stream: MediaStream | null) {
  stream?.getTracks().forEach((track) => track.stop());
}

export function cameraErrorMessage(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError") {
    return "Camera permission was denied. Allow camera access in your browser settings and try again.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No camera is available on this device.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "The camera could not start. It may be in use by another application.";
  }
  if (name === "OverconstrainedError") {
    return "No available camera meets the requested video settings.";
  }
  if (name === "SecurityError") {
    return "Camera access is blocked by this page's security settings.";
  }
  return "The camera could not be started. Check the device and browser permissions.";
}

export function useCamera(videoRef: RefObject<HTMLVideoElement | null>) {
  const [status, setStatus] = useState<CameraStatus>({ kind: "off" });
  const [stream, setStream] = useState<MediaStream | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const mountedRef = useRef(false);
  const requestIdRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestIdRef.current += 1;
      const current = streamRef.current;
      streamRef.current = null;
      stopTracks(current);
    };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = stream;
    if (stream) void video.play().catch(() => undefined);
    return () => {
      if (video.srcObject === stream) video.srcObject = null;
    };
  }, [stream, videoRef]);

  useEffect(() => {
    if (!stream) return;
    const onEnded = () => {
      if (streamRef.current !== stream) return;
      streamRef.current = null;
      stopTracks(stream);
      setStream(null);
      setStatus({ kind: "error", message: "The camera stopped. Re-enable it to continue." });
    };
    const tracks = stream.getTracks();
    tracks.forEach((track) => track.addEventListener("ended", onEnded));
    return () => tracks.forEach((track) => track.removeEventListener("ended", onEnded));
  }, [stream]);

  const stopCamera = useCallback(() => {
    requestIdRef.current += 1;
    const current = streamRef.current;
    streamRef.current = null;
    stopTracks(current);
    setStream(null);
    setStatus({ kind: "off" });
  }, []);

  const startCamera = useCallback(async () => {
    if (typeof window !== "undefined" && window.isSecureContext === false) {
      setStatus({
        kind: "error",
        message: "Camera access requires a secure page (HTTPS or localhost).",
      });
      return;
    }
    const getUserMedia = navigator.mediaDevices?.getUserMedia;
    if (!getUserMedia) {
      setStatus({ kind: "error", message: "Camera access is unavailable in this browser." });
      return;
    }

    const requestId = ++requestIdRef.current;
    setStatus({ kind: "requesting" });
    try {
      const nextStream = await getUserMedia.call(navigator.mediaDevices, {
        audio: false,
        video: { facingMode: { ideal: "environment" } },
      });
      if (!mountedRef.current || requestId !== requestIdRef.current) {
        stopTracks(nextStream);
        return;
      }
      const previous = streamRef.current;
      streamRef.current = nextStream;
      stopTracks(previous);
      setStream(nextStream);
      setStatus({ kind: "active" });
    } catch (error) {
      if (mountedRef.current && requestId === requestIdRef.current) {
        setStatus({ kind: "error", message: cameraErrorMessage(error) });
      }
    }
  }, []);

  return { status, stream, startCamera, stopCamera };
}
