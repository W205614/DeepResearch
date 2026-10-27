<script setup lang="ts">
import {onMounted,ref} from 'vue'
import {api} from './api'
type Collection={id:string;name:string;visibility:'workspace'|'restricted'}
const props=defineProps<{admin:boolean;documents:{id:string;name:string}[]}>()
const emit=defineEmits<{changed:[]}>()
const collections=ref<Collection[]>([]),name=ref(''),visibility=ref('restricted'),collection=ref(''),subject=ref(''),permission=ref('read'),document=ref(''),error=ref(''),notice=ref(''),busy=ref(false)
async function load(){collections.value=await api<Collection[]>('/api/collections')}
onMounted(()=>load().catch(()=>{error.value='集合列表暂时无法读取'}))
async function save(action:'create'|'grant'|'assign'){busy.value=true;error.value='';notice.value='';try{
 if(action==='create'){await api('/api/collections',{method:'POST',body:JSON.stringify({name:name.value,visibility:visibility.value})});name.value='';await load()}
 if(action==='grant')await api(`/api/collections/${collection.value}/grants`,{method:'PUT',body:JSON.stringify({subject:subject.value.trim(),permission:permission.value})})
 if(action==='assign')await api(`/api/documents/${document.value}/collection`,{method:'PUT',body:JSON.stringify({collection_id:collection.value})})
 notice.value='已保存。新的检索和访问将按当前权限检查；已下载的内容无法远程收回。';emit('changed')
 }catch(cause){error.value=cause instanceof Error?cause.message:'保存失败'}finally{busy.value=false}}
</script>
<template><details class="settings-card"><summary>资料集合与访问权限</summary><p class="muted">未分组资料继承工作空间权限。受限集合只允许资料所有者、工作空间管理员和获授权成员访问；正式报告快照保持原样，读取仍受当前资料权限控制。</p><p v-if="!collections.length" class="muted">暂无可见集合。</p><p v-for="item in collections" :key="item.id">{{item.name}} · {{item.visibility==='restricted'?'受限集合':'空间共享'}}</p><template v-if="props.admin"><form @submit.prevent="save('create')"><label>集合名称<input v-model.trim="name" required maxlength="100"></label><select v-model="visibility" aria-label="新集合访问范围"><option value="restricted">受限集合</option><option value="workspace">空间共享</option></select><button class="text-button" :disabled="busy||!name">创建集合</button></form><label>选择集合<select v-model="collection" aria-label="选择集合"><option value="">继承空间权限</option><option v-for="item in collections" :key="item.id" :value="item.id">{{item.name}}</option></select></label><form @submit.prevent="save('grant')"><label>成员账号 ID<input v-model.trim="subject" required maxlength="128"></label><select v-model="permission" aria-label="成员集合权限"><option value="read">读取</option><option value="write">读取与修改</option><option value="none">撤销集合授权</option></select><button class="text-button" :disabled="busy||!collection||!subject">保存成员权限</button></form><form @submit.prevent="save('assign')"><label>分配资料<select v-model="document" aria-label="分配资料" required><option value="">请选择资料</option><option v-for="doc in props.documents" :key="doc.id" :value="doc.id">{{doc.name}}</option></select></label><button class="text-button" :disabled="busy||!document">将资料分配到所选集合</button></form></template><p v-if="notice" role="status">{{notice}}</p><p v-if="error" class="inline-warning" role="alert">{{error}}</p></details></template>
