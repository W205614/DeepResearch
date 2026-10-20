<script setup lang="ts">
import { ref, watch } from 'vue'
import { api } from './api'
import type { DocumentVersion, DocumentDiff } from './lifecycle'
const props = defineProps<{ documentId: string; name: string }>()
const versions = ref<DocumentVersion[]>([]), diff = ref<DocumentDiff|null>(null), from = ref(0), to = ref(0), error = ref(''), busy = ref(false)
let generation = 0
watch(() => props.documentId, async id => {
  const epoch = ++generation
  versions.value = []; diff.value = null; error.value = ''; busy.value = true
  try {
    const result = await api<DocumentVersion[]>(`/api/documents/${encodeURIComponent(id)}/versions`)
    if (epoch !== generation) return
    versions.value = [...result].sort((a, b) => b.version - a.version)
    to.value = versions.value[0]?.version || 0; from.value = versions.value[1]?.version || to.value
  } catch (e) { if (epoch === generation) error.value = e instanceof Error ? e.message : '版本读取失败' }
  finally { if (epoch === generation) busy.value = false }
}, { immediate: true })
async function compare() {
  const epoch = generation
  busy.value = true; error.value = ''; diff.value = null
  try {
    const result = await api<DocumentDiff>(`/api/documents/${encodeURIComponent(props.documentId)}/diff?from=${from.value}&to=${to.value}`)
    if (epoch === generation) diff.value = result
  } catch (e) { if (epoch === generation) error.value = e instanceof Error ? e.message : '差异读取失败' }
  finally { if (epoch === generation) busy.value = false }
}
</script>
<template>
  <section class="settings-card lifecycle-panel" aria-label="资料版本历史">
    <h3>{{ name }} · 版本历史</h3>
    <p class="muted">文字变化不等于业务结论变化，请结合适用条件核对原文。</p>
    <p v-if="error" class="error-banner" role="alert">{{ error }}</p>
    <p v-if="busy" role="status">正在读取版本…</p>
    <p v-else-if="!versions.length && !error" class="muted">尚无可读取的版本档案。</p>
    <div v-for="version in versions" :key="version.version" class="debug-result">
      <strong>版本 {{ version.version }}{{ version.current ? ' · 当前有效' : '' }}</strong>
      <small>{{ version.name }} · {{ version.chunk_count }} 个片段 · {{ new Date(version.created_at).toLocaleString() }}</small>
      <details class="technical-details"><summary>版本指纹与配置</summary><p>{{ version.hash }}</p><pre>{{ JSON.stringify({ parser: version.parser_config, index: version.index_config }, null, 2) }}</pre></details>
    </div>
    <div v-if="versions.length > 1" class="lifecycle-controls">
      <label>原资料版本<select v-model.number="from" :disabled="busy"><option v-for="v in versions" :key="v.version" :value="v.version">版本 {{ v.version }}</option></select></label>
      <label>新资料版本<select v-model.number="to" :disabled="busy"><option v-for="v in versions" :key="v.version" :value="v.version">版本 {{ v.version }}</option></select></label>
      <button class="primary" :disabled="busy || from === to" @click="compare">比较资料版本</button>
    </div>
    <template v-if="diff">
      <p v-if="!diff.changes.length" class="muted">这两个版本未发现文字差异。</p>
      <p v-if="diff.truncated" class="inline-warning">差异过长，当前仅展示部分内容。</p>
      <article v-for="(change, index) in diff.changes" :key="index" class="lifecycle-diff">
        <strong>{{ ({ added: '新增', deleted: '删除', modified: '修改', unmapped: '无法可靠对应' } as Record<string,string>)[change.kind] || change.kind }}</strong>
        <div class="lifecycle-columns"><div v-if="change.old"><small>原版 · {{ change.old.locator }}</small><p>{{ change.old.text }}</p></div><div v-if="change.new"><small>新版 · {{ change.new.locator }}</small><p>{{ change.new.text }}</p></div></div>
      </article>
    </template>
  </section>
</template>
