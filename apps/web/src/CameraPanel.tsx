import { useEffect, useRef, useState, type CSSProperties } from "react";
import { useCamera } from "./camera";
import { alignmentTransform, normalizedRegionStyle } from "./geometry";

type CameraDiagram = {
  imageUrl: string;
  imageAspectRatio: number;
  regions: Array<{
    regionId: string;
    displayName: string;
    geometry: { x: number; y: number; width: number; height: number };
  }>;
};

type CameraPanelProps = {
  diagram: CameraDiagram;
  onSelectRegion: (regionId: string) => void;
};

const INITIAL_ALIGNMENT = { x: 0, y: 0, scale: 1, rotation: 0 };

export function CameraPanel({ diagram, onSelectRegion }: CameraPanelProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const { status, startCamera, stopCamera } = useCamera(videoRef);
  const [alignment, setAlignment] = useState(INITIAL_ALIGNMENT);
  const [alignmentConfirmed, setAlignmentConfirmed] = useState(false);

  function adjustAlignment(key: keyof typeof INITIAL_ALIGNMENT, value: number) {
    setAlignment((current) => ({ ...current, [key]: value }));
    setAlignmentConfirmed(false);
  }

  const cameraActive = status.kind === "active";
  const overlayStyle = {
    transform: alignmentTransform(alignment),
    aspectRatio: diagram.imageAspectRatio,
  } as CSSProperties;

  useEffect(() => {
    if (!cameraActive) setAlignmentConfirmed(false);
  }, [cameraActive]);

  useEffect(() => {
    if (!cameraActive) return;
    const markStale = () => setAlignmentConfirmed(false);
    window.addEventListener("orientationchange", markStale);
    screen.orientation?.addEventListener("change", markStale);
    return () => {
      window.removeEventListener("orientationchange", markStale);
      screen.orientation?.removeEventListener("change", markStale);
    };
  }, [cameraActive]);

  return (
    <section className="camera-panel" aria-labelledby="camera-title">
      <div className="camera-heading">
        <div>
          <p className="eyebrow">Optional local preview</p>
          <h3 id="camera-title">Camera alignment prototype</h3>
        </div>
        {cameraActive && (
          <span className="privacy-indicator">
            <span aria-hidden="true" /> Camera on · stays on this device
          </span>
        )}
      </div>
      <p className="camera-explanation">
        Camera access starts only when you press the button. Video is shown locally and is never
        uploaded, recorded, or saved. Align the overlay manually with the printed diagram.
      </p>

      {status.kind !== "active" && (
        <button
          className="camera-action"
          type="button"
          onClick={() =>
            status.kind === "requesting" ? stopCamera() : void startCamera()
          }
        >
          {status.kind === "requesting" ? "Cancel camera request" : "Enable camera"}
        </button>
      )}
      {cameraActive && (
        <button className="camera-action" type="button" onClick={stopCamera}>
          Turn camera off
        </button>
      )}
      {status.kind === "requesting" && <p role="status">Waiting for camera permission…</p>}
      {status.kind === "error" && (
        <p className="notice notice--error" role="alert">
          {status.message}
        </p>
      )}

      {cameraActive && (
        <>
          <div className="camera-stage-wrap">
            <div
              className="camera-stage"
              style={{ "--camera-ratio": diagram.imageAspectRatio } as CSSProperties}
              aria-label="Camera preview with manually aligned diagram overlay"
            >
              <video
                ref={videoRef}
                className="camera-video"
                autoPlay
                playsInline
                muted
                aria-label="Local camera preview"
              />
              <div className="camera-overlay" style={overlayStyle}>
                <img src={diagram.imageUrl} alt="" draggable={false} />
                {diagram.regions.map((region) => (
                  <button
                    key={region.regionId}
                    className="camera-region"
                    type="button"
                    style={normalizedRegionStyle(region.geometry)}
                    aria-label={`Explore ${region.displayName} in camera overlay`}
                    disabled={!alignmentConfirmed}
                    onClick={() => onSelectRegion(region.regionId)}
                  >
                    <span className="sr-only">{region.displayName}</span>
                  </button>
                ))}
              </div>
              <span className="camera-live-label">LOCAL PREVIEW</span>
            </div>
          </div>

          <fieldset className="alignment-controls">
            <legend>Manual overlay alignment</legend>
            <p className="alignment-help">
              Adjust normalized offsets, scale, and rotation. If the page moves or is covered, mark
              the alignment stale and realign it.
            </p>
            <AlignmentSlider
              label="Horizontal offset"
              min={-0.5}
              max={0.5}
              step={0.01}
              value={alignment.x}
              onChange={(value) => adjustAlignment("x", value)}
              output={`${Math.round(alignment.x * 100)}%`}
            />
            <AlignmentSlider
              label="Vertical offset"
              min={-0.5}
              max={0.5}
              step={0.01}
              value={alignment.y}
              onChange={(value) => adjustAlignment("y", value)}
              output={`${Math.round(alignment.y * 100)}%`}
            />
            <AlignmentSlider
              label="Scale"
              min={0.5}
              max={1.5}
              step={0.01}
              value={alignment.scale}
              onChange={(value) => adjustAlignment("scale", value)}
              output={`${alignment.scale.toFixed(2)}×`}
            />
            <AlignmentSlider
              label="Rotation"
              min={-180}
              max={180}
              step={1}
              value={alignment.rotation}
              onChange={(value) => adjustAlignment("rotation", value)}
              output={`${alignment.rotation}°`}
            />
          </fieldset>

          <div className="alignment-actions">
            {!alignmentConfirmed ? (
              <button
                className="camera-action camera-action--primary"
                type="button"
                onClick={() => setAlignmentConfirmed(true)}
              >
                Confirm manual alignment
              </button>
            ) : (
              <button
                className="camera-action"
                type="button"
                onClick={() => setAlignmentConfirmed(false)}
              >
                Mark alignment stale
              </button>
            )}
            <button
              className="camera-action camera-action--quiet"
              type="button"
              onClick={() => {
                setAlignment(INITIAL_ALIGNMENT);
                setAlignmentConfirmed(false);
              }}
            >
              Reset alignment
            </button>
          </div>
          <p className="alignment-state" role="status" aria-live="polite">
            {alignmentConfirmed
              ? "Manual alignment confirmed. If the camera or page moves, mark this alignment stale."
              : "Alignment is stale. Region selection is paused until you confirm the manual alignment."}
          </p>
        </>
      )}
    </section>
  );
}

type AlignmentSliderProps = {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  output: string;
  onChange: (value: number) => void;
};

function AlignmentSlider({
  label,
  min,
  max,
  step,
  value,
  output,
  onChange,
}: AlignmentSliderProps) {
  const inputId = `alignment-${label.toLowerCase().replaceAll(" ", "-")}`;
  return (
    <label className="alignment-slider" htmlFor={inputId}>
      <span>{label}</span>
      <input
        id={inputId}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.currentTarget.value))}
      />
      <output>{output}</output>
    </label>
  );
}
