<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, nextTick, watch } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ArrowUp, ArrowUpRight, BookOpen, Brain, Check, ChevronRight, Compass, Download, FileText, FolderOpen, Layers3, LoaderCircle, Menu, MessageSquare, Plus, RefreshCw, Search, Settings2, ShieldCheck, LogIn, LogOut, UserPlus, Sparkles, Square, Trash2, X } from 'lucide-vue-next'
import { api, authenticatedFetch, subscribe, terminal, type Run, type Source, type EventItem, type Publication } from './api'
import { completeLogin, configureIssuer, currentUser, login, logout, register } from './auth'

import ImageAttachments from './ImageAttachments.vue'
import ReportReader from './ReportReader.vue'
import AttachmentImage from './AttachmentImage.vue'
const attachmentIds=ref<string[]>([]), attachmentBusy=ref(false), imagePicker=ref<InstanceType<typeof ImageAttachments>|null>(null)
const serviceCapabilities=ref<{reasons:string[];submit_research:boolean}|null>(null)
const dataPolicy=ref("internal")
const qualityLabel=(value:string)=>({complete:"完整研究结果（模型核验）",partial:"部分结果：仍有未回答或未核验内容",insufficient:"证据不足",unverified:"未经研究证据核验",unknown:"历史结果：核验信息不完整"}[value]||"核验信息不完整")
const pendingRequest=ref<{fingerprint:string;id:string}|null>(null)
const reasonLabel=(key:string)=>({invalid_records:'部分检索字段无效，已剔除',vector_unavailable:'向量检索不可用，已使用关键词检索',reranker_unavailable:'重排服务不可用，已回退融合排序',ok:'检索完成',empty:'部分检索未找到结果',error:'检索服务发生故障',budget_exhausted:'本轮研究已达到检索上限',search_limit_reached:'本轮研究已达到检索上限，可继续研究',insufficient_evidence:'证据不足，请补充资料或调整问题',image_unreadable:'请上传清晰图片',policy_blocked:'资料策略禁止此检索路径',queue_unavailable:'任务队列暂不可用',object_store_unavailable:'资料存储暂不可用',scanner_unavailable:'安全扫描暂不可用',circuit_open:'模型服务暂时熔断',provider_blocked:'模型配置需要管理员处理'}[key]||key)

