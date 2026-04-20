export function formatRelativeTime(ts: string): string {
  const diff = (Date.now() - new Date(ts).getTime()) / 1000
  if (diff < 60) return `${Math.floor(diff)} 秒前`
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`
  return `${Math.floor(diff / 86400)} 天前`
}

export function formatDate(ts: string): string {
  return new Date(ts).toLocaleDateString('zh-CN')
}

export function formatDateTime(ts: string): string {
  return new Date(ts).toLocaleString('zh-CN')
}
