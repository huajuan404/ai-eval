export function findScreenshotSurface(elements, viewportWidth, viewportHeight, floor = 0.65) {
  const viewportArea = viewportWidth * viewportHeight;
  for (const element of elements) {
    const imageSurface = ["img", "canvas", "video"].includes(element.tag)
      || element.backgroundImage.includes("url(");
    const areaRatio = (element.width * element.height) / viewportArea;
    if (imageSurface && areaRatio >= floor && element.visible && element.opacity >= 0.2) {
      return {...element, areaRatio};
    }
  }
  return null;
}
