import type { Publication } from './api'

export type DocumentVersion = { document_id: string; version: number; name: string; hash: string; status: string; created_at: string; current: boolean; chunk_count: number; parser_config?: unknown; index_config?: unknown }
export type TextBlock = { locator: string; text: string }
export type DocumentDiff = { document_id: string; from_version: number; to_version: number; changes: { kind: string; old: TextBlock|null; new: TextBlock|null }[]; semantic_judgment: boolean; truncated?: boolean }
export type RevisionJob = { revision_job_id: string; run_id: string; status: string }
export const verificationContract = 'verification-v3-claim-span'
export const evidenceLabel = (status?: string) => ({ current: '依据当前有效', check_pending: '资料变化，待范围检查', pending_check: '资料变化，待范围检查', checking: '正在检查影响范围', needs_review: '依据变化，需重新核验', unknown: '影响范围无法判定', undetermined: '影响范围无法判定', legacy: '历史格式，修订时完整研究' }[status || ''] || '依据状态尚未检查')
export const impactReasonLabel = (reason:string) => ({direct_source_changed:'已引用的资料版本发生变化',scope_changed_requires_research:'资料范围发生变化，需要重新检索适用条件与潜在例外',legacy_artifact_full_research:'历史报告缺少结构化结论，修订将进行完整研究'}[reason] || reason)
export function canRevise(report: Publication, role: string, subject?: string) {
  return Boolean(subject && report.status === 'published' && (role === 'admin' || role === 'researcher' && report.author_subject === subject))
}
export function revisionFingerprint(report: Publication, revision: number) { return `${report.id}:${report.content_sha256}:${revision}` }
export function reviewBlocked(report: Publication) {
  return Boolean(report.evidence_status && !['current', 'legacy'].includes(report.evidence_status)
    || report.status === 'pending' && report.validation?.verification_contract !== verificationContract)
}
