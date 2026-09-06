<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, nextTick } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'
import { ArrowUp, ArrowUpRight, BookOpen, Brain, Check, ChevronRight, CircleHelp, Compass, Download, FileText, FolderOpen, Globe2, Layers3, LoaderCircle, Menu, MessageSquare, Plus, RefreshCw, Search, Settings2, ShieldCheck, LogIn, LogOut, UserPlus, Sparkles, Square, Trash2, X } from 'lucide-vue-next'
import { api, headers, subscribe, terminal, type Run, type Source, type EventItem } from './api'
import { completeLogin, currentUser, login, logout, register } from './auth'

type View = 'research'|'documents'|'memories'|'settings'
const view = ref<View>('research'), navOpen = ref(false)
const threads = ref<any[]>([]), threadId = ref(localStorage.getItem('dr-thread') || '')
const runs = ref<Run[]>([]), selected = ref<Run|null>(null), events = ref<EventItem[]>([])
const topic = ref(''), mode = ref('auto'), error = ref(''), busy = ref(false)
const status = ref<any>(null), documents = ref<any[]>([]), memories = ref<any[]>([]), metrics = ref<any>(null)
const preference = ref(''), editingMemory = ref(''), source = ref<Source|null>(null)
const selectedDocument = ref<any|null>(null), documentChunks = ref<any[]>([]), documentQuery = ref(''), documentResults = ref<any[]>([]), documentSearchBusy = ref(false)
const userId = ref('')
const authenticated = ref(false), threadSwitchId = ref(localStorage.getItem('dr-thread') || '')
const connection = ref<Record<string,any>|null>(null), checking = ref(false), uploadBusy = ref(false)
const uploadInput = ref<HTMLInputElement|null>(null), logOpen = ref(false), rename = ref(false), newTitle = ref('')
let controller: AbortController|null = null, poll: ReturnType<typeof setInterval>|undefined, generation = 0
const active = computed(()=>runs.value.some(run=>!terminal(run.status)))
const currentTitle = computed(()=>threads.value.find(t=>t.id===threadId.value)?.title || '研究工作台')
const viewTitle = computed(()=>({research: currentTitle.value, documents:'本地资料库', memories:'研究记忆', settings:'工作台设置'}[view.value]))
const conversationHistory = computed(()=>runs.value.filter(run=>run.id!==selected.value?.id))
function renderReport(report: string){return DOMPurify.sanitize(marked.parse(report,{async:false}) as string, {FORBID_TAGS:['img','style','iframe','form'], ADD_ATTR:['target']})}
const reportHtml = computed(()=>renderReport(selected.value?.report || ''))
const localSourceCount = computed(()=>selected.value?.sources.filter(item=>item.kind==='local').length || 0)
const plan = computed(()=>events.value.find(e=>e.type==='plan')?.data)
const nodes = [ ['router','识别需求'], ['planner','制定计划'], ['web_scout','网络检索'], ['local_scout','本地检索'], ['judge','筛选证据'], ['analyst','综合分析'], ['reflect','反思补搜'], ['writer','报告写作'], ['validator','引用核查'] ]
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
async function beginLogin(){ await login() }
async function beginRegistration(){ await register() }
function signOut(){ logout() }
function handleAuthExpired(){
  controller?.abort(); generation++; authenticated.value=false; userId.value=''; status.value=null
  threads.value=[]; runs.value=[]; selected.value=null; events.value=[]; threadId.value=''
  localStorage.removeItem('dr-thread'); error.value='登录已过期，请重新登录'
}
async function refreshThreads(){threads.value=await api('/api/threads')}
async function refreshStatus(){status.value=await api('/api/status')}
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
function newResearch(){controller?.abort();generation++;threadId.value='';threadSwitchId.value='';localStorage.removeItem('dr-thread');runs.value=[];selected.value=null;events.value=[];view.value='research';navOpen.value=false;topic.value='';error.value=''}
async function send() {
  if(!topic.value.trim() || busy.value || active.value)return
  busy.value=true
  await safely(async()=>{
    const run=await api<Run>('/api/research/runs',{method:'POST',body:JSON.stringify({topic:topic.value.trim(),mode:mode.value,thread_id:threadId.value||null,client_request_id:crypto.randomUUID()})})
    threadId.value=run.thread_id;localStorage.setItem('dr-thread',run.thread_id);runs.value.push(run);topic.value=''
    await refreshThreads();threadSwitchId.value=threads.value.find(thread=>thread.id===run.thread_id)?.thread_key||run.thread_id;await selectRun(run)
  });busy.value=false
}
async function cancel(){if(selected.value)await safely(async()=>{selected.value=await api(`/api/research/runs/${selected.value!.id}/cancel`,{method:'POST'});await refreshRun(selected.value!.id,generation)})}
async function resume(){if(selected.value)await safely(async()=>{const run=await api<Run>(`/api/research/runs/${selected.value!.id}/resume`,{method:'POST'});const i=runs.value.findIndex(r=>r.id===run.id);runs.value[i]=run;await selectRun(run)})}
async function navigate(next: View){view.value=next;navOpen.value=false;error.value='';await safely(async()=>{if(next==='documents')documents.value=await api('/api/documents');if(next==='memories')memories.value=await api('/api/memories');if(next==='settings'){await refreshStatus();metrics.value=await api('/api/metrics')}})}
async function upload(event: Event) {
  const input=event.target as HTMLInputElement
  if(!input.files?.length)return
  uploadBusy.value=true
  await safely(async()=>{for(const file of Array.from(input.files!)){const data=new FormData();data.append('file',file);await api('/api/documents',{method:'POST',body:data})}documents.value=await api('/api/documents')})
  input.value='';uploadBusy.value=false
}
async function deleteDocument(doc: any){await safely(async()=>{await api(`/api/documents/${doc.id}`,{method:'DELETE'});documents.value=await api('/api/documents')})}
async function inspectDocument(doc: any){await safely(async()=>{selectedDocument.value=doc;documentChunks.value=await api(`/api/documents/${doc.id}/chunks?limit=200`)})}
async function reindexDocument(doc: any){await safely(async()=>{await api(`/api/documents/${doc.id}/reindex`,{method:'POST'});documents.value=await api('/api/documents');if(selectedDocument.value?.id===doc.id)documentChunks.value=[]})}
async function searchDocuments(){if(!documentQuery.value.trim())return;documentSearchBusy.value=true;await safely(async()=>{documentResults.value=await api('/api/documents/search',{method:'POST',body:JSON.stringify({query:documentQuery.value.trim(),limit:6})})});documentSearchBusy.value=false}
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
async function saveMarkdown(path: string, filename: string){const response=await fetch(path,{headers:headers()});if(!response.ok)throw new Error('下载失败');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=filename;a.click();URL.revokeObjectURL(url)}
async function downloadRun(run: Run){await safely(async()=>{await saveMarkdown(`/api/research/runs/${run.id}/report`,`research-${run.id.slice(0,8)}.md`)})}
async function downloadConversation(){if(!threadId.value)return;await safely(async()=>{await saveMarkdown(`/api/threads/${threadId.value}/report`,`conversation-${threadId.value.slice(0,8)}.md`)})}
async function saveRunMemory(){if(!selected.value)return;await safely(async()=>{await api(`/api/research/runs/${selected.value!.id}/memory`,{method:'POST'});memories.value=await api('/api/memories')})}
async function exportData(){await safely(async()=>{const response=await fetch('/api/data/export',{headers:headers()});if(!response.ok)throw new Error('导出失败');const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download='deepresearch-export.zip';a.click();URL.revokeObjectURL(url)})}
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
function inspectCitation(event: MouseEvent){const target=event.target as HTMLElement;const text=target.textContent||'';const id=text.match(/\[(W-[a-f0-9]+|L-[a-f0-9]+)\]/)?.[1];if(id)source.value=selected.value?.sources.find(s=>s.id===id)||null}
onMounted(async()=>{
  window.addEventListener('deepresearch-auth-expired', handleAuthExpired)
  await safely(async()=>{const user=await completeLogin() || currentUser();if(!user)return;authenticated.value=true;userId.value=user.name;await refreshThreads();await refreshStatus();if(threadId.value&&threads.value.some(t=>t.id===threadId.value))await openThread(threadId.value);else newResearch()})
  poll=setInterval(()=>{if(view.value==='documents'&&documents.value.some(d=>d.status==='indexing'))void safely(async()=>{documents.value=await api('/api/documents')});if(selected.value&&!terminal(selected.value.status))void refreshRun(selected.value.id,generation).catch(()=>{})},2500)
})
onUnmounted(()=>{controller?.abort();clearInterval(poll);window.removeEventListener('deepresearch-auth-expired', handleAuthExpired)})
</script>

