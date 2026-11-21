<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from './api'

defineProps<{ policy:string }>()
type Quota = {
  scopes:{ scope:string; remaining_calls:number; remaining_token_exposure:number }[]
  run_limits?:{ calls:number; reserved_tokens:number; searches:number; seconds:number }
  processing?:{ internal_model_allowed:boolean; reranker_enabled:boolean; demo_mode:boolean }
}
const quota = ref<Quota|null>(null)
const personal = computed(() => quota.value?.scopes.find(scope => scope.scope === 'personal'))
const unavailable = ref(false)
const refreshed = ref('')
async function load() {
  try {
    quota.value = await api<Quota>('/api/usage')
    refreshed.value = new Date().toLocaleTimeString()
    unavailable.value = false
  } catch {
    quota.value = null
    unavailable.value = true
  }
}
onMounted(load)
</script>
<template>
  <details class="research-preflight" @toggle="event => { if ((event.target as HTMLDetailsElement).open) load() }">
    <summary>资料处理与额度<template v-if="personal"> · 个人剩余 {{personal.remaining_calls}} 次</template>
      <template v-if="policy === 'internal' && quota?.processing?.internal_model_allowed && !quota.processing.demo_mode">
        · 内部片段可能交给模型处理</template></summary>
    <p v-if="policy === 'public'">研究问题、生成的检索词与公开网页片段可能由配置的搜索或模型服务处理。图片附件可能由配置的视觉模型处理。</p>
    <p v-else-if="quota?.processing?.demo_mode">当前使用固定数据夹具。</p>
    <p v-else-if="quota?.processing?.internal_model_allowed">
      内部资料不做网页搜索。问题、匹配的内部片段、会话上下文和图片可能由配置的模型服务处理。
      查询与资料文本可能用于嵌入<template v-if="quota.processing.reranker_enabled">和重排</template>。
    </p>
    <p v-else-if="quota?.processing">当前配置禁止由外部模型处理内部资料。</p>
    <p v-else>内部资料不做网页搜索。外部模型处理权限待查询。</p>
    <ul v-if="quota">
      <li v-for="scope in quota.scopes" :key="scope.scope">
        {{scope.scope === 'personal' ? '个人' : scope.scope === 'workspace' ? '工作空间' : '平台'}}
        剩余 {{scope.remaining_calls.toLocaleString()}} 次 · {{scope.remaining_token_exposure.toLocaleString()}} Token
      </li>
      <li v-if="quota.run_limits">本次最多 {{quota.run_limits.calls}} 次调用 ·
        预留 {{quota.run_limits.reserved_tokens.toLocaleString()}} Token · {{quota.run_limits.searches}} 次搜索 ·
        {{Math.ceil(quota.run_limits.seconds / 60)}} 分钟</li>
    </ul>
    <p v-if="refreshed && quota" class="muted">额度查询于 {{refreshed}}，以执行时为准。</p>
    <p v-if="unavailable" role="status">暂时无法读取额度。<button class="text-button" type="button" @click="load">刷新</button></p>
  </details>
</template>
