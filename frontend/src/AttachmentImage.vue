<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import { authenticatedFetch } from './api'
const props=defineProps<{id:string; name?:string}>()
const url=ref(''),error=ref('')
let revision=0
watch(()=>props.id,async id=>{
  const current=++revision
  if(url.value)URL.revokeObjectURL(url.value)
  url.value='';error.value=''
  try {
    const response=await authenticatedFetch(`/api/attachments/${id}`)
    if(!response.ok)throw new Error('图片不可访问')
    const blob=await response.blob()
    if(current===revision)url.value=URL.createObjectURL(blob)
  } catch { if(current===revision)error.value='图片不可访问' }
},{immediate:true})
onUnmounted(()=>{revision++;if(url.value)URL.revokeObjectURL(url.value)})
</script>
<template><figure class="saved-attachment"><a v-if="url" :href="url" target="_blank" rel="noopener"><img :src="url" :alt="name||'用户图片'"></a><figcaption>{{error||name}}</figcaption></figure></template>
