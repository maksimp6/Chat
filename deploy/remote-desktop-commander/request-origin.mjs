/** Resolve identity from deployment configuration, never from request headers. */
export function canonicalPublicOrigin(value) {
  try {
    if (typeof value !== "string" || !value || /[\s\\?#]/.test(value)) throw new Error();
    const url = new URL(value);
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if (url.username || url.password || url.pathname !== "/" ||
        !(url.protocol === "https:" || (url.protocol === "http:" && loopback))) {
      throw new Error();
    }
    return url.origin;
  } catch {
    throw new Error("invalid_alice_dev_public_url");
  }
}
