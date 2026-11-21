<script setup lang="ts">
import { computed, ref } from 'vue'
import { api, type ClaimLink, type Coverage, type CoverageItem } from './api'

const props = defineProps<{ coverage: Coverage; runId: string; canWork: boolean; policy?:string; failed?:boolean }>()
const emit = defineEmits<{
  claim:[id:string]; supplement:[id:string, query:string, url?:string]; documents:[id:string]; adjust:[]
}>()
const editor = ref<{id:string; query:string; url:string; includeUrl:boolean; loading:boolean; error:string}>()
const labels = { answered:'已回答', partial:'部分回答', insufficient:'证据不足' } as const
const claims = computed(() => new Map(props.coverage.claim_links?.map(claim => [claim.id, claim])))
const linked = (item:CoverageItem) => item.claim_ids.map(id => claims.value.get(id)).filter(
  (claim): claim is ClaimLink => !!claim)
const supplement = computed(() => props.coverage.supplement)
const updated = computed(() => props.coverage.requirements?.find(item => item.id === supplement.value?.target_id))
async function openEditor(id:string, includeUrl = false) {
  editor.value = {id, query:'', url:'', includeUrl, loading:true, error:''}
  const current = editor.value
  try {
    const {query} = await api<{query:string}>(
      '/api/research/runs/' + encodeURIComponent(props.runId) + '/supplement-query?requirementId=' + encodeURIComponent(id))
    current.query = query
  } catch (error) {
    current.error = error instanceof Error ? error.message : '暂时无法加载检索词'
  } finally {
    current.loading = false
  }
}
function submitSupplement() {
  const current = editor.value
  if (!props.canWork || !current || current.loading || !current.query.trim()) return
  emit('supplement', current.id, current.query.trim(), current.includeUrl ? current.url.trim() : undefined)
}
function provideSource(id:string) {
  if (props.policy !== 'public') return emit('documents', id)
  void openEditor(id, true)
}
</script>

<template>
  <section v-if="coverage.requirements?.length || coverage.unanswered_questions?.length || failed || canWork"
    class="report-coverage" aria-label="答题覆盖">
    <h3>答题覆盖</h3>
    <p v-if="supplement?.merged" class="coverage-result" role="status">
      补查已合入草稿。<template v-if="updated && supplement.previous_status">
        {{labels[supplement.previous_status]}} → {{labels[updated.status]}}。</template>
      保留 {{supplement.retained_claim_ids?.length || 0}} 条结论，
      新增 {{supplement.added_claim_ids?.length || 0}} 条结论和 {{supplement.added_source_ids?.length || 0}} 份来源。
    </p>
    <p v-else-if="supplement || failed" class="inline-warning" role="status">原结果仍可查看。该项尚待补查。</p>
    <ul v-if="coverage.requirements?.length" class="coverage-list">
      <li v-for="item in coverage.requirements" :key="item.id" :data-requirement="item.id" tabindex="-1">
        <div class="coverage-title"><strong>{{item.text}}</strong>
          <span :class="['pill', { 'green-pill': item.status === 'answered' }]">{{labels[item.status]}}</span></div>
        <p v-if="item.gap">{{item.gap}}</p>
        <div v-if="item.claim_ids.length" class="coverage-actions">
          <button v-for="(claim, index) in linked(item)" :key="claim.id" class="text-button"
            :title="claim.text" @click="emit('claim', claim.id)">查看依据 {{index + 1}}</button>
        </div>
        <div v-if="canWork && item.status !== 'answered'" class="coverage-actions">
          <button class="text-button" @click="openEditor(item.id)">仅补查此项</button>
          <button class="text-button" @click="provideSource(item.id)">补充资料</button>
          <button class="text-button" @click="emit('adjust')">调整范围</button>
        </div>
        <form v-if="editor?.id === item.id" class="coverage-editor" @submit.prevent="submitSupplement">
          <p v-if="editor.loading" role="status">正在准备检索词…</p>
          <p v-else-if="editor.error" class="inline-warning" role="alert">{{editor.error}}</p>
          <label v-else>检索词 <textarea v-model="editor.query" rows="2" maxlength="1000" required></textarea></label>
          <label v-if="editor.includeUrl">公开来源 URL <input v-model="editor.url" type="url" maxlength="2048" required></label>
          <div class="coverage-actions">
            <button class="primary" type="submit" :disabled="!canWork || editor.loading || !editor.query.trim()">
              {{editor.includeUrl ? '使用此来源补查' : '开始补查'}}
            </button>
            <button class="text-button" type="button" @click="editor = undefined">取消</button>
          </div>
        </form>
      </li>
    </ul>
    <template v-else-if="coverage.unanswered_questions?.length">
      <p>历史结果仅记录了以下未答项。</p>
      <ul><li v-for="question in coverage.unanswered_questions" :key="question">{{question}}</li></ul>
      <button v-if="canWork" class="text-button" @click="emit('adjust')">重新研究</button>
    </template>
    <template v-else-if="canWork">
      <p>历史结果未保存逐项覆盖。</p>
      <button class="text-button" @click="emit('adjust')">重新研究</button>
    </template>
  </section>
</template>