type View = 'research'|'reports'|'documents'|'memories'|'settings'
const view = ref<View>('research'), navOpen = ref(false)
const threads = ref<any[]>([]), threadId = ref(localStorage.getItem('dr-thread') || '')
const runs = ref<Run[]>([]), selected = ref<Run|null>(null), events = ref<EventItem[]>([])
const publishedReports = ref<Publication[]>([]), pendingReports = ref<Publication[]>([]), archivedReports = ref<Publication[]>([])
const selectedPublication = ref<Publication|null>(null), reviewReason = ref('')
const topic = ref(''), mode = ref('auto'), error = ref(''), busy = ref(false)
const status = ref<any>(null), documents = ref<any[]>([]), memories = ref<any[]>([]), metrics = ref<any>(null)
const preference = ref(''), editingMemory = ref(''), source = ref<Source|null>(null)
const sourceDrawer = ref<HTMLElement|null>(null)
let sourceTrigger: HTMLElement|null = null
watch(source, async (next, previous) => {
  if (next && !previous) sourceTrigger = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  if (source.value !== next) return
  if (next) sourceDrawer.value?.querySelector<HTMLButtonElement>('.close-drawer')?.focus()
  else {
    if (sourceTrigger?.isConnected) sourceTrigger.focus()
    sourceTrigger = null
  }
})
function handleSourceKeys(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    source.value = null
  } else if (event.key === 'Tab') {
    const controls = Array.from(sourceDrawer.value?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], summary, [tabindex="0"]') || [])
      .filter(element => element.getClientRects().length > 0)
    const first = controls[0], last = controls.at(-1)
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
  }
}
const selectedDocument = ref<any|null>(null), documentChunks = ref<any[]>([]), documentQuery = ref(''), documentResults = ref<any[]>([]), documentReasons = ref<string[]>([]), documentSearchBusy = ref(false)
const userId = ref('')
const authenticated = ref(false), authMode = ref<'oidc'>('oidc'), threadSwitchId = ref(localStorage.getItem('dr-thread') || '')
const connection = ref<Record<string,any>|null>(null), checking = ref(false), uploadBusy = ref(false), deadLetters = ref<any[]>([]), workspace = ref<any|null>(null), workspaces = ref<any[]>([])
const members = ref<{subject:string;role:'admin'|'researcher'|'viewer';created_at:string;creator:boolean}[]>([])
const memberSubject = ref(''), memberRole = ref<'admin'|'researcher'|'viewer'>('admin'), memberNotice = ref('')
const removingMember = ref('')
const uploadInput = ref<HTMLInputElement|null>(null), logOpen = ref(false), rename = ref(false), newTitle = ref('')
const replacementInput = ref<HTMLInputElement|null>(null), replacementTarget = ref<any|null>(null), replacingId = ref('')
let controller: AbortController|null = null, poll: ReturnType<typeof setInterval>|undefined, generation = 0
const active = computed(()=>runs.value.some(run=>!terminal(run.status)))
const currentTitle = computed(()=>threads.value.find(t=>t.id===threadId.value)?.title || '研究工作台')
const viewTitle = computed(()=>({research: currentTitle.value, reports:'正式报告', documents:'本地资料库', memories:'研究记忆', settings:'工作台设置'}[view.value]))
// Presentation only: opening a report reader starts at its heading.
watch([view, () => selectedPublication.value?.id], async ([nextView, publicationId]) => {
  if (nextView === 'reports' && publicationId) {
    await nextTick()
    window.scrollTo({ top: 0, behavior: 'auto' })
  }
})
const conversationHistory = computed(()=>runs.value.filter(run=>run.id!==selected.value?.id))
function renderReport(report: string){return DOMPurify.sanitize(marked.parse(report,{async:false}) as string, {FORBID_TAGS:['img','style','iframe','form'], ADD_ATTR:['target']})}
function revealDetails(event: MouseEvent, selector: string) {
  const article = (event.currentTarget as HTMLElement).closest('article')
  const details = article?.querySelector<HTMLDetailsElement>(selector)
  if (!details) return
  details.open = true
  details.scrollIntoView({ block: 'center' })
  details.querySelector('summary')?.focus({ preventScroll: true })
}
const reportHtml = computed(()=>renderReport(selected.value?.report || ''))
const reviewSubmissionReason = computed(()=>{
  const run=selected.value
  if(!run||!run.report||run.report_submitted||run.publication)return null
  if(!run.created_by||run.created_by!==currentUser()?.id)return '只有这次研究任务的作者可以提交审核；作者记录缺失的历史任务也不能提交。'
  if(run.status!=='completed')return '只有已完成的研究任务可以提交审核。'
  if(!['complete','partial'].includes(run.validation?.quality)||run.validation?.verification_pending)return `当前结果为“${qualityLabel(run.validation?.quality||'unknown')}”，没有可送审的已核验证据；请完善资料后发起新的研究任务。`
  if(!run.sources?.some(item=>item.text?.trim()&&run.report.includes(`[${item.id}]`)))return '正文缺少可对应来源原文的引用，不能提交审核。'
  const checked=Number(run.validation?.checked_claims||0), supported=Number(run.validation?.supported_claims||0)
  if(checked<1||supported<1||supported>checked||run.validation?.quality==='complete'&&supported!==checked)return '没有保留下来的已核验结论，不能提交审核。'
  return null
})
const localSourceCount = computed(()=>selected.value?.sources.filter(item=>item.kind==='local').length || 0)
const plan = computed(()=>events.value.find(e=>e.type==='plan')?.data)
const nodes = [ ['vision','图片理解'], ['router','协调路由 Agent'], ['planner','研究规划 Agent'], ['web_scout','网络调研 Agent'], ['local_scout','本地资料 Agent'], ['judge','证据裁决 Agent'], ['analyst','分析 Agent'], ['reflect','补搜决策 Agent'], ['writer','报告撰写 Agent'], ['validator','引用核验 Agent'] ]
const nodeName = (key: string)=>nodes.find(n=>n[0]===key)?.[1] || key
const stateLabel = (state: string)=>({queued:'等待执行',running:'研究中',completed:'已完成',insufficient:'证据不足',failed:'执行失败',cancelled:'已取消',interrupted:'已中断'}[state] || state)
function nodeState(key: string) {
  const matches = events.value.filter(e=>e.data.node===key)
  const latest = matches[matches.length-1]
  if(!latest) return 'pending'
  if(latest.type==='node_end') return 'done'
  if(latest.type==='node_start' && selected.value && !terminal(selected.value.status)) return 'running'
  return 'stopped'
}
const warnings = computed(()=>[...new Set(events.value.filter(e=>e.type==='warning').map(e=>e.data.message))])
async function safely(fn: ()=>Promise<void>) { try{error.value='';await fn()}catch(e){error.value=e instanceof Error?e.message:'操作失败'} }
async function activateUser(user: ReturnType<typeof currentUser>){
  if(!user)throw new Error('未取得有效登录凭证')
  userId.value=user.name;await refreshWorkspace();await refreshThreads();await refreshStatus()
  if(threadId.value&&threads.value.some(t=>t.id===threadId.value))await openThread(threadId.value);else newResearch()
  authenticated.value=true
}
async function beginLogin(){ await login() }
async function beginRegistration(){ await register() }
function signOut(){ localStorage.removeItem('dr-workspace'); logout() }
function handleAuthExpired(){
  controller?.abort(); generation++; authenticated.value=false; userId.value=''; status.value=null
  localStorage.removeItem('dr-workspace');workspace.value=null;workspaces.value=[]
  threads.value=[]; runs.value=[]; selected.value=null; events.value=[]; threadId.value=''
  error.value='登录已过期，请重新登录；研究任务仍可在登录后查看'
}
async function refreshThreads(){threads.value=await api('/api/threads')}
async function refreshStatus(){status.value=await api('/api/status');serviceCapabilities.value=await api('/api/capabilities')}
async function refreshPublications(){
  publishedReports.value=await api('/api/reports')
  if(workspace.value?.role==='admin'){
    pendingReports.value=await api('/api/reports/pending')
    archivedReports.value=await api('/api/reports/archived')
  }else{pendingReports.value=[];archivedReports.value=[]}
}
async function openPublication(id:string){selectedPublication.value=await api<Publication>(`/api/reports/${id}`);reviewReason.value=''}
async function submitPublication(){if(!selected.value)return;await safely(async()=>{
  const report=await api<Publication>(`/api/research/runs/${selected.value!.id}/publication`,{method:'POST'})
  await refreshRun(selected.value!.id,generation)
  await refreshPublications();selectedPublication.value=report;view.value='reports'
})}
async function reviewPublication(action:'approve'|'reject'|'withdraw'){
  if(!selectedPublication.value)return
  if((action!=='approve'||selectedPublication.value.validation.quality==='partial')&&!reviewReason.value.trim())return
  await safely(async()=>{
    const id=selectedPublication.value!.id
    selectedPublication.value=await api<Publication>(`/api/reports/${id}/${action}`,{
      method:'POST',...(action==='approve'&&!reviewReason.value.trim()?{}:{body:JSON.stringify({reason:reviewReason.value.trim()})})})
    reviewReason.value='';await refreshPublications()
    if(selected.value?.id===selectedPublication.value?.source_run_id)await refreshRun(selected.value.id,generation)
  })
}
async function deletePublication(){if(!selectedPublication.value||!window.confirm('清理这条已驳回或已撤回的审核记录？此操作不可恢复。'))return
  await safely(async()=>{
    const sourceRunId=selectedPublication.value!.source_run_id
    const response=await authenticatedFetch(`/api/reports/${selectedPublication.value!.id}`,{method:'DELETE'})
    if(!response.ok)throw new Error((await response.json().catch(()=>({detail:'清理失败'}))).detail)
    selectedPublication.value=null;await refreshPublications()
    if(selected.value?.id===sourceRunId)await refreshRun(sourceRunId,generation)
  })
}
async function refreshWorkspace(){
  workspaces.value=await api<any[]>('/api/workspaces')
  const preferred=localStorage.getItem('dr-workspace')
  workspace.value=workspaces.value.find(item=>item.id===preferred)||workspaces.value[0]||null
  if(workspace.value)localStorage.setItem('dr-workspace',workspace.value.id)
  else localStorage.removeItem('dr-workspace')
}
async function refreshMembers(){
  members.value=workspace.value?.role==='admin'
    ?await api(`/api/workspaces/${workspace.value.id}/members`):[]
}
async function upsertMember(){
  const subject=memberSubject.value.trim()
  if(!workspace.value||!subject)return
  await safely(async()=>{
    await api(`/api/workspaces/${workspace.value.id}/members`,{method:'PUT',body:JSON.stringify({subject,role:memberRole.value})})
    await refreshMembers();memberSubject.value='';memberNotice.value='成员已更新。请让对方刷新工作空间列表并选择此工作空间。'
  })
}
async function removeMember(subject:string){
  if(!workspace.value||!window.confirm(`从当前工作空间移除 ${subject}？对方将失去访问权限，已有研究与审核记录会保留。`))return
  removingMember.value=subject
  try{await safely(async()=>{
    await api(`/api/workspaces/${workspace.value!.id}/members/${encodeURIComponent(subject)}`,{method:'DELETE'})
    await refreshMembers();memberNotice.value='成员已移除；对方刷新页面后将无法再进入此工作空间。'
  })}finally{removingMember.value=''}
}
async function copyAccountId(){
  const id=currentUser()?.id
  if(!id)return
  await safely(async()=>{await navigator.clipboard.writeText(id);memberNotice.value='账号 ID 已复制。'})
}
async function switchWorkspace(event:Event){
  const id=(event.target as HTMLSelectElement).value
  const target=workspaces.value.find(item=>item.id===id)
  if(!target||target.id===workspace.value?.id)return
  localStorage.setItem('dr-workspace',id);workspace.value=target
  threads.value=[];documents.value=[];memories.value=[];publishedReports.value=[];pendingReports.value=[]
  archivedReports.value=[];selectedPublication.value=null;newResearch()
  await safely(async()=>{await refreshThreads();await refreshStatus()})
}
async function refreshRun(id: string, epoch: number) {
  const run = await api<Run>(`/api/research/runs/${id}`)
  if(epoch!==generation) return
  selected.value=run
  const i=runs.value.findIndex(r=>r.id===id)
  if(i>=0) runs.value[i]=run
}
async function selectRun(run: Run) {
  controller?.abort(); controller=new AbortController(); const epoch=++generation
  selected.value=run; events.value=[]; source.value=null
  void subscribe(run.id,e=>{if(epoch===generation){events.value.push(e);if(e.type==='running')selected.value!.status='running'}},
    ()=>refreshRun(run.id,epoch),controller.signal).catch(e=>{if(epoch===generation)error.value=e.message})
}
async function openThread(id: string) {
  const thread=threads.value.find(item=>item.id===id||item.thread_key===id), resolvedId=thread?.id||id
  controller?.abort();generation++;threadId.value=resolvedId;threadSwitchId.value=thread?.thread_key||id;localStorage.setItem('dr-thread',resolvedId)
  runs.value=[];selected.value=null;events.value=[];view.value='research';navOpen.value=false
  const loaded=await api<Run[]>(`/api/threads/${resolvedId}/runs`)
  if(threadId.value!==resolvedId)return
  runs.value=loaded
  if(loaded.length)await selectRun(loaded[loaded.length-1])
}
function newResearch(){attachmentIds.value=[];pendingRequest.value=null;controller?.abort();generation++;threadId.value='';threadSwitchId.value='';localStorage.removeItem('dr-thread');runs.value=[];selected.value=null;events.value=[];view.value='research';navOpen.value=false;topic.value='';error.value=''}
async function send() {
  if((!topic.value.trim()&&!attachmentIds.value.length)||attachmentBusy.value||busy.value||active.value)return
  busy.value=true
  await safely(async()=>{
    const payload={data_policy:dataPolicy.value,topic:topic.value.trim(),mode:mode.value,thread_id:threadId.value||null,attachment_ids:[...attachmentIds.value]}
    const fingerprint=JSON.stringify(payload)
    if(pendingRequest.value?.fingerprint!==fingerprint)pendingRequest.value={fingerprint,id:crypto.randomUUID()}
    const run=await api<Run>('/api/research/runs',{method:'POST',body:JSON.stringify({...payload,client_request_id:pendingRequest.value!.id})})
    attachmentIds.value=[];pendingRequest.value=null
    threadId.value=run.thread_id;localStorage.setItem('dr-thread',run.thread_id);runs.value.push(run);topic.value=''
    await refreshThreads();threadSwitchId.value=threads.value.find(thread=>thread.id===run.thread_id)?.thread_key||run.thread_id;await selectRun(run)
  });busy.value=false
}
async function cancel(){if(selected.value)await safely(async()=>{selected.value=await api(`/api/research/runs/${selected.value!.id}/cancel`,{method:'POST'});await refreshRun(selected.value!.id,generation)})}
async function recoverDeadLetter(item: any){
  if(!workspace.value||workspace.value.role!=='admin')return
  if(!window.confirm('确认从检查点恢复此死信任务？'))return
  await safely(async()=>{
    await api(`/api/workspaces/${workspace.value.id}/dead-letters/${item.run_id}/recover`,{method:'POST'})
    deadLetters.value=await api(`/api/workspaces/${workspace.value.id}/dead-letters`)
  })
}
async function resume(){if(selected.value)await safely(async()=>{const run=await api<Run>(`/api/research/runs/${selected.value!.id}/resume`,{method:'POST'});const i=runs.value.findIndex(r=>r.id===run.id);runs.value[i]=run;await selectRun(run)})}
async function navigate(next: View){view.value=next;navOpen.value=false;error.value='';await safely(async()=>{if(next==='reports')await refreshPublications();if(next==='documents')documents.value=await api('/api/documents');if(next==='memories')memories.value=await api('/api/memories');if(next==='settings'){await refreshStatus();metrics.value=await api('/api/metrics');await refreshWorkspace();await refreshMembers();deadLetters.value=workspace.value?.role==='admin'?await api(`/api/workspaces/${workspace.value.id}/dead-letters`):[]}})}
async function upload(event: Event) {
  const input=event.target as HTMLInputElement
  if(!input.files?.length)return
  uploadBusy.value=true
  await safely(async()=>{for(const file of Array.from(input.files!)){const data=new FormData();data.append('file',file);await api('/api/documents',{method:'POST',body:data})}documents.value=await api('/api/documents')})
  input.value='';uploadBusy.value=false
}
async function deleteDocument(doc: any){await safely(async()=>{await api(`/api/documents/${doc.id}`,{method:'DELETE'});documents.value=await api('/api/documents')})}
function chooseReplacement(doc:any){replacementTarget.value=doc;replacementInput.value?.click()}
async function replaceDocument(event:Event){const input=event.target as HTMLInputElement;const file=input.files?.[0],doc=replacementTarget.value;if(!file||!doc){input.value='';return}replacingId.value=doc.id;await safely(async()=>{const data=new FormData();data.append('file',file);await api(`/api/documents/${doc.id}`,{method:'PUT',body:data});documents.value=await api('/api/documents');if(selectedDocument.value?.id===doc.id)documentChunks.value=[]});input.value='';replacementTarget.value=null;replacingId.value=''}
async function inspectDocument(doc: any){await safely(async()=>{selectedDocument.value=doc;documentChunks.value=await api(`/api/documents/${doc.id}/chunks?limit=400`)})}
async function reindexDocument(doc: any){await safely(async()=>{await api(`/api/documents/${doc.id}/reindex`,{method:'POST'});documents.value=await api('/api/documents');if(selectedDocument.value?.id===doc.id)documentChunks.value=[]})}
async function searchDocuments(){if(!documentQuery.value.trim())return;documentSearchBusy.value=true;documentResults.value=[];documentReasons.value=[];await safely(async()=>{const result=await api<{results:any[],reasons:string[]}>('/api/documents/search?diagnostics=true',{method:'POST',body:JSON.stringify({query:documentQuery.value.trim(),limit:6})});documentResults.value=result.results;documentReasons.value=result.reasons});documentSearchBusy.value=false}
async function saveMemory(){if(!preference.value.trim())return;await safely(async()=>{await api(editingMemory.value?`/api/memories/${editingMemory.value}`:'/api/memories',{method:editingMemory.value?'PUT':'POST',body:JSON.stringify({content:preference.value})});preference.value='';editingMemory.value='';memories.value=await api('/api/memories')})}
async function deleteMemory(id: string){await safely(async()=>{await api(`/api/memories/${id}`,{method:'DELETE'});memories.value=await api('/api/memories')})}
async function saveSettings(){await safely(async()=>{
  const requestedThread=threadSwitchId.value.trim()
  if(!/^[a-zA-Z0-9_-]{1,64}$/.test(userId.value))throw new Error('用户标识只能包含字母、数字、下划线和连字符')
  if(requestedThread&&!/^[a-zA-Z0-9_-]{1,32}$/.test(requestedThread))throw new Error('Thread ID 只能包含字母、数字、下划线和连字符')
  localStorage.setItem('dr-user',userId.value)
  controller?.abort();generation++;threadId.value='';localStorage.removeItem('dr-thread');runs.value=[];selected.value=null;events.value=[];topic.value='';error.value=''
  await refreshStatus();await refreshThreads()
  if(requestedThread){
    const thread=threads.value.find(item=>item.id===requestedThread||item.thread_key===requestedThread)
    if(!thread)throw new Error('该 User ID 下未找到此 Thread ID')
    await openThread(thread.id)
  }else{threadSwitchId.value='';view.value='settings'}
})}
async function checkConnection(){checking.value=true;await safely(async()=>{connection.value=await api('/api/config/check',{method:'POST'})});checking.value=false}
function downloadBlob(blob: Blob, filename: string){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(url),60_000)}
async function saveMarkdown(path: string, filename: string){const response=await authenticatedFetch(path);if(!response.ok)throw new Error('下载失败');downloadBlob(await response.blob(),filename)}
async function downloadRun(run: Run){await safely(async()=>{await saveMarkdown(`/api/research/runs/${run.id}/report`,`research-${run.id.slice(0,8)}.md`)})}
async function downloadConversation(){if(!threadId.value)return;await safely(async()=>{await saveMarkdown(`/api/threads/${threadId.value}/report`,`conversation-${threadId.value.slice(0,8)}.md`)})}
async function saveRunMemory(){if(!selected.value)return;await safely(async()=>{await api(`/api/research/runs/${selected.value!.id}/memory`,{method:'POST'});memories.value=await api('/api/memories')})}
async function exportData(){await safely(async()=>{const response=await authenticatedFetch('/api/data/export');if(!response.ok)throw new Error('导出失败');downloadBlob(await response.blob(),'deepresearch-export.zip')})}
async function purgeData(scope: string){if(!window.confirm(`确定清理${scope==='all'?'全部本地数据':'对应本地数据'}吗？此操作不可恢复。`))return;await safely(async()=>{await api(`/api/data?scope=${scope}`,{method:'DELETE',headers:{'X-Confirm-Delete':'DELETE'}});await refreshThreads();documents.value=[];memories.value=[];metrics.value=await api('/api/metrics');selected.value=null})}
function useExample(text: string){topic.value=text;void nextTick(()=>document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus())}
async function renameThread(){await safely(async()=>{await api(`/api/threads/${threadId.value}`,{method:'PATCH',body:JSON.stringify({title:newTitle.value})});rename.value=false;await refreshThreads()})}
async function deleteThread(thread: any){
  if(!window.confirm(`删除“${thread.title}”及其中的全部对话和报告吗？此操作不可恢复。`))return
  await safely(async()=>{
    const current=thread.id===threadId.value
    await api(`/api/threads/${thread.id}`,{method:'DELETE'})
    if(current)newResearch()
    await refreshThreads()
  })
}
function inspectCitation(event: MouseEvent){const target=event.target as HTMLElement;const text=target.textContent||'';const id=text.match(/\[(W-[a-f0-9]+|L-[a-f0-9]+|I-[a-f0-9]+)\]/)?.[1];if(id)source.value=selected.value?.sources.find(s=>s.id===id)||null}
onMounted(async()=>{
  window.addEventListener('deepresearch-auth-expired', handleAuthExpired)
  await safely(async()=>{
    const config=await fetch('/api/auth/config').then(async response=>{
      if(!response.ok)throw new Error('无法读取登录配置')
      return response.json() as Promise<{mode:'oidc',issuer:string}>
    })
    authMode.value=config.mode
    configureIssuer(config.issuer)
    const user=await completeLogin()||currentUser()
    if(user)await activateUser(user)
  })
  poll=setInterval(()=>{if(view.value==='settings'&&authenticated.value&&workspace.value?.role==='admin')void safely(async()=>{deadLetters.value=await api(`/api/workspaces/${workspace.value.id}/dead-letters`)});if(view.value==='documents'&&documents.value.some(d=>['indexing','rebuilding'].includes(d.status)))void safely(async()=>{documents.value=await api('/api/documents')});if(selected.value&&!terminal(selected.value.status))void refreshRun(selected.value.id,generation).catch(()=>{})},2500)
})
onUnmounted(()=>{controller?.abort();clearInterval(poll);window.removeEventListener('deepresearch-auth-expired', handleAuthExpired)})
</script>

