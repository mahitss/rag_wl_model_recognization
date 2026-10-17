# Model strategy

## Model inventory and ownership language

“Our model” should mean the specific artifact and training recipe are documented; it does not imply ownership of upstream architecture, pretrained weights, or training corpus. Every artifact records upstream model, revision, license, dataset version, configuration, and evaluation report.

| Component | Initial choice | Training category | Later path |
|---|---|---|---|
| WL-Gesture | MediaPipe hand landmarks plus explicit deterministic pinch/point rules; add a small classifier only after collecting consented landmark sequences | Existing landmark model used without additional training; rules are deterministic | Our dataset + normalized-landmark SVM/Random Forest/MLP; temporal model only if evaluation shows sequences are needed |
| WL-Vision | Curated supported diagrams with manual/configured region selection; later compare lightweight detector implementations and licenses | No learned model initially | Existing architecture trained on own labeled data, likely transfer learning; scratch only for a small constrained dataset if justified |
| WL-Embed | Optional compact sentence embedding model, initially compare multilingual/domain fit against lexical-only baseline | Existing pretrained model used without additional training | Fine-tune only with judged query-positive/negative pairs and held-out source split |
| WL-Rank | RRF fusion baseline | Deterministic, no ML | Train a small ranker on labeled query-document judgments if sufficient data exists |
| WL-Tutor | Template/evidence response first; optional small quantized local instruct model after benchmark | Existing model used without training; future LoRA only if evaluation identifies a correctable behavior gap | Fine-tuning is not a substitute for retrieval or citation validation |

## Vision choice

The first ML vision target should be **diagram-specific component localization**, not broad object classification. A known educational diagram has a small stable label vocabulary and component regions; this directly enables the desired selection flow and makes an evaluation set feasible. Begin with curated 2D region annotations/manual mapping. Once collected, compare a compact detector such as torchvision/RT-DETR family or another permissively licensed candidate against a classical/template baseline; license the code, checkpoint, and training data separately. Avoid committing to Ultralytics YOLO unless the project deliberately accepts AGPL obligations or a paid license; its official licensing page says YOLO code, models, and derived trained models carry AGPL-3.0 by default, with Enterprise for cases not meeting those terms. [Ultralytics license](https://www.ultralytics.com/license)

Classification says which diagram/object is present but does not locate a component. Segmentation is unnecessary for first use if selectable regions can be boxes/polygons. Use segmentation only where overlapping or irregular shapes make boxes inadequate and labeled masks are available.

## Gesture pipeline

Extract 21 hand landmarks in browser; normalize by wrist origin and palm scale, retain handedness as metadata but train/test both hands. For static gestures use rules as a transparent baseline, then compare a small MLP and classical SVM/Random Forest over normalized coordinates and pairwise distances. For swipe/rotation, collect timestamped sequences; only then compare a temporal CNN or small GRU/LSTM. A Transformer is unjustified for small local datasets. Add confidence threshold, neutral-state requirement, hysteresis, cooldown, and explicit confirmation for destructive or consequential actions. Test one/two hands and varied lighting, distance, skin tones, handedness, and camera angle.

## Hardware and inference

Assume CPU-first 16 GB RAM. Run MediaPipe in browser; throttle frames and move synchronous processing to a worker if responsiveness requires it. Keep vision models small and benchmark actual webcam resolution/CPU. Compact embeddings can be batched during ingestion and cached. Local quantized 1B–3B-class text models may be experimentally attempted with llama.cpp, but model file, KV cache, context, CPU generation speed, and license all matter; 16 GB does not guarantee an acceptable tutor. Start with retrieval plus templates and have a no-LLM path. Llama weights are governed by Meta's model-specific Community License, not simply MIT/open source; review each candidate's exact terms. [Llama 3.2 license](https://dev.meta.ai/llama/llama3_2/license), [llama.cpp](https://github.com/ggml-org/llama.cpp)

## Training and promotion

Train small models in free notebook sessions when available; save resumable checkpoints frequently and export artifacts to user-controlled storage. Colab explicitly says free resources are not guaranteed or unlimited, so CPU/local training and smaller experiments are fallback. [Colab FAQ](https://research.google.com/colaboratory/faq.html)

Training lifecycle: immutable dataset manifest → group-aware splits → baseline → candidate training → evaluation on frozen test set → error review → candidate report → explicit promotion decision → previous artifact retained for rollback. No automatic promotion or retraining in MVP. See data strategy and evaluation plan.
