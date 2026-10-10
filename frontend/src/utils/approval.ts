import type { SessionDetail } from '../types'
import type { PendingApproval } from '../components/ApprovalModal'

/** 刷新后同样读取后端的未决请求，不凭前端缓存推断授权状态。 */
export function pendingFromDetail(detail: SessionDetail | null): PendingApproval | null {
  const p = detail?.pending_approval
  if (!p || p.status !== 'requested') return null
  return { request_id: p.request_id, tool: p.tool, input: p.input, reason: p.reason, options: p.options ?? [], full_access: p.full_access ?? false }
}
