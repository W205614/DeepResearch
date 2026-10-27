<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { api, type Publication } from './api'
import { canRevise, evidenceLabel, impactReasonLabel, revisionFingerprint, type RevisionJob, type DocumentDiff } from './lifecycle'

type Impact = { report_id:string; target_revision:number; status:string; reasons:string[]; affected_claims:string[]; diff:Partial<DocumentDiff>; created_at:string }
type ImpactResult = { current_corpus_revision:number; corpus_revision:number; evidence_status:string; impacts:Impact[]; revision_jobs?:{id:string;target_run_id:string;status:string}[] }
type Series = { id:string; current_report_id:string|null; reports:Publication[] }
type ReportDiff = { changes:{ kind:string; old?:{text?:string;statement?:string}|null; new?:{text?:string;statement?:string}|null }[]; before_report:string; after_report:string; legacy:boolean }
const props = defineProps<{ report:Publication; role:string; subject?:string }>()
const emit = defineEmits<{ open:[id:string]; run:[id:string]; evidence:[status:string] }>()
const impact = ref<ImpactResult|null>(null), series = ref<Series|null>(null), diff = ref<ReportDiff|null>(null)
const against = ref(''), busy = ref(false), revising = ref(false), error = ref(''), job = ref<RevisionJob|null>(null)
const pending = ref<{ fingerprint:string; id:string }|null>(null)
let generation = 0, alive = true
onUnmounted(() => { alive = false; generation++ })
const alternatives = computed(() => series.value?.reports.filter(item => item.id !== props.report.id) || [])
const canStart = computed(() => canRevise(props.report, props.role, props.subject) && (!series.value || series.value.current_report_id === props.report.id) && impact.value && Number.isSafeInteger(impact.value.current_corpus_revision))
async function load() {
  const epoch = ++generation, id = props.report.id, seriesId = props.report.series_id
  impact.value = null; series.value = null; diff.value = null; error.value = ''; job.value = null; busy.value = true
  try {
    const results = await Promise.all([
      api<ImpactResult>(`/api/reports/${encodeURIComponent(id)}/impacts`),
      seriesId ? api<Series>(`/api/report-series/${encodeURIComponent(seriesId)}`) : Promise.resolve(null),
    ])
    if (!alive || epoch !== generation) return
    impact.value = results[0]; series.value = results[1]; emit('evidence', results[0].evidence_status)
    against.value = alternatives.value[0]?.id || ''
  } catch (e) { if (alive && epoch === generation) error.value = e instanceof Error ? e.message : '报告版本读取失败' }
  finally { if (alive && epoch === generation) busy.value = false }
}
watch(() => [props.report.id, props.report.status, props.report.content_sha256, props.report.evidence_status], load, { immediate:true })
async function compare() {
  const epoch = generation
  busy.value = true; error.value = ''; diff.value = null
  try {
    const result = await api<ReportDiff>(`/api/reports/${encodeURIComponent(props.report.id)}/diff?against=${encodeURIComponent(against.value)}`)
    if (alive && epoch === generation) diff.value = result
  } catch (e) { if (alive && epoch === generation) error.value = e instanceof Error ? e.message : '报告差异读取失败' }
  finally { if (alive && epoch === generation) busy.value = false }
}
async function revise() {
  if (!canStart.value || revising.value || !impact.value) return
  const epoch = generation, revision = impact.value.current_corpus_revision
  const fingerprint = revisionFingerprint(props.report, revision)
  if (pending.value?.fingerprint !== fingerprint) pending.value = { fingerprint, id: crypto.randomUUID() }
  const body = { client_request_id:pending.value.id, expected_content_sha256:props.report.content_sha256, expected_corpus_revision:revision }
  revising.value = true; error.value = ''
  try {
    const result = await api<RevisionJob>(`/api/reports/${encodeURIComponent(props.report.id)}/revisions`, { method:'POST', body:JSON.stringify(body) })
    if (!alive || epoch !== generation) return
    job.value = result
  } catch (e) { if (alive && epoch === generation) error.value = `${e instanceof Error ? e.message : '修订发起失败'}。若资料已变化，请刷新版本信息后重试。` }
  finally { if (alive && epoch === generation) revising.value = false }
}
</script>
<template>
  <section class="lifecycle-panel" aria-label="报告版本与资料影响">
    <div class="section-heading"><h3>报告版本与资料影响</h3><button class="text-button" :disabled="busy || revising" @click="load">刷新版本信息</button></div>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在读取版本信息…</p>
    <p v-if="!report.series_id" class="inline-warning">历史格式报告：尚无结构化版本关系，不能推断结论依赖。</p>
    <template v-if="impact">
      <p :class="impact.evidence_status === 'current' ? 'muted' : 'inline-warning'">{{ evidenceLabel(impact.evidence_status) }}。<template v-if="impact.corpus_revision >= 0">资料版本范围：{{ impact.corpus_revision }} → {{ impact.current_corpus_revision }}。</template><template v-else>公开资料报告，不受本地资料版本变化影响。</template></p>
      <p class="muted">来源更新不等于旧结论错误。修订会重新核验并生成新版本，正式正文保留原样，新版须再次异人审核。</p>
      <p v-if="!impact.impacts?.length" class="muted">尚无具体影响记录；这不代表所有结论均未受影响。</p>
      <article v-for="(item, index) in impact.impacts" :key="index" class="debug-result">
        <strong>资料范围 {{ item.target_revision }} · {{ evidenceLabel(item.status) }}</strong>
        <p v-for="reason in item.reasons" :key="reason">{{ impactReasonLabel(reason) }}</p>
        <div v-if="item.affected_claims?.length"><strong>待复核结论</strong><ul><li v-for="id in item.affected_claims" :key="id">{{ report.artifacts?.claims?.find(claim=>claim.id===id)?.text || `结论 ${id}` }}</li></ul></div>
        <div v-for="(change, changeIndex) in item.diff?.changes || []" :key="changeIndex" class="lifecycle-columns"><div v-if="change.old"><small>原证据 · {{ change.old.locator }}</small><p>{{ change.old.text }}</p></div><div v-if="change.new"><small>新证据 · {{ change.new.locator }}</small><p>{{ change.new.text }}</p></div></div>
        <details v-if="item.diff" class="technical-details"><summary>查看变化依据</summary><pre>{{ JSON.stringify(item.diff, null, 2) }}</pre></details>
      </article>
      <button v-if="canStart" class="primary" :disabled="busy || revising || !!job" @click="revise">{{ revising ? '正在提交修订…' : job ? '修订已提交' : '依据当前资料发起修订' }}</button>
      <p v-else-if="role === 'viewer'" class="muted">只读成员可以查看正式版本，不能发起修订。</p>
      <p v-else-if="report.status !== 'published'" class="muted">仅已发布报告可以发起新修订。</p>
      <p v-else-if="series && series.current_report_id !== report.id" class="muted">这是历史版本，请从系列当前正式版发起修订。</p>
    </template>
    <div v-if="job" class="debug-result" role="status"><strong>修订任务已受理</strong><p>{{ job.status }} · 新版仍须提交审核。</p><button v-if="job.run_id" class="text-button" @click="emit('run', job.run_id)">查看修订进度</button></div>
    <div v-for="item in impact?.revision_jobs || []" :key="item.id" class="debug-result"><strong>修订记录 · {{ item.status }}</strong><button v-if="item.target_run_id && role !== 'viewer'" class="text-button" @click="emit('run', item.target_run_id)">查看修订任务</button></div>
    <template v-if="series">
      <h4>系列历史版本</h4>
      <button v-for="item in series.reports" :key="item.id" class="text-button lifecycle-version" :disabled="item.id === report.id" @click="emit('open', item.id)">第 {{ item.revision_no || 1 }} 版 · {{ item.topic }} · {{ item.status === 'published' ? '已发布' : item.status === 'pending' ? '待审核' : item.status === 'withdrawn' ? '已撤回' : '已驳回' }}{{ series.current_report_id === item.id ? ' · 当前正式版' : '' }}</button>
      <div v-if="alternatives.length" class="lifecycle-controls"><label>对比报告版本<select v-model="against" :disabled="busy"><option v-for="item in alternatives" :key="item.id" :value="item.id">第 {{ item.revision_no || 1 }} 版</option></select></label><button class="text-button" :disabled="busy || !against" @click="compare">比较报告差异</button></div>
    </template>
    <div v-if="diff" class="lifecycle-diff" role="region" aria-label="报告结论差异">
      <h4>结论与证据差异</h4>
      <p v-if="diff.legacy" class="inline-warning">历史报告缺少结构化结论，请核对完整正文与引用。</p>
      <article v-for="(change, index) in diff.changes || []" :key="index"><strong>{{ ({added:'新增结论',removed:'删除结论',changed:'修改结论',evidence_changed:'引用证据变化'} as Record<string,string>)[change.kind] || change.kind }}</strong><div class="lifecycle-columns"><div v-if="change.old"><small>对比版</small><p>{{ change.old.text || change.old.statement }}</p></div><div v-if="change.new"><small>当前查看版</small><p>{{ change.new.text || change.new.statement }}</p></div></div><details class="technical-details"><summary>查看结论证据变化</summary><pre>{{ JSON.stringify({ before:change.old, after:change.new }, null, 2) }}</pre></details></article>
      <details class="technical-details"><summary>对比完整正文</summary><div class="lifecycle-columns"><div><small>对比版正文</small><pre>{{ diff.before_report }}</pre></div><div><small>当前查看版正文</small><pre>{{ diff.after_report }}</pre></div></div></details>
      <p v-if="!diff.changes?.length && !diff.legacy" class="muted">未发现结构化结论变化，仍可核对正文与引用。</p>
    </div>
  </section>
</template>
