<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, type Run, type ScopeDraft as Draft } from './api'
import ResearchPreflight from './ResearchPreflight.vue'
const props=defineProps<{draft:Draft|null; policy:string}>()
const emit=defineEmits<{created:[run:Run];closed:[]}>()
const current=ref<Draft|null>(null), saved=ref<Draft[]>([]),busy=ref(false),error=ref(''),notice=ref('')
const fields=ref({time:'',region:'',object:'',deliverable:''})
function open(draft:Draft){current.value=draft;error.value='';notice.value='';fields.value=draft.answers_json?JSON.parse(draft.answers_json):draft.suggestions?{...draft.suggestions}:{time:'',region:'',object:'',deliverable:''}}
watch(()=>props.draft,value=>{if(value)open(value)},{immediate:true})
onMounted(async()=>{try{const rows=await api<(Draft&{request_json:string})[]>('/api/research/intakes');saved.value=rows.map(row=>({...row,topic:JSON.parse(row.request_json).topic}))}catch{error.value='待确认研究暂时无法读取'}})
async function act(action:'save'|'confirm'|'cancel'){
 if(!current.value)return
 busy.value=true;error.value='';notice.value=''
 try{
  const id=current.value.id
  if(action==='save'){await api(`/api/research/intakes/${id}`,{method:'PUT',body:JSON.stringify(fields.value)});notice.value='范围已保存，确认前不会开始研究';return}
  if(action==='cancel')await api(`/api/research/intakes/${id}/cancel`,{method:'POST'})
  else{const run=await api<Run>(`/api/research/intakes/${id}/confirm`,{method:'POST',body:JSON.stringify(fields.value)});emit('created',run)}
  saved.value=saved.value.filter(row=>row.id!==id);current.value=null;emit('closed')
 }catch(cause){error.value=cause instanceof Error?cause.message:'操作失败'}finally{busy.value=false}
}
</script>
<template>
 <div v-if="saved.length&&!current" class="settings-card"><h3>待确认的研究</h3><button v-for="item in saved" :key="item.id" class="text-button" @click="open(item)">{{item.topic}}</button></div>
 <form v-if="current" class="settings-card research-scope" @submit.prevent="act('confirm')">
  <h3>先确认这次研究的范围</h3><p>{{current.topic}}</p><p class="muted">请补全会影响结果的范围。已填内容可直接确认。无限制的字段可选“不限定”。</p>
  <label>时间范围<input v-model.trim="fields.time" required maxlength="150" placeholder="例如：2024—2026年，或不限定"></label>
  <label>地区范围<input v-model.trim="fields.region" required maxlength="150" placeholder="例如：中国市场，或全球"></label>
  <label>研究对象<input v-model.trim="fields.object" required maxlength="150" placeholder="明确产品、人群或比较对象"></label>
  <label>交付标准<textarea v-model.trim="fields.deliverable" required maxlength="300" placeholder="需要回答什么、比较哪些方面、哪些条件必须说明"></textarea></label>
  <div class="coverage-actions"><button class="text-button" type="button" @click="fields.time='不限定'">时间不限定</button>
    <button class="text-button" type="button" @click="fields.region='不限定'">地区不限定</button></div>
  <ResearchPreflight :policy="current.data_policy || policy"/>
  <p class="muted">有效期至 {{new Date(current.expires_at*1000).toLocaleString()}}</p><p v-if="notice" role="status">{{notice}}</p><p v-if="error" class="inline-warning" role="alert">{{error}}</p>
  <button type="submit" class="primary" :disabled="busy">{{busy?'处理中…':'确认并开始研究'}}</button><button type="button" class="text-button" :disabled="busy" @click="act('save')">保存范围</button><button type="button" class="text-button" :disabled="busy" @click="act('cancel')">取消这次研究</button>
 </form>
</template>
