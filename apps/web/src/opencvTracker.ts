import {
  areCorrespondencesValid,
  hasReliableInlierSupport,
  isPlausibleDetection,
} from "./tracking";

type CvObject = { delete: () => void; [key: string]: any };
type CvConstructor = new (...args: any[]) => CvObject;
type CvRuntime = {
  Mat: CvConstructor;
  ORB: CvConstructor;
  BFMatcher: CvConstructor;
  KeyPointVector: CvConstructor;
  DMatchVectorVector: CvConstructor;
  CV_32FC2: number;
  COLOR_RGBA2GRAY: number;
  RANSAC: number;
  NORM_HAMMING: number;
  matFromImageData: (imageData: ImageData) => CvObject;
  matFromArray: (rows: number, columns: number, type: number, values: number[]) => CvObject;
  cvtColor: (source: CvObject, destination: CvObject, conversion: number) => void;
  findHomography: (
    source: CvObject,
    destination: CvObject,
    method: number,
    reprojectionThreshold: number,
    mask: CvObject,
    maxIterations: number,
    confidence: number,
  ) => CvObject;
  countNonZero: (matrix: CvObject) => number;
};

export type DiagramTracker = {
  detect: (video: HTMLVideoElement) => {
    matrix: number[];
    inliers: number;
    referenceWidth: number;
    referenceHeight: number;
    frameWidth: number;
    frameHeight: number;
  } | null;
  dispose: () => void;
};

async function loadOpenCv(): Promise<CvRuntime> {
  const imported = await import("@techstark/opencv-js");
  const exported = imported.default as Promise<CvRuntime> | CvRuntime;
  return exported instanceof Promise ? exported : exported;
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("The curated diagram could not be loaded for tracking."));
    image.src = url;
    if (image.complete && image.naturalWidth > 0) resolve(image);
  });
}

