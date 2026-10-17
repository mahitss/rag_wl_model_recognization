# Product requirements

## Product and scope

WonderLearn is intended to help a learner connect a visible educational diagram or object to explanations grounded in their permitted study materials. The camera is an input and interaction surface, not a promise of general-purpose scene understanding or 3D reconstruction.

## Personas and problems

- **Independent learner:** studies from PDFs and wants to ask what a labeled part does, find the relevant passage, and check understanding.
- **Teacher or content curator:** contributes material they are allowed to use and needs citations and document deletion.
- **Developer/ML learner:** wants reproducible pipelines, transparent model provenance, understandable evaluation, and low-cost experimentation.

Primary problem: learners must switch between a diagram, a textbook, and a search/chat interface, losing the visual context and often receiving explanations without traceable evidence.

## Core journeys

1. Learner imports a permitted, text-based PDF; sees extraction status and any failed pages.
2. Learner opens a supported diagram image or points the webcam at a supported printed diagram. In the first vertical slice, selection may use labeled component regions configured for that diagram; generic recognition is out of scope.
3. Learner clicks or points at a component, confirms the selected label, and asks a question.
4. The system retrieves passages with page and section provenance, presents a concise answer and citations, or says evidence was not found.
5. Learner requests a hint or answers an authored check question. The system compares against an explicit rubric and explains the result; no claim of broad misconception diagnosis is made.

## Functional requirements

- Import and delete user-provided text PDFs; reject unsupported, encrypted, oversized, malformed, or suspicious files with understandable errors.
- Preserve document ID, filename, page, section when extractable, and source span/locator for each passage.
- Support local keyword retrieval and optional local semantic retrieval, with visible citations and no fabricated source locations.
- Select a supported diagram region by accessible pointer/touch input; show confidence/status and allow manual fallback.
- Offer beginner and technical answer styles from retrieved evidence; mark generated examples as examples.
- Include deterministic, authored quiz prompts and rubrics in MVP; persist progress only if explicitly enabled.
- Show camera permission state, camera-off workflow, selection feedback, and a clear stop-camera control.
- Provide a local-first runtime and document deletion that removes extracted text and indexes.

## Non-functional requirements

- Strict ₹0 dependency budget; optional resources must remain optional and have local fallbacks.
- Modular monolith, typed contracts, validated inputs, reproducible model/data manifests, and logs without raw video by default.
- Graceful behavior when a model is unavailable, confidence is low, a source has no answer, or the camera is denied.
- Accessible keyboard/pointer selection, readable contrast, captions/text alternatives for visual states, and responsive layout.
- Target laptop assumption: Windows 11, 16 GB RAM, CPU-first inference; verify actual versions and hardware at implementation start.

## MVP acceptance criteria

Acceptance is behavior-based and must be verified with a recorded test set; no thresholds or results are claimed yet.

- A text PDF can be ingested, searched, cited to the correct page, and deleted.
- Given a curated supported diagram, selecting each configured component resolves the correct concept and retrieves its linked source passages.
- Unsupported diagram/object or absent evidence produces an explicit fallback rather than a confident guess.
- A learner can use the main flow without gestures or camera access.
- No image frames or answer text are retained beyond the session unless the user opts into an identified local learning history.
- Installation and run instructions work on the developer's verified machine without paid service credentials.

## Explicit non-goals for MVP

Open-world object detection, arbitrary textbook diagram parsing, segmentation, reliable monocular 3D anchoring, multi-user cloud accounts, adaptive psychometrics, autonomous online scraping, fine-tuned LLMs, and automated retraining.
