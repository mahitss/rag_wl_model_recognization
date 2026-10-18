# M3 camera preview and manual alignment prototype

The M3 frontend prototype uses the browser's `getUserMedia` API and the existing authored photosynthesis SVG. Camera permission is requested only after the learner chooses **Enable camera**. The preview is rendered in a muted, inline `<video>` element. Frames stay in the browser: this implementation has no camera upload, recording, canvas capture, or persistence path.

The camera stage keeps the diagram's 1000:640 aspect ratio. The video uses `object-fit: cover`; the overlay and selectable regions use the same responsive stage. Region rectangles remain normalized to the diagram, while alignment translation is expressed as a fraction of the stage. Scale and rotation are applied around the overlay center. The controls are available for horizontal/vertical translation, scale (0.5–1.5), and rotation (-180°–180°). The overlay remains responsive to viewport resizing. A device orientation change automatically marks alignment stale; the learner must realign and confirm again.

This milestone implements **manual alignment, not automatic visual tracking**. The learner adjusts the overlay against a supported printed diagram and confirms the alignment. Camera-region buttons remain disabled while alignment is stale. If the camera or page moves, or the page is covered, the learner must use **Mark alignment stale**, realign, and confirm again. The prototype cannot detect movement, target loss, or occlusion itself, and it makes no real-world tracking accuracy claim. The original static diagram and accessible region list remain available as fallback.

Camera access requires a secure context (HTTPS or localhost) and a browser/device with an available camera. Denied permission, unavailable APIs/devices, insecure context, and device errors produce user-facing messages. Turning the camera off or unmounting the panel stops every stream track; a pending request is invalidated and any stream that arrives afterward is immediately stopped. A visible indicator remains while an active stream is attached.

Run the browser-side tests and checks from the repository root:

```powershell
npm test --prefix apps/web
npm run typecheck --prefix apps/web
npm run build --prefix apps/web
```

The tests mock media APIs and tracks; they verify lifecycle and UI behavior without camera hardware. They do not measure overlay accuracy in a physical room.
