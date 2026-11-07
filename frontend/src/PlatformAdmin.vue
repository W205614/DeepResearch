<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'

type User = { subject:string; display_name:string; status:'active'|'disabled'; revoked_before:number; owned_workspaces:number; membership_count:number }
type Space = { id:string; name:string; created_by:string; owner_subject:string; owner_name?:string; active_admins:number }
type Task = { id:string; user_id:string; created_by:string; status:string; created_at:string; updated_at:string }
type Audit = { id:string; actor_subject:string; action:string; target_type:string; target_id:string; reason:string; result:string; created_at:string }
type Usage = { recorded:boolean; period_start:number|null; calls:number; reserved_tokens:number; pending_tokens:number; unknown_tokens:number; actual_input:number; actual_output:number }
type Overview = { operator:boolean; read_only:boolean; read_only_reasons:string[]; dependencies:Record<string,string>; usage:Usage; limits:{active_runs:number;queued_runs:number;workspace_concurrent_runs:number}; runs:Record<string,number>; users:Record<string,number>; workspaces:Space[]; recent_runs:Task[] }
type Page<T> = { items:T[]; next_cursor:string|null }

const overview = ref<Overview|null>(null), users = ref<User[]>([]), audits = ref<Audit[]>([])
const userCursor = ref<string|null>(null), auditCursor = ref<string|null>(null)
const busy = ref(false), notice = ref(''), error = ref(''), reason = ref('')
const transferTargets = ref<Record<string,string>>({}), filterSubject = ref(''), filterAction = ref(''), filterFrom = ref(''), filterUntil = ref('')
const writable = computed(() => !!overview.value && !overview.value.read_only)
const activeUsers = computed(() => users.value.filter(user => user.status === 'active'))
const terminal = (status:string) => ['completed','insufficient','failed','cancelled'].includes(status)
const label = (status:string) => ({active:'正常',disabled:'已停用',queued:'排队中',running:'执行中',interrupted:'待恢复',completed:'已完成',insufficient:'资料不足',failed:'失败',cancelled:'已取消'}[status] || status)
const dependencyName = (name:string) => ({database:'数据库',redis:'共享队列与限流',agent:'研究服务',vector_store:'向量库',object_store:'原文存储'}[name] || name)
const dependencyState = (state:string) => ({reachable:'已连接',unavailable:'不可用',not_checked:'本接口未探测'}[state] || state)
const readOnlyReason = (reason:string) => ({redis_unavailable:'共享队列不可用',embedding_migration_active:'索引迁移维护'}[reason] || reason)

async function perform(action:()=>Promise<void>) {
  if (busy.value) return
  busy.value=true; error.value=''; notice.value=''
  try { await action() } catch (cause) { error.value=cause instanceof Error ? cause.message : '管理操作失败' }
  finally { busy.value=false }
}
async function loadUsers(append=false) {
  const query=new URLSearchParams({limit:'20'})
  if (append && userCursor.value) query.set('cursor',userCursor.value)
  const result=await api<Page<User>>(`/api/admin/users?${query}`)
  users.value=append ? [...users.value,...result.items] : result.items
  userCursor.value=result.next_cursor
}
async function loadAudit(append=false) {
  const query=new URLSearchParams({limit:'20'})
  if (append && auditCursor.value) query.set('cursor',auditCursor.value)
  if (filterSubject.value.trim()) query.set('actor',filterSubject.value.trim())
  if (filterAction.value.trim()) query.set('action',filterAction.value.trim())
  if (filterFrom.value) query.set('from',new Date(filterFrom.value).toISOString())
  if (filterUntil.value) query.set('until',new Date(filterUntil.value).toISOString())
  const result=await api<Page<Audit>>(`/api/admin/audit?${query}`)
  audits.value=append ? [...audits.value,...result.items] : result.items
  auditCursor.value=result.next_cursor
}
async function refresh() {
  overview.value=await api<Overview>('/api/admin/overview')
  await Promise.all([loadUsers(),loadAudit()])
}
function requireReason() {
  if (!reason.value.trim()) throw new Error('请填写本次操作原因')
  return reason.value.trim()
}
async function setStatus(user:User) {
  await perform(async()=>{
    const status=user.status==='active' ? 'disabled' : 'active', explanation=requireReason()
    if (!window.confirm(`${status==='disabled'?'停用':'恢复'}账号“${user.display_name||user.subject}”？${status==='disabled'?'停用会停止其后续访问及在途研究。':'恢复后该用户需重新登录。'}`)) return
    await api(`/api/admin/users/${encodeURIComponent(user.subject)}/status`,{method:'PUT',body:JSON.stringify({status,reason:explanation})})
    await refresh(); notice.value=status==='disabled'?'账号已停用。':'账号已恢复，请通知用户重新登录。'; reason.value=''
  })
}
async function transfer(space:Space) {
  await perform(async()=>{
    const target=transferTargets.value[space.id], explanation=requireReason()
    if (!target) throw new Error('请选择新的空间所有者')
    if (!window.confirm(`交接“${space.name}”？新所有者将成为管理员，原所有者将降为研究员。`)) return
    await api(`/api/admin/workspaces/${encodeURIComponent(space.id)}/transfer`,{method:'POST',body:JSON.stringify({target_subject:target,reason:explanation})})
    await refresh(); notice.value='空间已交接，历史创建者与报告作者记录保留。'; reason.value=''
  })
}
async function cancel(task:Task) {
  await perform(async()=>{
    const explanation=requireReason()
    if (!window.confirm('取消该研究任务？已经发出的供应商请求及费用不保证能够撤销。')) return
    await api(`/api/admin/runs/${encodeURIComponent(task.id)}/cancel`,{method:'POST',body:JSON.stringify({reason:explanation})})
    await refresh(); notice.value='取消已记录，任务不能继续发布结果。'; reason.value=''
  })
}
onMounted(()=>perform(refresh))
</script>

