export function isExpectedHealthAclConsoleError(
  message: string,
  sourceUrl: string,
  pageUrl: string,
): boolean {
  if (message !== 'Failed to load resource: the server responded with a status of 403 (Forbidden)') {
    return false
  }
  try {
    const source = new URL(sourceUrl)
    return source.origin === new URL(pageUrl).origin &&
      source.pathname === '/api/health/ready' && source.search === '' && source.hash === ''
  } catch {
    return false
  }
}