<template>
  <section v-if="!authenticated" class="auth-landing">
    <div class="auth-card"><div class="auth-mark"><Layers3 :size="30"/></div><span class="eyebrow"><span></span> DEEPRESEARCH WORKSPACE</span><h1>让研究有据可循。</h1><p>登录后开始研究、保存资料，并保留可追溯的证据链。</p><button class="auth-primary" @click="safely(beginLogin)"><LogIn :size="18"/>登录并开始研究</button><button class="auth-secondary" @click="safely(beginRegistration)"><UserPlus :size="17"/>创建本地账号</button><small>账号由本机 Keycloak 管理；注册后会自动回到工作台。</small></div>
  </section>
  <div v-else class="workspace">
    <div v-if="navOpen" class="nav-backdrop" @click="navOpen=false"></div>
    <aside class="sidebar" :class="{open:navOpen}">
      <a class="brand" href="#" @click.prevent="newResearch"><div class="brand-mark"><Layers3 :size="23"/></div><div>DeepResearch<span>让研究有据可循</span></div></a>
      <button class="new-button" @click="newResearch"><Plus :size="18"/> 新建研究 <span>＋</span></button>
      <nav>
        <button :class="{selected:view==='research'}" @click="navigate('research')"><Compass :size="18"/>研究工作台</button>
        <button :class="{selected:view==='documents'}" @click="navigate('documents')"><FolderOpen :size="18"/>本地资料库 <small v-if="documents.length">{{documents.length}}</small></button>
        <button :class="{selected:view==='memories'}" @click="navigate('memories')"><Brain :size="18"/>研究记忆</button>
      </nav>
      <div class="history-label">最近研究 <span>{{threads.length}}</span></div>
      <div class="thread-list"><p v-if="!threads.length" class="empty-history">你的研究会保存在这里</p><div v-for="thread in threads" :key="thread.id" :class="['thread-item',{chosen:thread.id===threadId}]"><button class="thread-select" @click="safely(()=>openThread(thread.id))"><MessageSquare :size="15"/><div><span>{{thread.title}}</span><small>{{thread.thread_key}}</small></div></button><button class="thread-delete icon-button" :aria-label="`删除研究 ${thread.title}`" title="删除研究" @click.stop="deleteThread(thread)"><Trash2 :size="15"/></button></div></div>
      <div class="sidebar-bottom"><div class="local-badge"><span class="status-dot"></span>本地工作空间 <ShieldCheck :size="14"/></div><button @click="navigate('settings')"><Settings2 :size="17"/>工作台设置</button><div class="profile"><div class="avatar">研</div><div>{{userId}}<small>本地演示用户</small></div></div></div>
    </aside>
    <main>
      <header class="topbar"><div class="breadcrumb"><button class="mobile-menu icon-button" aria-label="打开导航" @click="navOpen=true"><Menu :size="20"/></button><span>工作空间</span><ChevronRight :size="14"/><strong>{{viewTitle}}</strong></div><div class="top-status"><span :class="['status-dot',{'amber':!status||status.missing?.length}]"></span>{{status?.mode==='demo'?'测试模式':status?.missing?.length?'部分服务待配置':'已连接'}}<span class="divider"></span><span>本地部署</span></div><button v-if="!authenticated" class="text-button" @click="safely(beginLogin)"><LogIn :size="15"/>登录</button><button v-else class="text-button" @click="signOut"><LogOut :size="15"/>退出 {{userId}}</button></header>
      <div v-if="error" class="error-banner" role="alert">{{error}}<button aria-label="关闭提示" @click="error=''"><X :size="16"/></button></div>
      <div v-if="status?.mode==='demo'" class="demo-banner">当前为固定数据测试模式，报告用于验证功能，不代表真实研究结论。</div>

      <section v-if="view==='research'" class="research-view" :class="{hasRun:selected}">
        <template v-if="!selected">
          <div class="welcome"><span class="eyebrow"><span></span> YOUR RESEARCH, CONNECTED</span><h1>从一个问题，<br>到一份<span>有依据的洞察。</span></h1><p>连接网络与本地知识，让研究、分析和写作有序发生。<br class="desktop-break">你专注于问题，我们沿着证据寻找答案。</p></div>
          <div class="start-area">
            <div class="composer"><textarea v-model="topic" aria-label="研究主题" maxlength="4000" placeholder="你想研究什么？试着描述主题、时间范围和你关心的问题…" @keydown.enter.exact.prevent="send"></textarea><div class="composer-footer"><div class="mode-picker"><Sparkles :size="15"/><select v-model="mode" aria-label="研究模式"><option value="auto">自动选择</option><option value="deep">深度研究</option><option value="quick">快速问答</option></select></div><span class="composer-hint">Enter 发送 · Shift + Enter 换行</span><button class="send-button" aria-label="开始研究" :disabled="busy||!topic.trim()" @click="send"><LoaderCircle v-if="busy" class="spin" :size="20"/><ArrowUp v-else :size="21"/></button></div></div>
            <div class="capabilities"><span><Globe2 :size="14"/>网络 + 本地资料</span><span><ShieldCheck :size="14"/>证据与引用核查</span><span><Brain :size="14"/>持续研究记忆</span></div>
            <div class="examples-title">从这里开始探索 <span>为复杂问题找到清晰路径</span></div>
            <div class="examples"><button @click="useExample('请调研企业知识库 Agent 的应用场景、主要产品与部署方式，明确资料时间并提供来源。')"><div class="example-icon purple"><Globe2 :size="20"/></div><h3>行业深度调研<ArrowUpRight :size="16"/></h3><p>了解市场、主要参与者与发展方向，建立全局视角。</p><span>探索一个行业 <ArrowUpRight :size="13"/></span></button><button @click="useExample('请对比 RAG 与长上下文方案在企业知识问答中的适用场景、局限和选型依据，并提供来源。')"><div class="example-icon blue"><Layers3 :size="20"/></div><h3>方案对比分析<ArrowUpRight :size="16"/></h3><p>沿着相同维度比较方案，让关键差异更清楚。</p><span>比较不同方案 <ArrowUpRight :size="13"/></span></button><button @click="navigate('documents')"><div class="example-icon green"><BookOpen :size="20"/></div><h3>基于资料的研究<ArrowUpRight :size="16"/></h3><p>上传已有文档，连接你的知识，核查并补充信息。</p><span>添加本地资料 <ArrowUpRight :size="13"/></span></button></div>
          </div>
          <div class="home-note"><CircleHelp :size="14"/>研究结果受来源覆盖与时效影响，重要结论请查看原始证据。</div>
        </template>

        <template v-else>
          <div class="run-header"><div><span class="eyebrow">RESEARCH SESSION</span><h2>连续研究会话</h2><p v-if="conversationHistory.length" class="session-note">已保留 {{conversationHistory.length}} 条历史研究；继续提问会沿用当前会话。</p></div><button v-if="threadId" class="text-button" @click="downloadConversation"><Download :size="15"/>导出全部会话</button><button v-if="threadId" class="text-button" @click="newTitle=currentTitle;rename=true">重命名会话</button></div>
          <div v-if="rename" class="inline-edit"><input v-model="newTitle" maxlength="100" aria-label="会话名称"><button class="primary" @click="renameThread">保存</button><button @click="rename=false">取消</button></div>
          <section v-if="conversationHistory.length" class="conversation-history" aria-label="本会话的历史研究">
            <article v-for="run in conversationHistory" :key="run.id" class="conversation-turn">
              <div class="conversation-question"><MessageSquare :size="15"/><strong>你</strong><span>{{run.topic}}</span></div>
              <div class="conversation-response"><div class="conversation-response-heading"><FileText :size="15"/><strong>研究结果</strong><span>{{stateLabel(run.status)}}</span><button class="text-button" @click="selectRun(run)"><BookOpen :size="14"/>查看来源 {{run.sources.length}}</button><button v-if="run.report" class="text-button" @click="downloadRun(run)"><Download :size="14"/>导出</button></div><div v-if="run.report" class="conversation-markdown markdown" v-html="renderReport(run.report)"></div><p v-else class="muted">这次研究尚未生成可展示的报告。</p></div>
            </article>
          </section>
          <div class="current-turn"><MessageSquare :size="16"/><strong>你</strong><span>{{selected.topic}}</span></div>
          <div class="run-layout"><div class="report-column">
            <div class="progress-card"><div class="progress-heading"><div><LoaderCircle v-if="!terminal(selected.status)" class="spin" :size="18"/><Check v-else-if="selected.status==='completed'" :size="18"/><Compass v-else :size="18"/><strong>{{stateLabel(selected.status)}}</strong></div><button v-if="!terminal(selected.status)" class="text-button" @click="cancel"><Square :size="12"/>停止</button><button v-else-if="['failed','cancelled','interrupted'].includes(selected.status)" class="text-button" @click="resume">从检查点继续</button><button class="text-button" @click="logOpen=!logOpen">{{logOpen?'收起':'查看'}}过程</button></div><div class="node-track"><div v-for="node in nodes" :key="node[0]" :class="['node',nodeState(node[0])]"><i><Check v-if="nodeState(node[0])==='done'" :size="11"/><LoaderCircle v-else-if="nodeState(node[0])==='running'" class="spin" :size="11"/></i>{{node[1]}}</div></div><div v-if="plan&&!selected.report" class="plan-summary"><strong>{{plan.title}}</strong><p>{{plan.scope}}</p><ol><li v-for="question in plan.questions" :key="question">{{question}}</li></ol></div><div v-if="logOpen" class="event-log"><div v-for="event in events" :key="event.id"><time>{{new Date(event.created_at).toLocaleTimeString()}}</time><span v-if="event.type==='node_start'">{{nodeName(event.data.node)}}开始</span><span v-else-if="event.type==='node_end'">{{nodeName(event.data.node)}}完成 · {{event.data.duration_ms}} ms</span><span v-else-if="event.type==='reflection'">补搜：{{event.data.reason}}</span><span v-else-if="event.type==='sources_found'">{{event.data.kind==='web'?'网络':'本地'}}取得 {{event.data.count}} 条来源</span><span v-else>{{event.data.message||event.type}}</span></div></div></div>
            <div v-if="selected.error" class="inline-warning">{{selected.error}}</div><div v-if="selected.sources.some(item=>item.kind==='web'&&item.access==='summary')" class="inline-warning">此历史报告含未取得可读正文的候选链接，可能已失效、需要登录或需要付费。请重新运行后再将结论用于决策。</div><details v-if="warnings.length" class="warnings"><summary>{{warnings.length}} 条研究提示</summary><p v-for="warning in warnings" :key="warning">{{warning}}</p></details>
            <article v-if="selected.report" class="report-card"><div class="report-toolbar"><span><FileText :size="16"/>研究报告</span><div><button v-if="selected.status==='completed'" class="text-button" @click="saveRunMemory"><Brain :size="15"/>保存为语义记忆</button><button class="text-button" @click="downloadRun(selected)"><Download :size="15"/>导出 Markdown</button></div></div><div class="markdown" v-html="reportHtml" @click="inspectCitation"></div><div class="report-metrics"><span>引用检查 {{selected.validation.supported_claims||0}} / {{selected.validation.checked_claims||0}} 条结论</span><span>来源 {{selected.validation.evidence_metrics?.source_count||0}} · 域名 {{selected.validation.evidence_metrics?.unique_web_domains||0}}</span><span>模型调用 {{selected.usage.llm_calls||0}} 次</span><span>搜索 {{selected.usage.search_calls||0}} 次</span></div></article>
            <div v-if="!selected.report&&!terminal(selected.status)" class="working-placeholder"><div class="orb"><Search :size="26"/></div><h3>正在沿着证据展开研究</h3><p>检索、分析和核查需要一些时间。你可以离开页面，稍后继续查看。</p></div>
          </div><aside class="evidence-panel"><div class="panel-heading"><BookOpen :size="17"/><strong>研究来源</strong><span v-if="localSourceCount">本地资料 {{localSourceCount}}</span><span>{{selected.sources.length}}</span></div><p v-if="!selected.sources.length" class="muted">报告完成后，引用的资料将展示在这里。</p><button v-for="(item,index) in selected.sources" :key="item.id" class="source-card" @click="source=item"><small>{{String(index+1).padStart(2,'0')}} · {{item.kind==='web'?'网络资料':'本地资料库'}} · {{item.evidence_level||'secondary'}}</small><h4>{{item.title}}</h4><p>{{item.trust_label||item.domain||item.locator||(item.access==='summary'?'仅搜索摘要':'已读取正文')}}</p><span>查看证据片段 <ArrowUpRight :size="12"/></span></button><div class="source-note"><ShieldCheck :size="17"/><p>引用可追溯<br><span>重要结论需要正文与独立来源支持。</span></p></div></aside></div>
          <div class="followup composer"><textarea v-model="topic" aria-label="继续追问" maxlength="4000" :placeholder="active?'研究进行中，完成后可继续追问…':'继续追问，或进一步限定研究范围…'" @keydown.enter.exact.prevent="send"></textarea><div class="composer-footer"><div class="mode-picker"><select v-model="mode" aria-label="追问模式"><option value="auto">自动选择</option><option value="deep">深度研究</option><option value="quick">快速问答</option></select></div><span class="composer-hint">结合当前会话 · 重新核查证据</span><button class="send-button" aria-label="发送追问" :disabled="active||busy||!topic.trim()" @click="send"><ArrowUp :size="20"/></button></div></div>
        </template>
      </section>

      <section v-if="view==='documents'" class="utility-view"><span class="eyebrow">YOUR KNOWLEDGE</span><h1>把资料，变成研究的依据。</h1><p class="intro">导入你的行业报告和笔记。研究时会同时检索这些资料，并保留原文位置。</p><input ref="uploadInput" type="file" accept=".txt,.md,.pdf,.docx" multiple hidden @change="upload"><button class="upload-zone" :disabled="uploadBusy" @click="uploadInput?.click()"><FolderOpen :size="32"/><strong>{{uploadBusy?'正在上传…':'选择本地资料'}}</strong><span>TXT / Markdown / DOCX / 文字型 PDF · 每份不超过 10 MB</span><small>扫描件需先 OCR；向量化时，文档文字会发送至你配置的嵌入服务。</small></button><div class="section-heading"><h3>已导入资料</h3><span>{{documents.length}} 份</span></div><div v-if="!documents.length" class="empty-state"><BookOpen :size="28"/><p>还没有资料，添加一份文档开始吧。</p></div><div v-for="doc in documents" :key="doc.id" class="list-card"><div class="file-icon"><FileText :size="21"/></div><div class="list-content"><strong>{{doc.name}}</strong><small>{{new Date(doc.created_at).toLocaleString()}}</small><p v-if="doc.error" class="danger">{{doc.error}}</p></div><span :class="['pill',doc.status==='ready'?'green-pill':'']">{{({ready:'可检索',indexing:'正在建立索引',failed:'导入失败'} as any)[doc.status]}}</span><button class="text-button" :disabled="doc.status!=='ready'" @click="inspectDocument(doc)">片段</button><button class="icon-button" :aria-label="`重建 ${doc.name} 的索引`" :disabled="doc.status==='indexing'" @click="reindexDocument(doc)"><RefreshCw :size="16"/></button><button class="icon-button" :aria-label="`删除 ${doc.name}`" @click="deleteDocument(doc)"><Trash2 :size="17"/></button></div><div v-if="documents.length" class="settings-card retrieval-debug"><h3>检索调试工作台</h3><p class="muted">输入问题，查看本地资料的向量、BM25 与融合分数。</p><div class="inline-edit"><input v-model="documentQuery" placeholder="例如：企业私有化部署的优缺点" @keydown.enter.prevent="searchDocuments"><button class="primary" :disabled="documentSearchBusy||!documentQuery.trim()" @click="searchDocuments"><Search :size="15"/>{{documentSearchBusy?'检索中…':'调试检索'}}</button></div><div v-for="result in documentResults" :key="result.id" class="debug-result"><strong>{{result.title}}</strong><small>{{result.locator}}</small><p>融合 {{result.score}} · 向量 {{result.vector_score}} · BM25 {{result.bm25_score}}</p><span>{{result.text.slice(0,260)}}{{result.text.length>260?'…':''}}</span></div></div><div v-if="selectedDocument" class="settings-card chunk-view"><h3>{{selectedDocument.name}} 的分块</h3><p class="muted">{{documentChunks.length}} 个已加载片段，保留标题或页码与字符定位。</p><div v-for="(chunk,index) in documentChunks" :key="chunk.id" class="debug-result"><strong>Chunk {{index+1}}</strong><small>{{chunk.locator}}</small><span>{{chunk.text}}</span></div></div></section>

      <section v-if="view==='memories'" class="utility-view"><span class="eyebrow">CONTEXT THAT CONTINUES</span><h1>让下一次研究，更懂你。</h1><p class="intro">用户偏好和个人设定会在相同 User ID 的 Thread 间共享；历史研究摘要只在保存它的 Thread 内用于找回上下文，不作为新事实的依据。</p><div class="memory-editor"><label for="preference">{{editingMemory?'编辑偏好':'添加一条研究偏好'}}</label><textarea id="preference" v-model="preference" maxlength="1000" placeholder="例如：优先关注中国市场，报告中明确资料年份，使用中文输出。"></textarea><button class="primary" :disabled="!preference.trim()" @click="saveMemory">{{editingMemory?'保存修改':'保存偏好'}}</button><button v-if="editingMemory" class="text-button" @click="editingMemory='';preference=''">取消编辑</button></div><div v-if="!memories.length" class="empty-state"><Brain :size="28"/><p>还没有保存的记忆。</p></div><div v-for="memory in memories" :key="memory.id" class="memory-card"><div class="memory-top"><span class="pill">{{memory.kind==='preference'?'用户偏好':memory.kind==='profile'?'个人设定':'历史研究摘要'}}</span><div><button v-if="memory.kind==='preference'" class="text-button" @click="editingMemory=memory.id;preference=memory.content">编辑</button><button class="icon-button" aria-label="删除记忆" @click="deleteMemory(memory.id)"><Trash2 :size="16"/></button></div></div><p>{{memory.content}}</p><small>{{new Date(memory.created_at).toLocaleString()}}</small></div></section>

      <section v-if="view==='settings'" class="utility-view"><span class="eyebrow">WORKSPACE SETTINGS</span><h1>你的研究工作空间。</h1><p class="intro">API 密钥在本地 .env 文件配置，网页不会接收或显示模型密钥。</p><div class="settings-card"><h3>服务连接</h3><dl><dt>对话模型</dt><dd>{{status?.llm_model}}</dd><dt>嵌入模型</dt><dd>{{status?.embedding_model}}</dd><dt>向量维度</dt><dd>{{status?.embedding_dimension}}</dd><dt>资料索引</dt><dd>{{status?.milvus==='ready'?'可用':'尚未连接'}}</dd></dl><p v-if="status?.missing?.length" class="inline-warning">待配置：{{status.missing.join('、')}}</p><button class="primary" :disabled="checking" @click="checkConnection"><LoaderCircle v-if="checking" class="spin" :size="15"/>{{checking?'正在测试…':'测试模型连接'}}</button><small class="block muted">会发送少量测试文字，产生少量 API 调用。</small><pre v-if="connection" class="connection-result">{{JSON.stringify(connection,null,2)}}</pre></div><div class="settings-card"><h3>本地研究指标</h3><dl><dt>完成 / 资料不足</dt><dd>{{metrics?.completed||0}} / {{metrics?.insufficient||0}}</dd><dt>平均来源 / 域名</dt><dd>{{metrics?.avg_sources||0}} / {{metrics?.avg_web_domains||0}}</dd><dt>P50 / P95 节点时延</dt><dd>{{metrics?.node_latency_ms?.p50||0}} / {{metrics?.node_latency_ms?.p95||0}} ms</dd><dt>Token</dt><dd>{{(metrics?.prompt_tokens||0)+(metrics?.completion_tokens||0)}}</dd></dl></div><div class="settings-card"><h3>本地数据</h3><button class="primary" @click="exportData"><Download :size="15"/>导出工作空间</button><button class="text-button" @click="purgeData('reports')">清理研究记录</button><button class="text-button" @click="purgeData('all')">清理全部本地数据</button></div><div v-if="!authenticated" class="settings-card"><h3>演示工作空间</h3><p class="muted">User ID 用于隔离资料、记忆和会话；每个用户的新会话自动命名为 thread01、thread02。填入 Thread ID 可直接打开已有会话，留空则切换到新的研究页面。</p><label>User ID<input v-model.trim="userId" maxlength="64" placeholder="例如：user01"></label><label>Thread ID（可选）<input v-model.trim="threadSwitchId" maxlength="32" placeholder="例如：thread01"></label><p v-if="threadId" class="muted">当前 Thread ID：<code>{{threadSwitchId}}</code></p><button class="primary" @click="saveSettings">切换工作空间</button></div></section>
    </main>
    <div v-if="source" class="drawer-backdrop" @click.self="source=null"><aside class="source-drawer"><button class="icon-button close-drawer" aria-label="关闭来源" @click="source=null"><X :size="20"/></button><span class="eyebrow">SOURCE EVIDENCE</span><h2>{{source.title}}</h2><div class="source-meta"><span>{{source.id}}</span><span>{{source.kind==='local'?'本地资料库':source.access==='summary'?'仅搜索摘要':'已读取正文'}}</span></div><p v-if="source.locator">{{source.locator}}</p><p v-if="source.published_at" class="muted">发布时间：{{source.published_at}}</p><p class="muted">取得时间：{{new Date(source.retrieved_at).toLocaleString()}}</p><a v-if="source.url && source.access!=='summary'" :href="source.url" target="_blank" rel="noopener noreferrer" class="source-link">打开原始来源 <ArrowUpRight :size="15"/></a><p v-else-if="source.kind==='web'" class="inline-warning">该候选地址未取得可读正文，可能已失效、需要登录或需要付费。新版研究不会将它作为引用。</p><h3>用于研究的原文片段</h3><div class="source-excerpt">{{source.text}}</div></aside></div>
  </div>
