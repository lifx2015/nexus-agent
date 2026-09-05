<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import DOMPurify from 'dompurify'
import { marked } from 'marked'
import { assetsApi, discoveredApi, errorMessage } from '@/api/client'
import type { AssetKind } from '@/constants'

const props = defineProps<{
  source: 'own' | 'track'
  id: string | number
  name: string
  kind?: AssetKind
  agent?: string
  path?: string
}>()

const visible = defineModel<boolean>({ default: false })

const loading = ref(false)
const files = ref<Record<string, string>>({})
const activeFile = ref('')

marked.setOptions({ gfm: true, breaks: false })

const fileNames = computed(() =>
  Object.keys(files.value).sort((a, b) => {
    if (a === 'SKILL.md') return -1
    if (b === 'SKILL.md') return 1
    const rank = (n: string) => (n.toLowerCase().endsWith('.json') ? 2 : n.toLowerCase().endsWith('.md') ? 0 : 1)
    return rank(a) - rank(b) || a.localeCompare(b)
  }),
)

const activeRaw = computed(() => files.value[activeFile.value] ?? '')
const isMarkdown = computed(() => activeFile.value.toLowerCase().endsWith('.md'))

const isJson = computed(() => {
  if (isMarkdown.value) return false
  const t = activeRaw.value.trim()
  if (!t) return false
  if (activeFile.value.toLowerCase().endsWith('.json')) return true
  if (!(t.startsWith('{') || t.startsWith('['))) return false
  try {
    JSON.parse(t)
    return true
  } catch {
    return false
  }
})

/** 极简 JSON 高亮（先转义再着色，内容仅来自本地文件） */
function highlightJson(src: string): string {
  const esc = src.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  return esc.replace(
    /("(?:\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(?:\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (m) => {
      let cls = 'tok-num'
      if (m.startsWith('"')) cls = m.trimEnd().endsWith(':') ? 'tok-key' : 'tok-str'
      else if (/^(true|false|null)$/.test(m)) cls = 'tok-bool'
      return `<span class="${cls}">${m}</span>`
    },
  )
}

const codeHtml = computed(() => (isJson.value ? highlightJson(activeRaw.value) : ''))

const html = computed(() => {
  if (!isMarkdown.value) return ''
  const raw = marked.parse(activeRaw.value, { async: false }) as string
  return DOMPurify.sanitize(raw, { FORBID_TAGS: ['style', 'iframe', 'form', 'object'] })
})

watch(visible, async (v) => {
  if (!v) return
  loading.value = true
  files.value = {}
  activeFile.value = ''
  try {
    if (props.source === 'own' && props.kind === 'skill') {
      files.value = await assetsApi.skillFiles(String(props.id))
    } else if (props.source === 'own') {
      // 工具/记忆/规范：正文 Markdown + 元数据 JSON 两个视图
      const a = await assetsApi.get((props.kind ?? 'skill') as AssetKind, String(props.id))
      const f: Record<string, string> = { [`${a.name || '内容'}.md`]: a.body || '' }
      if (a.metadata && Object.keys(a.metadata).length) {
        f['元数据.json'] = JSON.stringify(a.metadata, null, 2)
      }
      files.value = f
    } else {
      const res = await discoveredApi.files(Number(props.id))
      files.value = res.files
    }
    activeFile.value = fileNames.value[0] ?? ''
  } catch (e) {
    ElMessage.error(errorMessage(e))
    visible.value = false
  } finally {
    loading.value = false
  }
})

