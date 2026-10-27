<script setup lang="ts">
import {ref,watch} from 'vue'
import {api} from './api'
const props=defineProps<{reportId:string;canWrite:boolean}>()
const emit=defineEmits<{changed:[]}>()
type Check={source_id:string;status:string;checked_at:string;old_hash:string;new_hash:string}
const rows=ref<Check[]>([]),busy=ref(false),error=ref('')
const label=(status:string)=>({not_checked:'本轮时间已用尽，尚未检查该来源',unchanged:'获取的正文未变化',changed:'正文已变化，建议发起修订',unavailable:'当前无法访问，不代表来源不存在',comparison_unavailable:'原证据仅为摘要，需要重新研究'}[status]||status)
let generation=0
watch(()=>props.reportId,async id=>{const epoch=++generation;rows.value=[];error.value='';try{const result=await api<Check[]>(`/api/reports/${id}/source-rechecks`);if(epoch===generation)rows.value=result}catch(cause){if(epoch===generation)error.value=cause instanceof Error?cause.message:'读取失败'}},{immediate:true})
async function check(){const id=props.reportId,epoch=generation;busy.value=true;error.value='';try{const result=await api<{checks:Check[]}>(`/api/reports/${id}/source-rechecks`,{method:'POST'});if(epoch===generation){rows.value=result.checks;if(result.checks.some(row=>row.status==='changed'))emit('changed')}}catch(cause){if(epoch===generation)error.value=cause instanceof Error?cause.message:'检查失败'}finally{if(epoch===generation)busy.value=false}}
</script>
<template><section class="settings-card"><h3>公开来源时效</h3><p class="muted">手动重新获取公开网页，比较保存正文。变化不等于事实错误；正式报告和历史证据不会被覆盖，需重新研究后审核发布。</p><button v-if="canWrite" class="text-button" :disabled="busy" @click="check">{{busy?'正在获取来源…':'重新获取公开来源'}}</button><p v-if="error" class="inline-warning" role="alert">{{error}}</p><p v-if="!rows.length" class="muted">暂无重核验记录。原始来源的获取时间可在引用中查看。</p><div v-for="(row,index) in rows" :key="`${row.source_id}:${row.checked_at}:${index}`" class="debug-result"><strong>{{row.source_id}} · {{label(row.status)}}</strong><small>{{new Date(row.checked_at).toLocaleString()}}</small><details><summary>正文摘要指纹</summary><p>原指纹：{{row.old_hash}}</p><p>新指纹：{{row.new_hash||'未取得正文'}}</p></details></div></section></template>
