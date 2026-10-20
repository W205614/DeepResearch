import { describe, expect, it } from 'vitest'
import type { Publication } from './api'
import { canRevise, revisionFingerprint, reviewBlocked } from './lifecycle'

const report = { id: 'r', series_id: 's', author_subject: 'author', status: 'published', content_sha256: 'original' } as Publication
describe('report lifecycle UI guards', () => {
  it('allows only author researcher or workspace administrator to revise a published version', () => {
    expect(canRevise(report, 'researcher', 'author')).toBe(true)
    expect(canRevise(report, 'admin', 'reviewer')).toBe(true)
    expect(canRevise(report, 'viewer', 'author')).toBe(false)
    expect(canRevise(report, 'researcher', 'other')).toBe(false)
    expect(canRevise({ ...report, status: 'withdrawn' }, 'admin', 'reviewer')).toBe(false)
  })
  it('binds retry identity to exact report snapshot and corpus revision', () => {
    expect(revisionFingerprint(report, 3)).not.toBe(revisionFingerprint(report, 4))
    expect(revisionFingerprint(report, 3)).not.toBe(revisionFingerprint({ ...report, content_sha256: 'new' }, 3))
  })
  it('blocks approval when evidence needs checking without blocking legacy reads', () => {
    expect(reviewBlocked({ ...report, evidence_status: 'needs_review' })).toBe(true)
    expect(reviewBlocked({ ...report, evidence_status: 'pending_check' })).toBe(true)
    expect(reviewBlocked({ ...report, evidence_status: 'current' })).toBe(false)
    expect(reviewBlocked(report)).toBe(false)
    expect(reviewBlocked({ ...report, status:'pending', evidence_status:'current', validation:{} })).toBe(true)
    expect(reviewBlocked({ ...report, status:'pending', evidence_status:'current', validation:{verification_contract:'verification-v3-claim-span'} })).toBe(false)
  })
})