<template>
  <section v-if="!authenticated" class="auth-landing">
    <div class="auth-story">
      <div class="auth-brand"><Layers3 :size="26" aria-hidden="true"/><strong>DeepResearch</strong></div>
      <div class="auth-statement"><h1 aria-label="让研究有据可循。"><span>让研究</span><span>有据可循。</span></h1><p>从一个问题出发，留下可以核查的结论。</p></div>
      <div class="auth-method"><span>提出问题</span><ChevronRight :size="16" aria-hidden="true"/><span>查阅资料</span><ChevronRight :size="16" aria-hidden="true"/><span>核对证据</span></div>
    </div>
    <div class="auth-entry">
      <div class="auth-card">
        <h2>进入研究工作台</h2>
        <p>写下问题，选择自己的文档或公开资料，得到带来源的研究报告。</p>
        <button class="auth-primary" @click="safely(beginLogin)"><LogIn :size="18"/>登录并开始研究<ArrowUpRight :size="18" aria-hidden="true"/></button>
        <button class="auth-secondary" @click="safely(beginRegistration)"><UserPlus :size="18"/>创建本地账号</button>
        <small>使用本地账号登录，资料与报告按工作空间管理。</small>
        <p v-if="error" class="error-banner" role="alert">{{error}}</p>
      </div>
      <p class="auth-footnote">研究结果受来源覆盖与时效影响，请核对原始证据。</p>
    </div>
  </section>
  <div v-else class="workspace">
    <div v-if="navOpen" class="nav-backdrop" @click="navOpen=false"></div>
    <aside id="workspace-navigation" :inert="!!source" class="sidebar" :class="{open:navOpen}">
      <a class="brand" href="#" @click.prevent="newResearch"><div class="brand-mark"><Layers3 :size="23"/></div><div>DeepResearch<span>让研究有据可循</span></div></a>
      <button class="new-button" @click="newResearch"><Plus :size="18"/> 新建研究</button>
      <nav>
        <button :class="{selected:view==='research'}" @click="navigate('research')"><Compass :size="18"/>研究工作台</button>
        <button :class="{selected:view==='reports'}" @click="navigate('reports')"><FileText :size="18"/>正式报告</button>
        <button :class="{selected:view==='documents'}" @click="navigate('documents')"><FolderOpen :size="18"/>本地资料库 <small v-if="documents.length">{{documents.length}}</small></button>
        <button :class="{selected:view==='memories'}" @click="navigate('memories')"><Brain :size="18"/>研究记忆</button>
      </nav>
      <div class="history-label">最近研究 <span>{{threads.length}}</span></div>
      <div class="thread-list"><p v-if="!threads.length" class="empty-history">你的研究会保存在这里</p><div v-for="thread in threads" :key="thread.id" :class="['thread-item',{chosen:thread.id===threadId}]"><button class="thread-select" @click="safely(()=>openThread(thread.id))"><MessageSquare :size="15"/><div><span>{{thread.title}}</span></div></button><button v-if="workspace?.role==='admin'" class="thread-delete icon-button" :aria-label="`删除研究 ${thread.title}`" title="删除研究" @click.stop="deleteThread(thread)"><Trash2 :size="15"/></button></div></div>
      <div class="sidebar-bottom"><label class="workspace-selector">工作空间<select :value="workspace?.id" aria-label="切换工作空间" @change="switchWorkspace"><option v-for="item in workspaces" :key="item.id" :value="item.id">{{item.name}} · {{item.role}}</option></select></label><div class="local-badge"><span class="status-dot"></span>本地工作空间 <ShieldCheck :size="14"/></div><button @click="navigate('settings')"><Settings2 :size="17"/>工作台设置</button><div class="profile"><div class="avatar">研</div><div>{{userId}}<small>已登录用户</small></div></div></div>
    </aside>
    <main :inert="!!source">
      <header class="topbar"><div class="breadcrumb"><button class="mobile-menu icon-button" aria-label="打开导航" :aria-expanded="navOpen" aria-controls="workspace-navigation" @click="navOpen=true"><Menu :size="20"/></button><span>工作空间</span><ChevronRight :size="14"/><strong>{{viewTitle}}</strong></div><div class="top-status"><span :class="['status-dot',{'amber':!status||status.missing?.length}]"></span>{{status?.mode==='demo'?'测试模式':status?.missing?.length?'部分服务待配置':'已连接'}}<span class="divider"></span><span>本地工作空间</span></div><button v-if="!authenticated" class="text-button" @click="safely(beginLogin)"><LogIn :size="15"/>登录</button><button v-else class="text-button" @click="signOut"><LogOut :size="15"/><span>退出 {{userId}}</span></button></header>
      <div v-if="error" class="error-banner" role="alert">{{error}}<button aria-label="关闭提示" @click="error=''"><X :size="16"/></button></div>
      <div v-if="status?.mode==='demo'" class="demo-banner">当前为固定数据测试模式，报告用于验证功能，不代表真实研究结论。</div>

      <section v-if="view==='research'" class="research-view" :class="{hasRun:selected}">
        <template v-if="!selected">
          <div class="welcome"><h1>这次，想研究什么？</h1><p>写下问题，带着原文依据看结果。</p></div>
          <div class="start-area"><p v-if="serviceCapabilities?.reasons.length" class="inline-warning">部分服务暂不可用：{{serviceCapabilities.reasons.map(reasonLabel).join("；")}}。已有报告仍可查看。</p>
            <div class="composer" @paste="imagePicker?.paste($event)" @dragover.prevent @drop="imagePicker?.drop($event)"><div class="scope-bar"><label class="data-policy">资料范围<select v-model="dataPolicy" aria-label="资料范围" aria-describedby="scope-help"><option value="internal">内部资料（不联网）</option><option value="public">公开问题（联网检索）</option></select></label><p id="scope-help" class="scope-help" aria-live="polite"><template v-if="dataPolicy==='internal'">只查本地资料，不联网；可结合会话与研究记忆。<button class="text-button" @click="navigate('documents')">查看资料库 <ArrowUpRight :size="14"/></button></template><template v-else>联网查公开信息，不读取本地资料、私有历史与个人记忆。</template></p></div><textarea v-model="topic" aria-label="研究主题" maxlength="4000" placeholder="你想研究什么？试着描述主题、时间范围和你关心的问题…" @keydown.enter.exact.prevent="send"></textarea><ImageAttachments ref="imagePicker" v-model="attachmentIds" :disabled="busy||active" @busy="attachmentBusy=$event"/><div class="composer-footer"><div class="mode-picker"><Sparkles :size="15"/><select v-model="mode" aria-label="研究模式"><option value="auto">自动判断研究方式</option><option value="deep">深度研究</option><option value="quick">快速问答</option></select></div><span class="composer-hint">Enter 发送 · Shift + Enter 换行</span><button class="send-button" aria-label="开始研究" :disabled="busy||attachmentBusy||(!topic.trim()&&!attachmentIds.length)" @click="send"><LoaderCircle v-if="busy" class="spin" :size="20"/><ArrowUp v-else :size="18"/><span>开始研究</span></button></div></div>
            <div class="examples-title">试试这样问 <span>点击填入问题，资料范围保持当前选择</span></div>
            <div class="examples"><button @click="useExample('请对比线上课程和线下课程在学习时间、互动方式和适用人群上的差异，说明资料时间、依据和仍需确认的地方。')"><h3>比较两种选择<ArrowUpRight :size="16"/></h3><p>线上课还是线下课？比较差异与适用情况。</p><span>填入问题 <ArrowUpRight :size="13"/></span></button><button @click="useExample('请梳理远程办公常见的协作方式、适用场景与局限，注明资料时间和来源，区分事实与观点。')"><h3>了解一个话题<ArrowUpRight :size="16"/></h3><p>远程办公怎样协作，有哪些局限？</p><span>填入问题 <ArrowUpRight :size="13"/></span></button></div>
            <button class="document-entry" @click="navigate('documents')"><h3>读懂自己的资料<ArrowUpRight :size="16"/></h3><p>添加文件，再围绕内容提问。</p><span>打开资料库 <ArrowUpRight :size="13"/></span></button>
          </div>
        </template>

        <template v-else>
          <div class="run-header"><div><h2>研究结果</h2><p v-if="conversationHistory.length" class="session-note">已保留 {{conversationHistory.length}} 条历史研究；继续提问会沿用当前会话。</p></div><button v-if="threadId" class="text-button" @click="downloadConversation"><Download :size="15"/>导出全部会话</button><button v-if="threadId" class="text-button" @click="newTitle=currentTitle;rename=true">重命名会话</button></div>
          <div v-if="rename" class="inline-edit"><input v-model="newTitle" maxlength="100" aria-label="会话名称"><button class="primary" @click="renameThread">保存</button><button @click="rename=false">取消</button></div>

          <p v-if="terminal(selected.status)" class="result-quality" role="status">{{qualityLabel(selected.validation?.quality||"unknown")}}</p><p v-if="selected.error_info?.action" class="inline-warning">{{selected.error_info.action}}</p><p class="current-turn"><strong>研究问题</strong><span>{{selected.topic}}</span></p>
          <div class="attachment-previews"><AttachmentImage v-for="item in selected.attachments||[]" :key="item.id" :id="item.id" :name="item.name"/></div><p v-if="selected.validation?.reasons?.length" class="inline-warning">{{selected.validation.reasons.map(reasonLabel).join("；")}}</p><div class="run-layout"><div class="report-column">
            <div class="progress-card"><div class="progress-heading"><div><LoaderCircle v-if="!terminal(selected.status)" class="spin" :size="18"/><Check v-else-if="selected.status==='completed'" :size="18"/><Compass v-else :size="18"/><strong>{{stateLabel(selected.status)}}</strong></div><button v-if="!terminal(selected.status)" class="text-button" @click="cancel"><Square :size="12"/>停止</button><button v-else-if="selected.can_resume" class="text-button" @click="resume">从检查点继续</button><button class="text-button" :aria-expanded="logOpen" @click="logOpen=!logOpen">{{logOpen?'收起研究过程':'展开研究过程'}}</button></div><div v-if="logOpen" class="node-track"><div v-for="node in nodes" :key="node[0]" :class="['node',nodeState(node[0])]"><i><Check v-if="nodeState(node[0])==='done'" :size="11"/><LoaderCircle v-else-if="nodeState(node[0])==='running'" class="spin" :size="11"/></i>{{node[1]}}</div></div><div v-if="plan&&!selected.report" class="plan-summary"><strong>{{plan.title}}</strong><p>{{plan.scope}}</p><ol><li v-for="question in plan.questions" :key="question">{{question}}</li></ol></div><div v-if="logOpen" class="event-log"><div v-for="event in events" :key="event.id"><time>{{new Date(event.created_at).toLocaleTimeString()}}</time><span v-if="event.type==='agent_start'">{{event.data.label}}启动 · 工具：{{event.data.tools.join('、')}}</span><span v-else-if="event.type==='agent_handoff'">{{nodeName(event.data.from)}}交接给{{event.data.to.map(nodeName).join('、')||'完成'}}</span><span v-else-if="event.type==='node_start'">{{nodeName(event.data.node)}}开始</span><span v-else-if="event.type==='node_end'">{{nodeName(event.data.node)}}完成 · {{event.data.duration_ms}} ms</span><span v-else-if="event.type==='retrieval_status'">{{event.data.message||reasonLabel(event.data.outcome)}}</span><span v-else-if="event.type==='vision_result'">图片理解完成 · 可读 {{event.data.readable}} / {{event.data.count}} 张</span><span v-else-if="event.type==='reflection'">补搜：{{event.data.reason}}</span><span v-else-if="event.type==='sources_found'">{{event.data.kind==='web'?'网络':'本地'}}取得 {{event.data.count}} 条来源</span><span v-else>{{event.data.message||event.type}}</span></div></div></div>
            <div v-if="selected.error" class="inline-warning">{{selected.error}}</div><div v-if="selected.sources.some(item=>item.kind==='web'&&item.access==='summary')" class="inline-warning">此历史报告含未取得可读正文的候选链接，可能已失效、需要登录或需要付费。请重新运行后再将结论用于决策。</div><details v-if="warnings.length" class="warnings"><summary>{{warnings.length}} 条研究提示</summary><p v-for="warning in warnings" :key="warning">{{warning}}</p></details>
            <article v-if="selected.report" class="report-card"><div class="report-toolbar"><span><FileText :size="16"/>研究报告 · {{selected.publication?.status==='published'?'已发布':selected.publication?.status==='pending'?'待人工审核':selected.publication?.status==='rejected'?'已驳回':selected.publication?.status==='withdrawn'?'已撤回':selected.report_submitted?'审核记录已清理':'未提交审核'}}</span><button class="text-button" @click="revealDetails($event, '.report-metrics')">核验与用量 <ChevronRight :size="14"/></button></div><ReportReader :html="reportHtml" @inspect="inspectCitation"/><div class="report-actions"><button v-if="selected.status==='completed'" class="text-button" @click="saveRunMemory"><Brain :size="15"/>保存到研究记忆</button><button v-if="!selected.report_submitted&&!selected.publication" class="text-button review-submit" :disabled="!!reviewSubmissionReason" @click="submitPublication">提交人工审核</button><button v-if="selected.publication" class="text-button" @click="safely(()=>openPublication(selected!.publication!.id).then(()=>{view='reports'}))">查看审核记录</button><button class="text-button" @click="downloadRun(selected)"><Download :size="15"/>导出 Markdown</button></div><p v-if="reviewSubmissionReason" class="review-guidance">{{reviewSubmissionReason}}</p><p v-else-if="selected.validation?.quality==='partial'&&!selected.report_submitted&&!selected.publication" class="review-guidance">这是一份部分结果。可以送人工审核，但未回答的问题及删除的结论会保留在正式快照中，管理员批准时须说明发布范围。</p><p v-if="selected.publication?.review_reason" class="review-guidance">{{selected.publication.status==='published'?'批准说明':'驳回原因'}}：{{selected.publication.review_reason}}</p><p v-if="selected.publication?.withdrawal_reason" class="review-guidance">撤回原因：{{selected.publication.withdrawal_reason}}</p><details class="technical-details report-metrics"><summary>核验与用量（引用、来源、调用次数）</summary><div class="metrics-grid"><span>引用检查 {{selected.validation.supported_claims||0}} / {{selected.validation.checked_claims||0}} 条结论</span><span>来源 {{selected.validation.evidence_metrics?.source_count||0}} · 域名 {{selected.validation.evidence_metrics?.unique_web_domains||0}}</span><span>模型调用 {{selected.usage.llm_calls||0}} 次</span><span>搜索 {{selected.usage.search_calls||0}} 次</span></div></details></article>
            <div v-if="!selected.report&&!terminal(selected.status)" class="working-placeholder"><div class="orb"><Search :size="26"/></div><h3>正在沿着证据展开研究</h3><p>检索、分析和核查需要一些时间。你可以离开页面，稍后继续查看。</p></div>
          </div><aside class="evidence-panel"><div class="panel-heading"><BookOpen :size="17"/><strong>研究来源</strong><span v-if="localSourceCount">本地资料 {{localSourceCount}}</span><span>{{selected.sources.length}}</span></div><p v-if="!selected.sources.length" class="muted">报告完成后，引用的资料将展示在这里。</p><button v-for="(item,index) in selected.sources" :key="item.id" class="source-card" @click="source=item"><small>{{index+1}} · {{item.kind==='attachment'?'本轮图片':item.kind==='web'?'网络资料':'本地资料库'}}</small><h4>{{item.title}}</h4><p>{{item.trust_label||item.domain||item.locator||(item.access==='summary'?'仅搜索摘要':'已读取正文')}}</p><span>查看证据片段 <ArrowUpRight :size="12"/></span></button><div class="source-note"><ShieldCheck :size="17"/><p>引用可追溯<br><span>重要结论需要正文与独立来源支持。</span></p></div></aside></div>
          <details v-if="conversationHistory.length" class="conversation-history technical-details" aria-label="本会话的历史研究"><summary>查看本会话之前的 {{conversationHistory.length}} 次研究</summary>
            <article v-for="run in conversationHistory" :key="run.id" class="conversation-turn">
              <div class="conversation-question"><MessageSquare :size="15"/><strong>你</strong><span>{{run.topic}}</span></div>
              <div class="attachment-previews"><AttachmentImage v-for="item in run.attachments||[]" :key="item.id" :id="item.id" :name="item.name"/></div><div class="conversation-response"><div class="conversation-response-heading"><FileText :size="15"/><strong>研究结果</strong><span>{{stateLabel(run.status)}}</span><button class="text-button" @click="selectRun(run)"><BookOpen :size="14"/>查看来源 {{run.sources.length}}</button><button v-if="run.report" class="text-button" @click="downloadRun(run)"><Download :size="14"/>导出</button></div><div v-if="run.report" class="conversation-markdown markdown" v-html="renderReport(run.report)"></div><p v-else class="muted">这次研究尚未生成可展示的报告。</p></div>
            </article>
          </details>
          <div class="followup composer" @paste="imagePicker?.paste($event)" @dragover.prevent @drop="imagePicker?.drop($event)"><ImageAttachments ref="imagePicker" v-model="attachmentIds" :disabled="busy||active" @busy="attachmentBusy=$event"/><textarea v-model="topic" aria-label="继续追问" maxlength="4000" :placeholder="active?'研究进行中，完成后可继续追问…':'继续追问，或进一步限定研究范围…'" @keydown.enter.exact.prevent="send"></textarea><div class="composer-footer"><label class="data-policy">资料范围<select v-model="dataPolicy" aria-label="资料范围"><option value="internal">内部资料（不联网）</option><option value="public">公开问题（联网检索）</option></select></label><div class="mode-picker"><select v-model="mode" aria-label="追问模式"><option value="auto">自动判断研究方式</option><option value="deep">深度研究</option><option value="quick">快速问答</option></select></div><span class="composer-hint">{{dataPolicy==='internal'?'结合当前会话 · 重新核查证据':'仅用公开资料 · 不读取私有历史'}}</span><button class="send-button" aria-label="发送追问" :disabled="active||busy||attachmentBusy||(!topic.trim()&&!attachmentIds.length)" @click="send"><ArrowUp :size="20"/></button></div></div>
        </template>
      </section>

      <section v-if="view==='reports'" class="utility-view report-workflow">
        <h1>正式报告</h1>
        <p v-if="!selectedPublication" class="intro">在这里查看已发布报告，或处理待审核的研究。正式版本保留提交时的正文和证据。</p>
        <div v-if="!selectedPublication" class="report-index">
        <div class="section-heading"><h3>已发布</h3><span>{{publishedReports.length}} 份</span></div>
        <p v-if="!publishedReports.length" class="muted">暂无已发布报告。</p>
        <button v-for="item in publishedReports" :key="item.id" class="list-card report-list-item" @click="safely(()=>openPublication(item.id))"><FileText :size="19"/><span><strong>{{item.topic}}</strong><small>审核于 {{new Date(item.reviewed_at).toLocaleString()}}</small></span><span class="pill green-pill">{{item.quality==='partial'?'已发布 · 部分结果':'已发布'}}</span></button>
        <template v-if="workspace?.role==='admin'">
          <div class="section-heading"><h3>待审核</h3><span>{{pendingReports.length}} 份</span></div>
          <p v-if="!pendingReports.length" class="muted">暂无待审核报告。</p>
          <button v-for="item in pendingReports" :key="item.id" class="list-card report-list-item" @click="safely(()=>openPublication(item.id))"><FileText :size="19"/><span><strong>{{item.topic}}</strong><small>提交于 {{new Date(item.submitted_at).toLocaleString()}}</small></span><span class="pill">{{item.quality==='partial'?'待审核 · 部分结果':'待审核'}}</span></button>
          <div class="section-heading"><h3>已驳回与已撤回</h3><span>{{archivedReports.length}} 份</span></div>
          <button v-for="item in archivedReports" :key="item.id" class="list-card report-list-item" @click="safely(()=>openPublication(item.id))"><FileText :size="19"/><span><strong>{{item.topic}}</strong><small>提交于 {{new Date(item.submitted_at).toLocaleString()}}</small></span><span class="pill">{{item.status==='rejected'?'已驳回':'已撤回'}}</span></button>
        </template>
        </div>
        <button v-if="selectedPublication" class="text-button back-to-reports" @click="selectedPublication=null">返回报告列表</button>
        <article v-if="selectedPublication" class="report-card publication-detail">
          <div class="report-toolbar"><strong>{{selectedPublication.topic}}</strong><span class="pill">{{selectedPublication.status==='published'?'正式版本':selectedPublication.status==='pending'?'待审核':selectedPublication.status==='rejected'?'已驳回':'已撤回'}}{{selectedPublication.validation.quality==='partial'?' · 部分结果':''}}</span><button class="text-button" @click="revealDetails($event, '.publication-meta')">版本与审核 <ChevronRight :size="14"/></button></div>

          <div v-if="selectedPublication.validation.quality==='partial'" class="publication-limits"><strong>限定范围报告：仍有未完整回答或未保留的结论。</strong><p>{{selectedPublication.validation.unanswered_questions?.length?'以下问题尚未完整回答，请留意报告的局限与审核说明。':'仍有未通过核验或未保留的内容，可用范围以现有证据与审核意见为准。'}}</p><details class="technical-details"><summary>查看核验数量</summary><p>已核验 {{selectedPublication.validation.supported_claims||0}} 条；未通过或删除 {{selectedPublication.validation.removed_claims||0}} 条。管理员批准时须说明可发布范围。</p></details><ul v-if="selectedPublication.validation.unanswered_questions?.length"><li v-for="question in selectedPublication.validation.unanswered_questions" :key="question">{{question}}</li></ul></div>
          <p v-if="selectedPublication.review_reason" class="publication-review-note">{{selectedPublication.status==='published'?'批准说明':'驳回原因'}}：{{selectedPublication.review_reason}}</p>
          <p v-if="selectedPublication.withdrawal_reason" class="publication-review-note">撤回原因：{{selectedPublication.withdrawal_reason}}</p>
          <ReportReader :html="renderReport(selectedPublication.report_markdown)"/>
          <div class="publication-evidence"><h3>这份报告依据的原文</h3><div v-for="item in selectedPublication.sources" :key="item.id" class="debug-result"><strong>{{item.title}}</strong><small>{{item.locator||item.url||item.id}}<template v-if="item.original_available===false"> · 原文当前不可访问</template><template v-else-if="item.original_available===null"> · 外部网页未持续核验</template></small><p>{{item.text}}</p></div></div>
          <details class="publication-meta technical-details"><summary>版本与审核（作者、审核人、内容指纹）</summary><div>作者 {{selectedPublication.author_subject}} · 提交 {{new Date(selectedPublication.submitted_at).toLocaleString()}}<br>内容 SHA-256：<code>{{selectedPublication.content_sha256}}</code><template v-if="selectedPublication.reviewed_by"><br>审核人 {{selectedPublication.reviewed_by}} · {{new Date(selectedPublication.reviewed_at).toLocaleString()}}</template></div></details>
          <div class="review-actions"><button v-if="selectedPublication.status==='published'" class="text-button" @click="safely(()=>saveMarkdown(`/api/reports/${selectedPublication!.id}/download`,`report-${selectedPublication!.id.slice(0,8)}.md`))"><Download :size="15"/>下载正式版本</button><template v-if="workspace?.role==='admin'"><p v-if="selectedPublication.status==='pending'&&selectedPublication.author_subject===currentUser()?.id" class="inline-warning">报告作者不能审核自己的报告，请由另一位管理员处理。</p><label v-if="(selectedPublication.status==='pending'&&selectedPublication.author_subject!==currentUser()?.id)||selectedPublication.status==='published'">{{selectedPublication.status==='published'?'撤回原因':'审核意见（部分结果批准时必填）'}}<textarea v-model="reviewReason" maxlength="1000" placeholder="说明可发布范围、尚未回答的问题或驳回原因"></textarea></label><button v-if="selectedPublication.status==='pending'&&selectedPublication.author_subject!==currentUser()?.id" class="primary" :disabled="selectedPublication.validation.quality==='partial'&&!reviewReason.trim()" @click="reviewPublication('approve')">批准发布</button><button v-if="selectedPublication.status==='pending'&&selectedPublication.author_subject!==currentUser()?.id" class="text-button" :disabled="!reviewReason.trim()" @click="reviewPublication('reject')">驳回</button><button v-if="selectedPublication.status==='published'" class="text-button" :disabled="!reviewReason.trim()" @click="reviewPublication('withdraw')">撤回正式版本</button><button v-if="['rejected','withdrawn'].includes(selectedPublication.status)" class="text-button" @click="deletePublication">清理审核记录</button></template></div>
        </article>
      </section>

      <section v-if="view==='documents'" class="utility-view">
        <h1>把资料，变成研究的依据。</h1>
        <p class="intro">导入你的行业报告和笔记。选择“内部资料”研究时会检索这些资料，并保留原文位置。</p>
        <input ref="uploadInput" type="file" accept=".txt,.md,.pdf,.docx,.jpg,.jpeg,.png,.gif,.webp" multiple hidden @change="upload">
        <input ref="replacementInput" type="file" accept=".txt,.md,.pdf,.docx,.jpg,.jpeg,.png,.gif,.webp" hidden @change="replaceDocument">
        <button class="upload-zone" :disabled="uploadBusy" @click="uploadInput?.click()"><FolderOpen :size="32"/><strong>{{uploadBusy?'正在上传…':'选择本地资料'}}</strong><span>TXT / Markdown / DOCX / PDF / JPG / PNG / GIF / WebP · 每份不超过 10 MB</span><small>表格保留表头；扫描或复杂 PDF 页会尝试视觉解析，无法可靠识别时提示修复文件。</small></button>
        <div class="section-heading"><h3>已导入资料</h3><span>{{documents.length}} 份</span></div>
        <div v-if="!documents.length" class="empty-state"><BookOpen :size="28"/><p>还没有资料，添加一份文档开始吧。</p></div>
        <div v-for="doc in documents" :key="doc.id" class="list-card">
          <div class="file-icon"><FileText :size="21"/></div>
          <div class="list-content"><strong>{{doc.name}}</strong><small>{{new Date(doc.created_at).toLocaleString()}} · 版本 {{doc.index_version}}<template v-if="doc.pending_version"> → {{doc.pending_version}}</template></small><p v-if="doc.error" class="danger">{{doc.error}}</p><p v-if="['quarantined','scan_failed'].includes(doc.status)" class="inline-warning">隔离或扫描失败的资料不会建立索引，不能用于研究、检索或导出。</p></div>
          <span :class="['pill',doc.status==='ready'?'green-pill':'']">{{({scanning:'正在安全扫描',ready:'可检索',rebuilding:'重建中（旧版可用）',indexing:'正在建立索引',quarantined:'已隔离',scan_failed:'扫描失败',failed:'导入失败'} as any)[doc.status]}}</span>
          <button class="text-button" :disabled="doc.status!=='ready'" @click="inspectDocument(doc)">片段</button>
          <button class="text-button" :disabled="doc.status!=='ready'||replacingId===doc.id" @click="chooseReplacement(doc)">{{replacingId===doc.id?'替换中…':'替换'}}</button>
          <button class="icon-button" :aria-label="`重建 ${doc.name} 的索引`" :disabled="!['ready','failed'].includes(doc.status)" @click="reindexDocument(doc)"><RefreshCw :size="16"/></button>
          <button class="icon-button" :aria-label="`删除 ${doc.name}`" @click="deleteDocument(doc)"><Trash2 :size="17"/></button>
        </div>
        <div v-if="documents.length" class="settings-card retrieval-debug"><h3>在资料中查找</h3><p class="muted">输入问题，先看看已有文档能提供哪些原文依据。</p><div class="inline-edit"><input v-model="documentQuery" placeholder="例如：企业私有化部署的优缺点" @keydown.enter.prevent="searchDocuments"><button class="primary" :disabled="documentSearchBusy||!documentQuery.trim()" @click="searchDocuments"><Search :size="15"/>{{documentSearchBusy?'检索中…':'检索资料'}}</button></div><p v-for="reason in documentReasons" :key="reason" class="inline-warning">{{reasonLabel(reason)}}</p><div v-for="result in documentResults" :key="result.id" class="debug-result"><strong>{{result.title}}</strong><small>{{result.locator}}</small><span>{{result.text.slice(0,260)}}{{result.text.length>260?'…':''}}</span><details class="technical-details"><summary>检索技术信息（匹配分数）</summary><p>融合 {{result.score}} · 向量 {{result.vector_score}} · BM25 {{result.bm25_score}}<template v-if="result.rerank_score!==null"> · 重排 {{result.rerank_score}}</template> · {{result.ranking_stage}}</p></details></div></div>
        <div v-if="selectedDocument" class="settings-card chunk-view"><h3>{{selectedDocument.name}} 的分块</h3><p class="muted">{{documentChunks.length}} 个已加载片段，保留标题或页码与字符定位。</p><div v-for="(chunk,index) in documentChunks" :key="chunk.id" class="debug-result"><strong>Chunk {{index+1}}</strong><small>{{chunk.locator}}</small><span>{{chunk.text}}</span></div></div>
      </section>

      <section v-if="view==='memories'" class="utility-view"><h1>让下一次研究，更懂你。</h1><p class="intro">用户偏好和个人设定按登录账号私有，会在本人的不同研究会话间共享，同一工作空间的其他成员无法读取或修改；历史研究摘要只在保存它的研究会话内用于找回上下文，不作为新事实的依据。</p><div class="memory-editor"><label for="preference">{{editingMemory?'编辑偏好':'添加一条研究偏好'}}</label><textarea id="preference" v-model="preference" maxlength="1000" placeholder="例如：优先关注中国市场，报告中明确资料年份，使用中文输出。"></textarea><button class="primary" :disabled="!preference.trim()" @click="saveMemory">{{editingMemory?'保存修改':'保存偏好'}}</button><button v-if="editingMemory" class="text-button" @click="editingMemory='';preference=''">取消编辑</button></div><div v-if="!memories.length" class="empty-state"><Brain :size="28"/><p>还没有保存的记忆。</p></div><div v-for="memory in memories" :key="memory.id" class="memory-card"><div class="memory-top"><span class="pill">{{memory.kind==='preference'?'用户偏好':memory.kind==='profile'?'个人设定':'历史研究摘要'}}</span><div><button v-if="memory.kind==='preference'" class="text-button" @click="editingMemory=memory.id;preference=memory.content">编辑</button><button class="icon-button" aria-label="删除记忆" @click="deleteMemory(memory.id)"><Trash2 :size="16"/></button></div></div><p>{{memory.content}}</p><small>{{new Date(memory.created_at).toLocaleString()}}</small></div></section>

      <section v-if="view==='settings'" class="utility-view"><h1>你的研究工作空间。</h1><p class="intro">API 密钥在本地 .env 文件配置，网页不会接收或显示模型密钥。</p>
        <div class="settings-card"><h3>工作空间成员</h3><p class="muted">让另一位已注册用户加入同一工作空间，才能审核你提交的报告。Keycloak 管理台账号不等于这里的审核角色。</p>
          <p>我的账号 ID：<code>{{currentUser()?.id}}</code> <button class="text-button" @click="copyAccountId">复制账号 ID</button></p>
          <p v-if="memberNotice" class="muted" role="status">{{memberNotice}}</p>
          <template v-if="workspace?.role==='admin'">
            <h4>当前成员</h4><p v-if="!members.length" class="muted">暂无成员记录。</p>
            <div v-for="member in members" :key="member.subject" class="debug-result"><strong>{{member.subject===currentUser()?.id?'我':member.subject}}</strong><small>{{member.role==='admin'?'管理员':member.role==='researcher'?'研究员':'只读成员'}}{{member.creator?' · 创建者':''}}</small><button v-if="!member.creator&&member.subject!==currentUser()?.id" class="text-button" :aria-label="`移除成员 ${member.subject}`" :disabled="!!removingMember" @click="removeMember(member.subject)">移除</button></div>
            <label>已注册用户的账号 ID<input v-model.trim="memberSubject" maxlength="128" placeholder="请对方在此页面复制账号 ID"></label>
            <label>工作空间角色<select v-model="memberRole"><option value="admin">管理员（可审核）</option><option value="researcher">研究员</option><option value="viewer">成员（只读正式报告）</option></select></label>
            <button class="primary" :disabled="!memberSubject.trim()" @click="upsertMember">添加或更新成员</button>
          </template>
          <button v-else class="text-button" @click="safely(async()=>{await refreshWorkspace();memberNotice='工作空间列表已刷新；请在左侧切换到受邀的工作空间。'})">刷新工作空间列表</button>
        </div><div class="settings-card"><h3>服务连接</h3><dl><dt>对话模型</dt><dd>{{status?.llm_model}}</dd><dt>嵌入模型</dt><dd>{{status?.embedding_model}}</dd><dt>向量维度</dt><dd>{{status?.embedding_dimension}}</dd><dt>资料索引</dt><dd>{{status?.milvus==='ready'?'可用':'尚未连接'}}</dd></dl><p v-if="status?.missing?.length" class="inline-warning">待配置：{{status.missing.join('、')}}</p><button class="primary" :disabled="checking" @click="checkConnection"><LoaderCircle v-if="checking" class="spin" :size="15"/>{{checking?'正在测试…':'测试模型连接'}}</button><small class="block muted">会发送少量测试文字，产生少量 API 调用。</small><pre v-if="connection" class="connection-result">{{JSON.stringify(connection,null,2)}}</pre></div><div class="settings-card"><h3>本地研究指标</h3><dl><dt>完成 / 资料不足</dt><dd>{{metrics?.completed||0}} / {{metrics?.insufficient||0}}</dd><dt>平均来源 / 域名</dt><dd>{{metrics?.avg_sources||0}} / {{metrics?.avg_web_domains||0}}</dd><dt>P50 / P95 节点时延</dt><dd>{{metrics?.node_latency_ms?.p50||0}} / {{metrics?.node_latency_ms?.p95||0}} ms</dd><dt>Token</dt><dd>{{(metrics?.prompt_tokens||0)+(metrics?.completion_tokens||0)}}</dd></dl></div><div v-if="authenticated && workspace?.role==='admin'" class="settings-card"><h3>死信任务治理</h3><p class="muted">管理员可查看失败及重试记录；仅未完成且已停止的任务可从检查点恢复。</p><p v-if="!deadLetters.length" class="muted">当前没有死信任务记录。</p><div v-for="item in deadLetters" :key="item.run_id" class="debug-result"><strong>{{item.category}}</strong><small>{{new Date(item.failed_at).toLocaleString()}}</small><p>{{item.message}}</p><p>{{item.recovery_status==='succeeded'?'重试成功':item.recovery_status==='insufficient'?'重试完成（证据不足）':item.recovery_status==='retrying'?'正在重试…':'待恢复'}}</p><button v-if="item.can_recover" class="text-button" @click="recoverDeadLetter(item)">从检查点恢复</button></div></div><div class="settings-card"><h3>本地数据</h3><button class="primary" @click="exportData"><Download :size="15"/>导出工作空间</button><button class="text-button" @click="purgeData('reports')">清理研究记录</button><button class="text-button" @click="purgeData('all')">清理全部本地数据</button></div><div v-if="!authenticated" class="settings-card"><h3>演示工作空间</h3><p class="muted">User ID 用于隔离资料、记忆和会话；每个用户的新会话自动命名为 thread01、thread02。填入 Thread ID 可直接打开已有会话，留空则切换到新的研究页面。</p><label>User ID<input v-model.trim="userId" maxlength="64" placeholder="例如：user01"></label><label>Thread ID（可选）<input v-model.trim="threadSwitchId" maxlength="32" placeholder="例如：thread01"></label><p v-if="threadId" class="muted">当前 Thread ID：<code>{{threadSwitchId}}</code></p><button class="primary" @click="saveSettings">切换工作空间</button></div></section>
    </main>
    <div v-if="source" class="drawer-backdrop" @click.self="source=null"><aside ref="sourceDrawer" class="source-drawer" role="dialog" aria-modal="true" :aria-label="source.title" @keydown="handleSourceKeys"><button class="icon-button close-drawer" aria-label="关闭来源" @click="source=null"><X :size="20"/></button><h2>{{source.title}}</h2><div class="source-meta"><span>{{source.kind==='attachment'?'本轮图片':source.kind==='local'?'本地资料库':source.access==='summary'?'仅搜索摘要':'已读取正文'}}</span></div><p v-if="source.locator">{{source.locator}}</p><p v-if="source.published_at" class="muted">发布时间：{{source.published_at}}</p><p class="muted">取得时间：{{new Date(source.retrieved_at).toLocaleString()}}</p><a v-if="source.url && source.access!=='summary'" :href="source.url" target="_blank" rel="noopener noreferrer" class="source-link">打开原始来源 <ArrowUpRight :size="15"/></a><p v-else-if="source.kind==='web'" class="inline-warning">该候选地址未取得可读正文，可能已失效、需要登录或需要付费。新版研究不会将它作为引用。</p><AttachmentImage v-if="source.attachment_id" :id="source.attachment_id" :name="source.title"/><h3>{{source.kind==='attachment'?'图片识别内容（未经外部核实）':'用于研究的原文片段'}}</h3><div class="source-excerpt">{{source.text}}</div><details class="technical-details source-technical"><summary>来源技术信息（标识、系统分类）</summary><p>来源 ID：<code>{{source.id}}</code></p><p>系统分类：{{source.evidence_level||'未记录'}}（不等于可信度保证）</p></details></aside></div>
  </div>
</template>