<template>
  <section class="utility-view platform-admin" aria-labelledby="platform-admin-title">
    <div class="admin-heading"><div><h1 id="platform-admin-title">平台管理</h1><p class="intro">管理账号、空间交接和运行状态。这里仅显示治理所需的信息。</p></div><button class="text-button" :disabled="busy" @click="perform(refresh)">刷新状态</button></div>
    <p v-if="error" class="error-banner" role="alert">{{error}}</p>
    <p v-if="notice" class="admin-notice" role="status">{{notice}}</p>
    <p v-if="overview?.read_only" class="inline-warning" role="status">平台当前为只读状态（{{overview.read_only_reasons.map(readOnlyReason).join('、')}}），管理修改暂不可用；历史资料仍按当前权限提供。</p>
    <p v-if="busy && !overview" class="muted" role="status">正在读取平台状态…</p>
    <template v-if="overview">
      <div class="admin-summary">
        <div><span>执行中 / 上限</span><strong>{{overview.runs.running||0}} / {{overview.limits.active_runs}}</strong></div>
        <div><span>等待中 / 上限</span><strong>{{(overview.runs.queued||0)+(overview.runs.interrupted||0)}} / {{overview.limits.queued_runs}}</strong></div>
        <div><span>正常账号</span><strong>{{overview.users.active||0}}</strong></div>
        <div><span>已停用账号</span><strong>{{overview.users.disabled||0}}</strong></div>
      </div>
      <section class="settings-card" aria-labelledby="admin-usage-title"><h2 id="admin-usage-title">平台用量与依赖</h2>
        <p v-if="!overview.usage.recorded" class="muted">暂无供应商调用计量记录。</p>
        <template v-else><p class="muted">最近有记录的计量周期始于 {{new Date(overview.usage.period_start!*1000).toLocaleString()}}；Token 用量不等于实际账单金额。</p><dl class="admin-usage"><div><dt>供应商请求</dt><dd>{{overview.usage.calls}}</dd></div><div><dt>已报告输入 / 输出 Token</dt><dd>{{overview.usage.actual_input}} / {{overview.usage.actual_output}}</dd></div><div><dt>待结算 / 用量未知 Token</dt><dd>{{overview.usage.pending_tokens}} / {{overview.usage.unknown_tokens}}</dd></div><div><dt>累计预留 Token</dt><dd>{{overview.usage.reserved_tokens}}</dd></div></dl></template>
        <div class="admin-dependencies"><span v-for="(state,name) in overview.dependencies" :key="name">{{dependencyName(String(name))}}：{{dependencyState(state)}}</span></div><p class="muted">未探测的依赖可在工作台设置中查看服务诊断；此处不发起模型请求。</p>
      </section>
      <div class="settings-card"><h2>操作原因</h2><label for="platform-reason">账号变更、空间交接和任务取消都会记录此原因</label><textarea id="platform-reason" v-model="reason" :disabled="busy" maxlength="1000" rows="2" placeholder="例如：人员离职，已完成资料交接"></textarea></div>
      <section class="settings-card" aria-labelledby="admin-users-title"><h2 id="admin-users-title">已知账号</h2><p class="muted">此列表来自本应用已有账号记录，不代表身份平台的全部用户。停用前须交接名下空间，并保留有效管理员。</p>
        <div class="admin-table-wrap"><table><thead><tr><th>账号</th><th>状态</th><th>拥有空间 / 成员关系</th><th>操作</th></tr></thead><tbody><tr v-for="user in users" :key="user.subject"><td><strong>{{user.display_name||user.subject}}</strong><small>{{user.subject}}</small></td><td>{{label(user.status)}}</td><td>{{user.owned_workspaces}} / {{user.membership_count}}</td><td><button class="text-button" :disabled="busy||!writable||!reason.trim()" :aria-label="`${user.status==='active'?'停用':'恢复'}账号 ${user.display_name||user.subject}`" @click="setStatus(user)">{{user.status==='active'?'停用账号':'恢复账号'}}</button></td></tr></tbody></table></div>
        <p v-if="!users.length" class="muted">尚无账号记录。</p><button v-if="userCursor" class="text-button" :disabled="busy" @click="perform(()=>loadUsers(true))">加载更多账号</button>
      </section>
      <section class="settings-card" aria-labelledby="admin-spaces-title"><h2 id="admin-spaces-title">空间交接</h2><p class="muted">从已加载的正常账号中选择接收人；如未找到，请先加载更多账号。交接不改写历史作者。</p>
        <div v-for="space in overview.workspaces" :key="space.id" class="admin-record"><div><strong>{{space.name}}</strong><small>当前所有者：{{space.owner_name||space.owner_subject}} · 有效管理员 {{space.active_admins}} 人</small></div><div class="admin-controls"><select v-model="transferTargets[space.id]" :aria-label="`为 ${space.name} 选择新所有者`" :disabled="busy||!writable"><option :value="undefined" disabled>选择新的所有者</option><option v-for="user in activeUsers.filter(user=>user.subject!==space.owner_subject)" :key="user.subject" :value="user.subject">{{user.display_name||user.subject}}</option></select><button class="text-button" :disabled="busy||!writable||!reason.trim()||!transferTargets[space.id]" @click="transfer(space)">交接空间</button></div></div>
      </section>
      <section class="settings-card" aria-labelledby="admin-runs-title"><h2 id="admin-runs-title">最近任务</h2><p class="muted">这里只显示标识、状态和时间，不展示研究正文。</p><div v-for="task in overview.recent_runs" :key="task.id" class="admin-record"><div><strong>{{label(task.status)}}</strong><small>{{task.id}} · 发起人 {{task.created_by}}</small><small>{{task.updated_at}}</small></div><button v-if="!terminal(task.status)" class="text-button" :disabled="busy||!writable||!reason.trim()" :aria-label="`取消任务 ${task.id}`" @click="cancel(task)">取消任务</button></div><p v-if="!overview.recent_runs.length" class="muted">暂无任务。</p></section>
      <section class="settings-card" aria-labelledby="admin-audit-title"><h2 id="admin-audit-title">治理审计</h2><form class="admin-controls" @submit.prevent="perform(()=>loadAudit())"><label>操作人<input v-model.trim="filterSubject" maxlength="128" placeholder="账号 ID"></label><label>操作类型<input v-model.trim="filterAction" maxlength="100" placeholder="例如 account.disabled"></label><label>开始时间<input v-model="filterFrom" type="datetime-local"></label><label>结束时间<input v-model="filterUntil" type="datetime-local"></label><button class="text-button" :disabled="busy" type="submit">筛选审计</button></form><div v-for="audit in audits" :key="audit.id" class="admin-record"><div><strong>{{audit.action}} · {{audit.result}}</strong><p>{{audit.reason}}</p><small>{{audit.actor_subject}} → {{audit.target_type}} / {{audit.target_id}}</small><small>{{audit.created_at}}</small></div></div><p v-if="!audits.length" class="muted">暂无符合条件的审计记录。</p><button v-if="auditCursor" class="text-button" :disabled="busy" @click="perform(()=>loadAudit(true))">加载更多审计</button></section>
    </template>
  </section>
