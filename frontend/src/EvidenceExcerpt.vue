<script setup lang="ts">
import { ArrowUpRight } from 'lucide-vue-next'
import type { Source } from './api'
import AttachmentImage from './AttachmentImage.vue'

defineProps<{ source:Source; compare?:boolean }>()
</script>
<template>
  <section class="evidence-excerpt">
    <h3 v-if="compare">{{source.title}}</h3>
    <div class="source-meta"><span>{{source.kind === 'attachment' ? '本轮图片' :
      source.kind === 'local' ? '本地资料库' : source.access === 'summary' ? '仅搜索摘要' : '已读取正文'}}</span></div>
    <p v-if="source.index_version && source.index_version > 0">资料版本 {{source.index_version}}</p>
    <p v-if="source.locator">{{source.locator}}</p>
    <p v-if="source.published_at" class="muted">发布时间：{{source.published_at}}</p>
    <p v-if="source.retrieved_at" class="muted">取得时间：{{new Date(source.retrieved_at).toLocaleString()}}</p>
    <a v-if="source.url && /^https?:\/\//i.test(source.url) && source.access !== 'summary'" :href="source.url"
      target="_blank" rel="noopener noreferrer" class="source-link">打开原始来源 <ArrowUpRight :size="15"/></a>
    <p v-else-if="source.kind === 'web' && source.access === 'summary'" class="inline-warning">此候选地址只有搜索摘要。</p>
    <p v-if="source.original_available === false" class="inline-warning">当前原文不可访问，以下为保存时的片段。</p>
    <AttachmentImage v-if="source.attachment_id" :id="source.attachment_id" :name="source.title"/>
    <component :is="compare ? 'h4' : 'h3'">{{source.kind === 'attachment' ? '图片识别内容（未经外部核实）' : '用于研究的原文片段'}}</component>
    <div class="source-excerpt">{{source.text}}</div>
    <details class="technical-details source-technical"><summary>来源技术信息（标识、系统分类）</summary>
      <p>来源 ID：<code>{{source.id}}</code></p><p>系统分类：{{source.evidence_level || '未记录'}}</p>
    </details>
  </section>
</template>
