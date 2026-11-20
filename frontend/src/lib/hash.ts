/**
 * Client-side SHA-256 using the browser's native Web Crypto API.
 * The digest travels in the upload manifest; the server recomputes it while streaming
 * and rejects the upload (HTTP 409) if they differ — end-to-end transfer integrity.
 */
export async function sha256File(file: File): Promise<string> {
  const buf = await file.arrayBuffer()
  const digest = await crypto.subtle.digest('SHA-256', buf)
  return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, '0')).join('')
}
