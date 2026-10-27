import { ref, watch, type Ref } from 'vue'
import { api } from './api'

export type LibraryDocument = {id:string;name:string;status:string;error?:string;index_version:number;pending_version:number;created_at:string;updated_at:string}
type Chunk = {id:string;locator:string;text:string;ordinal:number}
type Hit = Chunk & {title:string;score:number;vector_score:number;bm25_score:number;rerank_score:number|null;ranking_stage:string}
type Safe = (operation:()=>Promise<void>)=>Promise<void>

/** Library state is scoped to one workspace; a late response cannot populate the next workspace. */
export function useDocumentLibrary(workspace:Ref<{id?:string}|null>, safely:Safe) {
 const documents=ref<LibraryDocument[]>([]),selectedDocument=ref<LibraryDocument|null>(null),documentChunks=ref<Chunk[]>([])
 const documentQuery=ref(''),documentResults=ref<Hit[]>([]),documentReasons=ref<string[]>([]),documentSearchBusy=ref(false)
 const versionDocument=ref<{id:string;name:string}|null>(null),uploadBusy=ref(false),uploadInput=ref<HTMLInputElement|null>(null)
 const replacementInput=ref<HTMLInputElement|null>(null),replacementTarget=ref<LibraryDocument|null>(null),replacingId=ref('')
 let epoch=0
 watch(()=>workspace.value?.id,()=>{epoch++;documents.value=[];selectedDocument.value=null;versionDocument.value=null;documentChunks.value=[];documentResults.value=[];documentReasons.value=[];uploadBusy.value=false;documentSearchBusy.value=false})
 const scope=()=>({epoch,id:workspace.value?.id||''})
 const current=(token:ReturnType<typeof scope>)=>token.epoch===epoch&&token.id===(workspace.value?.id||'')
 const request=<T>(token:ReturnType<typeof scope>,path:string,init:RequestInit={})=>api<T>(path,{...init,headers:{...Object.fromEntries(new Headers(init.headers)),...(token.id?{'X-Workspace-ID':token.id}:{})}})
 async function guarded(operation:(token:ReturnType<typeof scope>)=>Promise<void>){const token=scope();await safely(async()=>{try{await operation(token)}catch(error){if(current(token))throw error}})}
 async function refresh(token:ReturnType<typeof scope>){const result=await request<LibraryDocument[]>(token,'/api/documents');if(current(token))documents.value=result}
 function resetDocument(id:string){if(selectedDocument.value?.id===id){selectedDocument.value=null;documentChunks.value=[]}if(versionDocument.value?.id===id)versionDocument.value=null}
 async function upload(event:Event){const input=event.target as HTMLInputElement;if(!input.files?.length)return;const files=Array.from(input.files);uploadBusy.value=true;await guarded(async token=>{try{for(const file of files){if(!current(token))return;const data=new FormData();data.append('file',file);await request(token,'/api/documents',{method:'POST',body:data})}await refresh(token)}finally{if(current(token))uploadBusy.value=false}});input.value=''}
 async function deleteDocument(doc:LibraryDocument){await guarded(async token=>{await request(token,`/api/documents/${doc.id}`,{method:'DELETE'});if(current(token))resetDocument(doc.id);await refresh(token)})}
 function chooseReplacement(doc:LibraryDocument){replacementTarget.value=doc;replacementInput.value?.click()}
 async function replaceDocument(event:Event){const input=event.target as HTMLInputElement,doc=replacementTarget.value,file=input.files?.[0];if(!doc||!file)return;replacingId.value=doc.id;await guarded(async token=>{try{const data=new FormData();data.append('file',file);await request(token,`/api/documents/${doc.id}`,{method:'PUT',body:data});if(current(token))resetDocument(doc.id);await refresh(token)}finally{if(current(token)){replacingId.value='';replacementTarget.value=null}}});input.value=''}
 async function inspectDocument(doc:LibraryDocument){await guarded(async token=>{const chunks=await request<Chunk[]>(token,`/api/documents/${doc.id}/chunks?limit=400`);if(current(token)){selectedDocument.value=doc;documentChunks.value=chunks}})}
 async function reindexDocument(doc:LibraryDocument){await guarded(async token=>{await request(token,`/api/documents/${doc.id}/reindex`,{method:'POST'});if(current(token))resetDocument(doc.id);await refresh(token)})}
 async function searchDocuments(){if(!documentQuery.value.trim())return;documentSearchBusy.value=true;documentResults.value=[];documentReasons.value=[];await guarded(async token=>{try{const result=await request<{results:Hit[];reasons:string[]}>(token,'/api/documents/search?diagnostics=true',{method:'POST',body:JSON.stringify({query:documentQuery.value.trim(),limit:6})});if(current(token)){documentResults.value=result.results;documentReasons.value=result.reasons}}finally{if(current(token))documentSearchBusy.value=false}})}
 return {documents,selectedDocument,documentChunks,documentQuery,documentResults,documentReasons,documentSearchBusy,versionDocument,uploadBusy,uploadInput,replacementInput,replacementTarget,replacingId,upload,deleteDocument,chooseReplacement,replaceDocument,inspectDocument,reindexDocument,searchDocuments}
}