</template>

<style>
.auth-landing{min-height:100vh;display:grid;place-items:center;padding:24px;background:radial-gradient(circle at 20% 15%,#e7e7ff 0,transparent 31%),linear-gradient(135deg,#f7f8ff,#eef2f9)}
.auth-card{width:min(460px,100%);padding:46px;border:1px solid #e1e3ee;border-radius:24px;background:rgba(255,255,255,.94);box-shadow:0 24px 60px rgba(55,61,104,.13);text-align:center}.auth-mark{width:62px;height:62px;margin:0 auto 22px;display:grid;place-items:center;border-radius:18px;background:#5957a7;color:#fff}.auth-card h1{margin:13px 0 12px;font-size:32px;color:#272a45}.auth-card p{margin:0 0 28px;color:#6f7488;line-height:1.7}.auth-card small{display:block;margin-top:18px;color:#9296a7;line-height:1.55}.auth-primary,.auth-secondary{width:100%;display:flex;justify-content:center;align-items:center;gap:8px;padding:13px;border-radius:10px;font-weight:700}.auth-primary{background:#5957a7;color:#fff}.auth-secondary{margin-top:10px;border:1px solid #d9dcea;color:#454962;background:#fff}

.thread-item{display:flex;align-items:center;border-radius:7px}
.thread-item .thread-select{width:100%;min-width:0;display:flex;align-items:center;text-align:left;gap:9px;padding:10px 12px;border-radius:7px;color:#7a8196;font-size:12px}
.thread-item .thread-select span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.thread-item .thread-select>div{min-width:0;display:grid;gap:2px}.thread-item .thread-select small{font-size:9px;color:#a2a7b5}
.thread-item .thread-select:hover,.thread-item.chosen .thread-select{background:#e9ebf4;color:#4b4a6a}
.thread-item .thread-delete{width:28px;height:28px;margin-right:4px;opacity:0}
.thread-item:hover .thread-delete,.thread-item.chosen .thread-delete{opacity:.7}
.thread-item .thread-delete:hover{opacity:1!important;background:#f7e8e8;color:#b36262}
</style>
