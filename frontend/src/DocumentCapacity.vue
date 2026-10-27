<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api} from './api'
type Capacity={documents:number;chunks:number;document_limit:number;chunk_limit:number;max_file_bytes:number;max_chunks_per_document:number}
const capacity=ref<Capacity|null>(null),error=ref('')
onMounted(async()=>{try{capacity.value=await api('/api/documents/capacity')}catch{error.value='资料容量暂时无法读取'}})
</script>
<template><p v-if="error" class="inline-warning">{{error}}</p><details v-if="capacity" class="settings-card"><summary>工作空间资料容量</summary><p class="muted">容量按整个工作空间计算。达到上限时会拒绝新增内容，已有资料仍可检索。</p><dl><dt>文档数量</dt><dd>{{capacity.documents}} / {{capacity.document_limit}}</dd><dt>可检索内容片段</dt><dd>{{capacity.chunks}} / {{capacity.chunk_limit}}</dd><dt>每份文件大小上限</dt><dd>{{capacity.max_file_bytes/1024/1024}} MB</dd><dt>每份文件片段上限</dt><dd>{{capacity.max_chunks_per_document}}</dd></dl></details></template>