async function openExternal(mode: 'file' | 'dir') {
  try {
    const res = await discoveredApi.open(Number(props.id), mode)
    ElMessage.success(`已打开：${res.opened}`)
  } catch (e) {
    ElMessage.error(errorMessage(e))
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    width="min(920px, 94vw)"
    top="4vh"
    class="nx-preview"
    append-to-body
  >
    <template #header>
      <div class="pv-head">
        <div class="pv-title">{{ name }}</div>
        <div class="pv-sub">
          <span v-if="agent" class="pv-agent">
            <span class="pv-dot" />{{ agent }}
          </span>
          <span v-else class="pv-agent pv-agent--own">自建</span>
          <span v-if="path" class="pv-path" :title="path">{{ path }}</span>
        </div>
      </div>
    </template>

    <div v-loading="loading" class="pv-body">
      <div v-if="fileNames.length > 1" class="pv-tabs">
        <button
          v-for="f in fileNames"
          :key="f"
          class="pv-tab"
          :class="{ active: f === activeFile }"
          @click="activeFile = f"
        >
          {{ f }}
        </button>
      </div>

      <div v-if="!activeFile && !loading" class="pv-empty">没有可预览的文本文件（可能是二进制或目录为空）</div>
      <div v-else-if="isMarkdown" class="md-body" v-html="html" />
      <pre v-else-if="isJson" class="pv-code pv-code--json" v-html="codeHtml" />
      <pre v-else class="pv-code"><code>{{ activeRaw }}</code></pre>
    </div>

    <template #footer>
      <el-button v-if="source === 'track'" @click="openExternal('dir')">打开所在目录</el-button>
      <el-button v-if="source === 'track'" @click="openExternal('file')">打开原文件</el-button>
      <el-button type="primary" @click="visible = false">关闭</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.pv-head {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-right: 28px;
}
.pv-title {
  font-size: 17px;
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.3;
}
.pv-sub {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}
.pv-agent {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
  height: 20px;
  padding: 0 8px;
  border-radius: 999px;
  font-size: 11px;
  color: var(--nx-text-dim);
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  white-space: nowrap;
}
.pv-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--nx-accent-2);
  flex: none;
}
.pv-agent--own {
  color: var(--nx-accent);
  background: var(--nx-accent-soft);
  border-color: transparent;
}
.pv-path {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  color: var(--nx-text-faint);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.pv-body {
  min-height: 320px;
  max-height: calc(88vh - 170px);
  overflow: auto;
  padding: 18px 24px 24px;
}

.pv-tabs {
  position: sticky;
  top: -18px;
  z-index: 2;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  padding: 12px 0;
  margin: -18px 0 14px;
  background: var(--nx-bg-elevated);
  border-bottom: 1px solid var(--nx-border-soft);
}
.pv-tab {
  appearance: none;
  border: 1px solid var(--nx-border-soft);
  background: var(--nx-bg-soft);
  color: var(--nx-text-dim);
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 11px;
  padding: 4px 10px;
  border-radius: 999px;
  cursor: pointer;
  transition: all 0.15s var(--nx-ease);
}
.pv-tab:hover {
  color: var(--nx-text);
  border-color: var(--nx-outline);
}
.pv-tab.active {
  color: var(--nx-accent);
  background: var(--nx-accent-soft);
  border-color: transparent;
}

.pv-code {
  margin: 0;
  padding: 16px;
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  border-radius: 10px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12px;
  line-height: 1.7;
  color: var(--nx-text-dim);
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.pv-code--json {
  background: #0b0f18;
  border-color: var(--nx-border);
  color: var(--nx-text);
}
/* JSON 语法高亮 token */
.pv-code--json :deep(.tok-key) {
  color: #7aa6ff;
}
.pv-code--json :deep(.tok-str) {
  color: #34d399;
}
.pv-code--json :deep(.tok-num) {
  color: #f5a623;
}
.pv-code--json :deep(.tok-bool) {
  color: #22d3ee;
  font-style: italic;
}

.pv-empty {
  padding: 64px 0;
  text-align: center;
  color: var(--nx-text-faint);
  font-size: 13px;
}

/* ---------- Markdown 正文排版 ---------- */
.md-body {
  font-size: 14px;
  line-height: 1.78;
  color: var(--nx-text);
  word-break: break-word;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 1.6em 0 0.6em;
  font-weight: 700;
  letter-spacing: -0.015em;
  line-height: 1.35;
  color: var(--nx-text);
}
.md-body :deep(h1:first-child),
.md-body :deep(h2:first-child),
.md-body :deep(h3:first-child) {
  margin-top: 0;
}
.md-body :deep(h1) {
  font-size: 23px;
  padding-bottom: 0.4em;
  border-bottom: 1px solid var(--nx-border-soft);
}
.md-body :deep(h2) {
  font-size: 19px;
  padding-bottom: 0.35em;
  border-bottom: 1px solid var(--nx-border-soft);
}
.md-body :deep(h3) {
  font-size: 16px;
}
.md-body :deep(h4) {
  font-size: 14px;
  color: var(--nx-text-dim);
}
.md-body :deep(p) {
  margin: 0.7em 0;
}
.md-body :deep(a) {
  color: var(--nx-accent);
  text-decoration: none;
}
.md-body :deep(a:hover) {
  text-decoration: underline;
}
.md-body :deep(strong) {
  font-weight: 650;
  color: #fff;
}
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 0.6em 0;
  padding-left: 1.5em;
}
.md-body :deep(li) {
  margin: 0.3em 0;
}
.md-body :deep(li::marker) {
  color: var(--nx-text-faint);
}
.md-body :deep(blockquote) {
  margin: 1em 0;
  padding: 10px 16px;
  border-left: 3px solid var(--nx-accent);
  border-radius: 0 8px 8px 0;
  background: var(--nx-accent-soft);
  color: var(--nx-text-dim);
}
.md-body :deep(blockquote p) {
  margin: 0.2em 0;
}
.md-body :deep(code) {
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 0.86em;
  padding: 2px 6px;
  border-radius: 5px;
  background: var(--nx-bg-soft);
  border: 1px solid var(--nx-border-soft);
  color: var(--nx-accent-2);
}
.md-body :deep(pre) {
  margin: 1em 0;
  padding: 14px 16px;
  border-radius: 10px;
  background: #0c1018;
  border: 1px solid var(--nx-border-soft);
  overflow-x: auto;
}
.md-body :deep(pre code) {
  padding: 0;
  background: none;
  border: none;
  color: var(--nx-text);
  font-size: 12.5px;
  line-height: 1.7;
}
.md-body :deep(table) {
  margin: 1em 0;
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.md-body :deep(th),
.md-body :deep(td) {
  padding: 8px 12px;
  border: 1px solid var(--nx-border-soft);
  text-align: left;
}
.md-body :deep(th) {
  background: var(--nx-bg-soft);
  font-weight: 600;
  color: var(--nx-text-dim);
  font-size: 12px;
}
.md-body :deep(tr:nth-child(2n) td) {
  background: rgba(255, 255, 255, 0.015);
}
.md-body :deep(hr) {
  margin: 1.8em 0;
  border: none;
  border-top: 1px solid var(--nx-border-soft);
}
.md-body :deep(img) {
  max-width: 100%;
  border-radius: 10px;
  border: 1px solid var(--nx-border-soft);
}
.md-body :deep(input[type='checkbox']) {
  accent-color: var(--nx-accent);
  margin-right: 4px;
}
</style>
