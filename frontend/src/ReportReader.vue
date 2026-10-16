<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, useId, watch } from 'vue'

// The parent supplies the existing sanitized report. Only reading aids are added;
// report text, citations and the exported Markdown remain untouched.
const props = defineProps<{ html: string }>()
const emit = defineEmits<{ inspect: [event: MouseEvent] }>()
const prefix = useId().replace(/[^a-zA-Z0-9-]/g, '')
const contents = ref<HTMLDetailsElement | null>(null)
const reader = ref<HTMLElement | null>(null)
let resizeObserver: ResizeObserver | undefined
const documentView = computed(() => {
  const template = document.createElement('template')
  template.innerHTML = props.html
  const headings = Array.from(template.content.querySelectorAll('h2, h3')).map((heading, index) => {
    if (!heading.id) heading.id = `report-${prefix}-${index}`
    heading.setAttribute('tabindex', '-1')
    return { id: heading.id, text: heading.textContent || '', nested: heading.tagName === 'H3' }
  })
  template.content.querySelectorAll('table').forEach(table => {
    const wrapper = document.createElement('div')
    wrapper.className = 'report-table'
    wrapper.tabIndex = 0
    wrapper.setAttribute('role', 'region')
    wrapper.setAttribute('aria-label', '报告表格，可横向滚动')
    table.replaceWith(wrapper)
    wrapper.append(table)
  })
  template.content.querySelectorAll('pre').forEach(block => {
    block.tabIndex = 0
    block.setAttribute('aria-label', '代码或原文，可横向滚动')
  })
  return { html: template.innerHTML, headings }
})

function markWideTables() {
  reader.value?.querySelectorAll<HTMLElement>('.report-table').forEach(table => {
    table.classList.toggle('is-wide', table.scrollWidth > table.clientWidth)
  })
}
onMounted(() => {
  resizeObserver = new ResizeObserver(markWideTables)
  if (reader.value) resizeObserver.observe(reader.value)
  markWideTables()
})
watch(documentView, markWideTables, { flush: 'post' })
onUnmounted(() => resizeObserver?.disconnect())

function jump(index: number) {
  // Scope by position so pre-existing or repeated author IDs cannot send the
  // reader to a different report. Preserve author IDs for existing report links.
  const heading = reader.value?.querySelectorAll<HTMLElement>('.markdown h2, .markdown h3')[index]
  if (!heading) return
  if (contents.value) contents.value.open = false
  heading.focus({ preventScroll: true })
  heading.scrollIntoView({ block: 'start' })
}
</script>

<template>
  <div ref="reader" class="report-reader">
    <details v-if="documentView.headings.length >= 3" ref="contents" class="report-contents">
      <summary>报告目录 <span>{{ documentView.headings.length }} 节 · 跳转阅读</span></summary>
      <nav aria-label="报告目录">
        <a v-for="(heading, index) in documentView.headings" :key="`${heading.id}-${index}`" :href="`#${encodeURIComponent(heading.id)}`"
          :class="{ nested: heading.nested }" @click.prevent="jump(index)">{{ heading.text }}</a>
      </nav>
    </details>
    <div class="markdown" v-html="documentView.html" @click="emit('inspect', $event)"></div>
  </div>
</template>