</template>

<style scoped>
.admin-heading,.admin-record,.admin-controls { display:flex; align-items:center; justify-content:space-between; gap:16px; }
.admin-heading { align-items:flex-start; }
.admin-summary { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:16px; margin:24px 0; }
.admin-summary>div { border:1px solid var(--line); padding:20px; background:var(--paper,#fff); }
.admin-summary span,.admin-record small,td small { display:block; color:var(--muted); font-size:13px; overflow-wrap:anywhere; }
.admin-summary strong { display:block; margin-top:8px; font-size:26px; font-weight:500; }
.admin-table-wrap { overflow-x:auto; }
table { width:100%; border-collapse:collapse; text-align:left; }
th,td { padding:14px 10px; border-bottom:1px solid var(--line); font-size:14px; vertical-align:top; }
th { color:var(--muted); font-weight:500; }
td strong { font-weight:500; }
.settings-card h2 { margin:0 0 12px; font-size:18px; }
.settings-card textarea { width:100%; margin-top:8px; }
.admin-record { padding:18px 0; border-bottom:1px solid var(--line); }
.admin-record>div { min-width:0; }
.admin-record small { margin-top:6px; }
.admin-record p { margin:8px 0; white-space:pre-wrap; overflow-wrap:anywhere; }
.admin-controls { flex-wrap:wrap; justify-content:flex-start; }
.admin-controls label { display:grid; gap:6px; color:var(--muted); font-size:13px; }
.admin-controls select,.admin-controls input { min-height:44px; max-width:100%; }
.admin-usage { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:16px; }
.admin-usage dt,.admin-dependencies { color:var(--muted); font-size:13px; }
.admin-usage dd { margin:6px 0 0; font-variant-numeric:tabular-nums; }
.admin-dependencies { display:flex; flex-wrap:wrap; gap:10px 24px; margin-top:16px; }
.admin-notice { padding:14px 18px; background:#e7f0e8; color:#2b5736; }
@media(max-width:760px) { .admin-summary { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }.admin-heading,.admin-record { align-items:flex-start; flex-direction:column; }.admin-controls { width:100%; }.admin-controls select { width:100%; } }
</style>
