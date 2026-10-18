# M3 camera preview and M4 automatic diagram tracking prototype

The camera preview uses the browser's `getUserMedia` API and the existing authored photosynthesis SVG. Permission is requested only after the learner chooses **Enable camera**. The preview is rendered in a muted, inline `<video>` element. The M4 tracker samples the live video into a reusable in-memory canvas at up to 640 pixels wide, about 5.5 times per second. Frames are processed locally and discarded as the canvas is reused. They are not uploaded, recorded, or persisted.

The camera stage keeps the diagram's 1000:640 aspect ratio. The video uses `object-fit: cover`; the overlay and selectable regions use the same responsive stage. Region rectangles remain normalized to the diagram, while alignment translation is expressed as a fraction of the stage. Scale and rotation are applied around the overlay center. The controls are available for horizontal/vertical translation, scale (0.5–1.5), and rotation (-180°–180°). The overlay remains responsive to viewport resizing. A device orientation change automatically marks alignment stale; the learner must realign and confirm again.

M3 provides manual alignment as a fallback. M4 optionally loads the pinned Apache-2.0 OpenCV.js 5.0.0-release.1 package after camera permission is granted. It describes the authored SVG and each camera sample with ORB features, matches binary descriptors with Hamming distance and a ratio filter, and estimates a planar homography with RANSAC. The homography is mapped through the camera's `object-fit: cover` crop to a CSS projective transform, so selectable regions move with the detected diagram. Tracking runs every 180 ms while the camera is active. Two consecutive detections are required to enable region selection. A missed detection immediately pauses selection; three consecutive misses show a tracking-lost state. Two new detections reacquire the diagram. The learner can switch to the retained manual translation/scale/rotation controls at any time.

This is a classical single-reference proof of concept, not robust AR. It expects the printed page to show the authored graphic at a reasonably similar scale and appearance. Glare, blur, low light, occlusion, small targets, page curvature, or visually similar content can prevent matching or produce a bad estimate. The minimum match counts, ratio, and inlier thresholds are engineering heuristics, not calibrated confidence. No real-camera recording or validation set was available for this task, so alignment accuracy and performance on physical pages are **not measured**. Simulated tests cover homography geometry, tracking-loss gating, and recovery; they do not establish real-world accuracy. The static diagram and accessible region list remain available as fallback.

Camera access requires a secure context (HTTPS or localhost) and a browser/device with an available camera. Denied permission, unavailable APIs/devices, insecure context, and device errors produce user-facing messages. Turning the camera off or unmounting the panel stops every stream track; a pending request is invalidated and any stream that arrives afterward is immediately stopped. A visible indicator remains while an active stream is attached.

The OpenCV.js runtime is code-split and loaded only when automatic tracking begins. Its production chunk is about 15.6 MB uncompressed (4.0 MB gzip) in this build; this adds startup delay on slower devices. The tracker runs on the UI thread at a capped sampling rate and may cause visible stutter on low-power hardware. Selecting **Use manual alignment** stops the tracking loop and releases its OpenCV objects.

Run the browser-side tests and checks from the repository root:

```powershell
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

The tests mock media APIs and tracks; they verify lifecycle and UI behavior without camera hardware. They do not measure overlay accuracy in a physical room.
