export function firstHeader(...values) {
  for (const value of values) {
    const candidate = String(value ?? "").split(",")[0].trim();
    if (candidate) return candidate;
  }
  return "";
}
