<script setup lang="ts">
import { onUnmounted, ref, watch } from 'vue'
import { api } from './api'

const props = defineProps<{ modelValue: string[]; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string[]]; busy: [boolean] }>()
type Item = { key: string; id?: string; name: string; url: string; error: string; loading: boolean }
const items = ref<Item[]>([]), input = ref<HTMLInputElement|null>(null), error = ref('')
let disposed = false
const sync = () => { emit('update:modelValue', items.value.flatMap(i=>i.id?[i.id]:[])); emit('busy', items.value.some(i=>i.loading||!!i.error)) }
watch(()=>props.modelValue, ids=>{
  if(!ids.length && items.value.some(i=>i.id) && !items.value.some(i=>i.loading)) {
    items.value.forEach(i=>URL.revokeObjectURL(i.url)); items.value=[]; emit('busy',false)
  }
})
async function add(files: File[]) {
  if(props.disabled)return
  error.value=''
  for(const file of files) {
    if(items.value.length>=4) { error.value='每轮最多 4 张图片'; break }
    if(!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)||file.size>10*1024*1024) {
      error.value='仅支持 JPEG、PNG、WebP、静态 GIF，每张不超过 10 MB'; continue
    }
    const item = {key:crypto.randomUUID(),name:file.name,url:URL.createObjectURL(file),error:'',loading:true} as Item
    items.value.push(item); sync()
    const data=new FormData(); data.append('file',file)
    try {
      const uploaded=await api<{id:string}>('/api/attachments',{method:'POST',body:data})
      if(disposed) { await api(`/api/attachments/${uploaded.id}`,{method:'DELETE'}); return }
      const current=items.value.find(i=>i.key===item.key)!
      current.id=uploaded.id; current.loading=false
    } catch(e) { const current=items.value.find(i=>i.key===item.key); if(current){current.loading=false;current.error=e instanceof Error?e.message:'上传失败'} }
    sync()
  }
}
async function remove(item: Item) {
  if(item.loading||props.disabled)return
  try { if(item.id)await api(`/api/attachments/${item.id}`,{method:'DELETE'}) }
  catch(e){error.value=e instanceof Error?e.message:'删除失败';return}
  URL.revokeObjectURL(item.url);items.value=items.value.filter(i=>i.key!==item.key);sync()
}
function choose(event:Event){const target=event.target as HTMLInputElement;void add(Array.from(target.files||[]));target.value=''}
function paste(event:ClipboardEvent){const files=Array.from(event.clipboardData?.files||[]);if(files.length){event.preventDefault();void add(files)}}
function drop(event:DragEvent){event.preventDefault();void add(Array.from(event.dataTransfer?.files||[]))}
defineExpose({paste,drop})
onUnmounted(()=>{disposed=true;items.value.forEach(i=>URL.revokeObjectURL(i.url));emit('update:modelValue',[]);emit('busy',false)})
</script>
<template>
  <div class="image-attachments">
    <input ref="input" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple hidden @change="choose">
    <button class="text-button" type="button" :disabled="disabled||items.length>=4" @click="input?.click()">＋ 添加图片</button>
    <span class="muted">可粘贴或拖入 · 最多 4 张 · 每张 10 MB</span>
    <div class="attachment-previews"><div v-for="item in items" :key="item.key" class="attachment-preview">
      <img :src="item.url" :alt="item.name"><span>{{item.loading?'上传并扫描中…':item.error||item.name}}</span>
      <button type="button" :disabled="disabled||item.loading" :aria-label="`移除 ${item.name}`" @click="remove(item)">移除</button>
    </div></div>
    <p v-if="error" role="alert" class="danger">{{error}}</p>
  </div>
</template>
