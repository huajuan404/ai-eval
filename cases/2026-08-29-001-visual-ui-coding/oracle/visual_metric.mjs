export function parseNormalizedMetric(output) {
  const match = String(output).trim().match(/\((0(?:\.\d+)?|1(?:\.0+)?)\)\s*$/);
  if (!match) throw new Error(`ImageMagick output has no normalized metric: ${output}`);
  return Number.parseFloat(match[1]);
}

export function similarityFromNormalizedRmse(output) {
  return 1 - parseNormalizedMetric(output);
}
