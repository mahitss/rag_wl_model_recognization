import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CameraPanel } from "./CameraPanel";
import { createDiagramTracker } from "./opencvTracker";
import { useDiagramTracking } from "./useDiagramTracking";

vi.mock("./opencvTracker", () => ({
  createDiagramTracker: vi.fn().mockResolvedValue({ detect: () => null, dispose: vi.fn() }),
}));

const diagram = {
  imageUrl: "/diagrams/photosynthesis.svg",
  imageAspectRatio: 1000 / 640,
  regions: [
    {
      regionId: "sunlight",
      displayName: "Light energy",
      geometry: { x: 0.02, y: 0.08, width: 0.25, height: 0.28 },
    },
    {
      regionId: "chlorophyll",
      displayName: "Chlorophyll in leaves",
      geometry: { x: 0.36, y: 0.25, width: 0.28, height: 0.5 },
    },
  ],
};

function makeStream() {
  const track = {
    stop: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  };
  const stream = { getTracks: () => [track] } as unknown as MediaStream;
  return { stream, track };
}

function setMediaDevices(getUserMedia?: ReturnType<typeof vi.fn>) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: getUserMedia ? { getUserMedia } : undefined,
  });
}

describe("camera alignment panel", () => {
  beforeEach(() => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
    const getUserMedia = vi.fn();
    setMediaDevices(getUserMedia);
    vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue();
  });

  afterEach(() => {
    vi.useRealTimers();
    cleanup();
    vi.restoreAllMocks();
  });

  it("requests permission only after an explicit user action and stops tracks when disabled", async () => {
    const user = userEvent.setup();
    const { stream, track } = makeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    setMediaDevices(getUserMedia);
    render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);

    expect(getUserMedia).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect((await screen.findByText(/Camera on · stays on this device/)).textContent).toContain(
      "Camera on",
    );
    expect(getUserMedia).toHaveBeenCalledWith({
      audio: false,
      video: { facingMode: { ideal: "environment" } },
    });

    await user.click(screen.getByRole("button", { name: "Turn camera off" }));
    expect(track.stop).toHaveBeenCalledTimes(1);
    expect(screen.queryByLabelText("Local camera preview")).toBeNull();
  });

  it("reports denied permission, unavailable cameras/APIs, and insecure contexts safely", async () => {
    const user = userEvent.setup();
    const denied = Object.assign(new Error("denied"), { name: "NotAllowedError" });
    const getUserMedia = vi.fn().mockRejectedValue(denied);
    setMediaDevices(getUserMedia);
    const deniedView = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/permission was denied/i);
    deniedView.unmount();

    const noCamera = Object.assign(new Error("missing"), { name: "NotFoundError" });
    setMediaDevices(vi.fn().mockRejectedValue(noCamera));
    const noCameraView = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/No camera is available/i);
    noCameraView.unmount();

    const busyCamera = Object.assign(new Error("busy"), { name: "NotReadableError" });
    setMediaDevices(vi.fn().mockRejectedValue(busyCamera));
    const busyView = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect((await screen.findByRole("alert")).textContent).toMatch(/may be in use/i);
    busyView.unmount();

    setMediaDevices(undefined);
    const unavailableView = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect(screen.getByRole("alert").textContent).toMatch(/unavailable in this browser/i);
    unavailableView.unmount();

    Object.defineProperty(window, "isSecureContext", { configurable: true, value: false });
    const insecureView = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    expect(screen.getByRole("alert").textContent).toMatch(/secure page/i);
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    insecureView.unmount();
  });

  it("stops camera tracks when the panel unmounts", async () => {
    const user = userEvent.setup();
    const { stream, track } = makeStream();
    const getUserMedia = vi.fn().mockResolvedValue(stream);
    setMediaDevices(getUserMedia);
    const view = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Camera on · stays on this device/);

    view.unmount();
    expect(track.stop).toHaveBeenCalledTimes(1);
  });

  it("requires a fresh manual confirmation after a camera restart", async () => {
    const user = userEvent.setup();
    const first = makeStream();
    const second = makeStream();
    setMediaDevices(vi.fn().mockResolvedValueOnce(first.stream).mockResolvedValueOnce(second.stream));
    render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Camera on · stays on this device/);
    await user.click(screen.getByRole("button", { name: "Use manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Confirm manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Turn camera off" }));
    expect(first.track.stop).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Camera on · stays on this device/);
    expect(screen.getByRole("button", { name: "Use manual alignment" })).not.toBeNull();
    expect(
      (screen.getByRole("button", { name: "Explore Light energy in camera overlay" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
  });

  it("stops a pending permission stream if the panel unmounts before permission resolves", async () => {
    const user = userEvent.setup();
    let resolveStream: (stream: MediaStream) => void = () => undefined;
    const getUserMedia = vi.fn(
      () =>
        new Promise<MediaStream>((resolve) => {
          resolveStream = resolve;
        }),
    );
    setMediaDevices(getUserMedia);
    const view = render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    view.unmount();
    const { stream, track } = makeStream();
    resolveStream(stream);
    await waitFor(() => expect(track.stop).toHaveBeenCalledTimes(1));
  });

  it("stops a late stream when the user cancels a pending permission request", async () => {
    const user = userEvent.setup();
    let resolveStream: (stream: MediaStream) => void = () => undefined;
    const getUserMedia = vi.fn(
      () =>
        new Promise<MediaStream>((resolve) => {
          resolveStream = resolve;
        }),
    );
    setMediaDevices(getUserMedia);
    render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await user.click(screen.getByRole("button", { name: "Cancel camera request" }));
    const { stream, track } = makeStream();
    resolveStream(stream);
    await waitFor(() => expect(track.stop).toHaveBeenCalledTimes(1));
  });

  it("keeps overlay regions disabled while stale, then supports pointer and keyboard selection", async () => {
    const user = userEvent.setup();
    const { stream } = makeStream();
    setMediaDevices(vi.fn().mockResolvedValue(stream));
    const onSelectRegion = vi.fn();
    render(<CameraPanel diagram={diagram} onSelectRegion={onSelectRegion} />);

    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    const sunlight = await screen.findByRole("button", { name: "Explore Light energy in camera overlay" });
    expect((sunlight as HTMLButtonElement).disabled).toBe(true);
    await user.click(screen.getByRole("button", { name: "Use manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Confirm manual alignment" }));
    expect((sunlight as HTMLButtonElement).disabled).toBe(false);

    await user.click(sunlight);
    expect(onSelectRegion).toHaveBeenCalledWith("sunlight");
    const chlorophyll = screen.getByRole("button", {
      name: "Explore Chlorophyll in leaves in camera overlay",
    });
    chlorophyll.focus();
    await user.keyboard("{Enter}");
    expect(onSelectRegion).toHaveBeenLastCalledWith("chlorophyll");

    act(() => window.dispatchEvent(new Event("orientationchange")));
    expect((sunlight as HTMLButtonElement).disabled).toBe(true);

    await user.click(screen.getByRole("button", { name: "Confirm manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Mark alignment stale" }));
    expect(screen.getByRole("button", { name: "Confirm manual alignment" })).not.toBeNull();
  });

  it("marks an alignment stale whenever a manual transform changes", async () => {
    const user = userEvent.setup();
    const { stream } = makeStream();
    setMediaDevices(vi.fn().mockResolvedValue(stream));
    render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Camera on · stays on this device/);
    await user.click(screen.getByRole("button", { name: "Use manual alignment" }));
    await user.click(screen.getByRole("button", { name: "Confirm manual alignment" }));

    fireEvent.change(screen.getByRole("slider", { name: /Horizontal offset/ }), {
      target: { value: "0.12" },
    });
    expect(screen.getByRole("button", { name: "Confirm manual alignment" })).not.toBeNull();
    expect(screen.getByText(/Manual alignment active/)).not.toBeNull();
  });

  it("ignores queued tracking callbacks after unmounting", async () => {
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    let intervalCallback: (() => void) | undefined;
    vi.spyOn(window, "setInterval").mockImplementation((handler: TimerHandler) => {
      intervalCallback = handler as () => void;
      return 1 as unknown as number;
    });

    const tracker = {
      detect: vi.fn(() => ({
        matrix: [0.5, 0, 0, 0, 0.5, 0, 0, 0, 1],
        inliers: 18,
        referenceWidth: 1000,
        referenceHeight: 640,
        frameWidth: 500,
        frameHeight: 320,
      })),
      dispose: vi.fn(),
    };
    vi.mocked(createDiagramTracker).mockResolvedValue(tracker as never);

    const videoRef = { current: { readyState: HTMLMediaElement.HAVE_CURRENT_DATA, videoWidth: 500, videoHeight: 320 } as HTMLVideoElement };
    const stageRef = { current: { clientWidth: 500, clientHeight: 320 } as HTMLDivElement };

    function HookHarness() {
      useDiagramTracking(videoRef, stageRef, diagram.imageUrl, true);
      return <div>tracking</div>;
    }

    const { unmount } = render(<HookHarness />);
    await waitFor(() => expect(createDiagramTracker).toHaveBeenCalledWith(diagram.imageUrl));
    unmount();

    act(() => {
      if (intervalCallback) intervalCallback();
    });

    expect(errorSpy).not.toHaveBeenCalledWith(
      expect.stringContaining("state update on an unmounted component"),
    );
    expect(tracker.dispose).toHaveBeenCalledTimes(1);
  });

  it("enables regions after detection, pauses on loss, and recovers after reacquisition", async () => {
    const user = userEvent.setup();
    const { stream } = makeStream();
    setMediaDevices(vi.fn().mockResolvedValue(stream));
    const sample = {
      matrix: [0.5, 0, 0, 0, 0.5, 0, 0, 0, 1],
      inliers: 18,
      referenceWidth: 1000,
      referenceHeight: 640,
      frameWidth: 500,
      frameHeight: 320,
    };
    const detections = [sample, sample, null, null, null, sample, sample];
    vi.mocked(createDiagramTracker).mockResolvedValue({
      detect: vi.fn(() => detections.shift() ?? null),
      dispose: vi.fn(),
    });
    render(<CameraPanel diagram={diagram} onSelectRegion={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Enable camera" }));
    await screen.findByText(/Searching locally for the full photosynthesis diagram/);

    const video = screen.getByLabelText("Local camera preview");
    const stage = video.parentElement;
    Object.defineProperties(video, {
      readyState: { configurable: true, value: HTMLMediaElement.HAVE_CURRENT_DATA },
      videoWidth: { configurable: true, value: 500 },
      videoHeight: { configurable: true, value: 320 },
    });
    Object.defineProperties(stage, {
      clientWidth: { configurable: true, value: 500 },
      clientHeight: { configurable: true, value: 320 },
    });
    const region = screen.getByRole("button", { name: "Explore Light energy in camera overlay" });
    await waitFor(() => expect((region as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText(/Diagram tracked locally/)).not.toBeNull();

    await waitFor(() => {
      expect((region as HTMLButtonElement).disabled).toBe(true);
      expect(screen.getByText(/Tracking lost/)).not.toBeNull();
    });

    await waitFor(() => expect((region as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByText(/Diagram tracked locally/)).not.toBeNull();
  });
});
