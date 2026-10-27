<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api} from './api'
type Scope={scope:string;calls:number;reserved_tokens:number;pending_tokens:number;unknown_tokens:number;actual_input:number;actual_output:number;remaining_calls:number;remaining_token_exposure:number;call_limit:number;token_limit:number}
const usage=ref<{period_end:number;scopes:Scope[]}|null>(null),busy=ref(false),error=ref('')
async function refresh(){busy.value=true;error.value='';try{usage.value=await api('/api/usage')}catch(cause){error.value=cause instanceof Error?cause.message:'读取失败'}finally{busy.value=false}}
onMounted(refresh)
</script>
<template><section class="settings-card"><h3>调用额度与用量</h3><p class="muted">所有真实模型、嵌入和重排调用共享周期额度。额度由平台运维配置；累计预留用于追踪风险，不能当作供应商账单或金额。</p><button class="text-button" :disabled="busy" @click="refresh">{{busy?'读取中…':'刷新用量'}}</button><p v-if="error" class="inline-warning" role="alert">{{error}}</p><template v-if="usage"><p class="muted">当前周期结束于 {{new Date(usage.period_end*1000).toLocaleString()}}</p><div v-for="row in usage.scopes" :key="row.scope" class="debug-result"><strong>{{({personal:'我的账号',workspace:'当前工作空间',platform:'平台'} as Record<string,string>)[row.scope]}}</strong><dl><dt>请求次数</dt><dd>{{row.calls}} / {{row.call_limit}}</dd><dt>已知 Token</dt><dd>输入 {{row.actual_input}} · 输出 {{row.actual_output}}</dd><dt>累计预留 Token</dt><dd>{{row.reserved_tokens}}</dd><dt>待结算 / 未知 Token</dt><dd>{{row.pending_tokens}} / {{row.unknown_tokens}}</dd><dt>剩余可用请求 / Token 风险额度</dt><dd>{{row.remaining_calls}} / {{row.remaining_token_exposure}}</dd></dl></div></template></section></template>