export async function createDiagramTracker(imageUrl: string): Promise<DiagramTracker> {
  const [cv, image] = await Promise.all([loadOpenCv(), loadImage(imageUrl)]);
  const referenceCanvas = document.createElement("canvas");
  referenceCanvas.width = image.naturalWidth;
  referenceCanvas.height = image.naturalHeight;
  const referenceContext = referenceCanvas.getContext("2d", { willReadFrequently: true });
  if (!referenceContext) throw new Error("Local diagram tracking is unavailable in this browser.");
  referenceContext.drawImage(image, 0, 0);

  const referenceRgba = cv.matFromImageData(referenceContext.getImageData(0, 0, referenceCanvas.width, referenceCanvas.height));
  const referenceGray = new cv.Mat();
  const referenceKeypoints = new cv.KeyPointVector();
  const referenceDescriptors = new cv.Mat();
  const orb = new cv.ORB(900);
  const matcher = new cv.BFMatcher(cv.NORM_HAMMING, false);
  const mask = new cv.Mat();
  cv.cvtColor(referenceRgba, referenceGray, cv.COLOR_RGBA2GRAY);
  orb.detectAndCompute(referenceGray, mask, referenceKeypoints, referenceDescriptors);
  referenceRgba.delete();
  referenceGray.delete();

  if (referenceDescriptors.rows < 8) {
    mask.delete();
    referenceKeypoints.delete();
    referenceDescriptors.delete();
    orb.delete();
    matcher.delete();
    throw new Error("The curated diagram has too few visual features for automatic tracking.");
  }

  const frameCanvas = document.createElement("canvas");
  const frameContext = frameCanvas.getContext("2d", { willReadFrequently: true });
  if (!frameContext) {
    mask.delete();
    matcher.delete();
    orb.delete();
    referenceKeypoints.delete();
    referenceDescriptors.delete();
    throw new Error("Local camera-frame processing is unavailable in this browser.");
  }

  return {
    detect(video) {
      if (video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA || !video.videoWidth || !video.videoHeight) {
        return null;
      }
      const scale = Math.min(1, 640 / video.videoWidth);
      frameCanvas.width = Math.max(1, Math.round(video.videoWidth * scale));
      frameCanvas.height = Math.max(1, Math.round(video.videoHeight * scale));
      frameContext.drawImage(video, 0, 0, frameCanvas.width, frameCanvas.height);

      const rgba = cv.matFromImageData(frameContext.getImageData(0, 0, frameCanvas.width, frameCanvas.height));
      const gray = new cv.Mat();
      const keypoints = new cv.KeyPointVector();
      const descriptors = new cv.Mat();
      const matches = new cv.DMatchVectorVector();
      const sourcePoints: number[] = [];
      const destinationPoints: number[] = [];
      let source: CvObject | null = null;
      let destination: CvObject | null = null;
      let inlierMask: CvObject | null = null;
      let homography: CvObject | null = null;
      let result: ReturnType<DiagramTracker["detect"]> = null;

      try {
        cv.cvtColor(rgba, gray, cv.COLOR_RGBA2GRAY);
        orb.detectAndCompute(gray, mask, keypoints, descriptors);
        if (descriptors.rows < 8) return null;
        matcher.knnMatch(referenceDescriptors, descriptors, matches, 2);
        for (let index = 0; index < matches.size(); index += 1) {
          const pair = matches.get(index);
          let best: CvObject | null = null;
          let next: CvObject | null = null;
          let referencePoint: CvObject | null = null;
          let framePoint: CvObject | null = null;
          try {
            if (pair.size() >= 2) {
              best = pair.get(0);
              next = pair.get(1);
              if (best && next && best.distance < next.distance * 0.72 && best.distance < 72) {
                referencePoint = referenceKeypoints.get(best.queryIdx);
                framePoint = keypoints.get(best.trainIdx);
                if (referencePoint && framePoint) {
                  sourcePoints.push(referencePoint.pt.x, referencePoint.pt.y);
                  destinationPoints.push(framePoint.pt.x, framePoint.pt.y);
                }
              }
            }
          } finally {
            best?.delete?.();
            next?.delete?.();
            referencePoint?.delete?.();
            framePoint?.delete?.();
            pair.delete();
          }
        }
        if (
          !areCorrespondencesValid(
            sourcePoints,
            destinationPoints,
            image.naturalWidth,
            image.naturalHeight,
            frameCanvas.width,
            frameCanvas.height,
          )
        ) return null;

        source = cv.matFromArray(sourcePoints.length / 2, 1, cv.CV_32FC2, sourcePoints);
        destination = cv.matFromArray(destinationPoints.length / 2, 1, cv.CV_32FC2, destinationPoints);
        inlierMask = new cv.Mat();
        homography = cv.findHomography(source, destination, cv.RANSAC, 4, inlierMask, 1200, 0.995);
        const inliers = cv.countNonZero(inlierMask);
        if (
          homography.empty() ||
          !hasReliableInlierSupport(inliers, sourcePoints.length / 2)
        ) return null;

        const matrix = Array.from(homography.data64F as Float64Array).slice(0, 9);
        if (
          isPlausibleDetection(
            matrix,
            image.naturalWidth,
            image.naturalHeight,
            frameCanvas.width,
            frameCanvas.height,
          )
        ) {
          result = {
            matrix,
            inliers,
            referenceWidth: image.naturalWidth,
            referenceHeight: image.naturalHeight,
            frameWidth: frameCanvas.width,
            frameHeight: frameCanvas.height,
          };
        }
      } catch {
        return null;
      } finally {
        rgba.delete();
        gray.delete();
        keypoints.delete();
        descriptors.delete();
        matches.delete();
        source?.delete();
        destination?.delete();
        inlierMask?.delete();
        homography?.delete();
      }
      return result;
    },
    dispose() {
      matcher.delete();
      orb.delete();
      mask.delete();
      referenceKeypoints.delete();
      referenceDescriptors.delete();
    },
  };
}
