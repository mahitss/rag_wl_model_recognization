export type NormalizedRegion = { x: number; y: number; width: number; height: number };

export type ManualAlignment = {
  x: number;
  y: number;
  scale: number;
  rotation: number;
};

export function normalizedRegionStyle(region: NormalizedRegion) {
  return {
    left: `${region.x * 100}%`,
    top: `${region.y * 100}%`,
    width: `${region.width * 100}%`,
    height: `${region.height * 100}%`,
  };
}

export function alignmentTransform(alignment: ManualAlignment): string {
  return `translate(${alignment.x * 100}%, ${alignment.y * 100}%) rotate(${alignment.rotation}deg) scale(${alignment.scale})`;
}
