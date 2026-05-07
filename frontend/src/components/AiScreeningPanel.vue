<template>
  <div class="ai-screening-panel" v-loading="loading">
    <!-- idle: 配置 -->
    <div v-if="status === 'idle'" class="config-block">
      <el-alert v-if="eligibleCount === 0" type="warning" :closable="false" show-icon>
        候选池为空。请先在「匹配候选人」Tab 跑硬筛, 让候选人通过硬门槛。
      </el-alert>
      <el-alert v-else type="info" :closable="false" show-icon>
        候选池: 硬筛通过 <b>{{ eligibleCount }}</b> 人 (已排除拒绝)。AI 会横向对比并 0-100 打分。
      </el-alert>

      <el-form label-width="100px" style="margin-top: 16px;">
        <el-form-item label="筛选方式">
          <el-radio-group v-model="form.mode">
            <el-radio value="count">指定人数</el-radio>
            <el-radio value="ratio">通过比例</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="form.mode === 'count' ? '通过人数' : '通过比例'">
          <el-input-number
            v-model="form.threshold"
            :min="1"
            :max="thresholdMax"
            :step="1"
          />
          <span style="margin-left: 8px; color: #909399;">
            {{ form.mode === 'count' ? `(1..${eligibleCount})` : '%(1..100)' }}
          </span>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :disabled="eligibleCount === 0"
            @click="onStart"
            :loading="starting"
          >
            开始 AI 筛选
          </el-button>
        </el-form-item>
      </el-form>
      <el-collapse v-if="lastFinishedItems.length" style="margin-top: 16px;">
        <el-collapse-item title="上一次筛选结果" name="last">
          <ItemsTable :items="lastFinishedItems" :show-actions="false" />
        </el-collapse-item>
      </el-collapse>
    </div>

    <!-- running -->
    <div v-else-if="status === 'running'" class="running-block">
      <div class="status-line">
        <el-tag type="primary">分析中</el-tag>
        <span style="margin-left: 12px;">
          已分析 <b>{{ processed }}</b> / {{ total }}
        </span>
      </div>
      <el-progress
        :percentage="percent"
        :stroke-width="14"
        :format="() => `${processed}/${total}`"
        style="margin: 12px 0;"
      />
      <el-alert type="info" :closable="false">
        AI 正调用本地 Claude Code 横向对比简历, 多批分析可能 1-3 分钟。退出后回来仍可看到当前进度。
      </el-alert>
      <el-button
        type="danger"
        plain
        @click="onCancel"
        :loading="cancelling"
        style="margin-top: 12px;"
      >
        取消任务
      </el-button>
    </div>

    <!-- done -->
    <div v-else-if="status === 'done'" class="done-block">
      <div class="status-line">
        <el-tag type="success">完成</el-tag>
        <span style="margin-left: 12px;">
          共 <b>{{ total }}</b> 份简历, 通过 <b>{{ passCount }}</b> 份
        </span>
        <el-button size="small" style="margin-left: 16px;" @click="reset">
          重新筛选
        </el-button>
      </div>
      <ItemsTable
        :items="items"
        :show-actions="true"
        @decide="onDecide"
        style="margin-top: 12px;"
      />
    </div>

    <!-- failed / cancelled -->
    <div v-else class="error-block">
      <el-alert
        :title="status === 'cancelled' ? '已取消' : '任务失败'"
        :description="errorMsg"
        :type="status === 'cancelled' ? 'warning' : 'error'"
        :closable="false"
        show-icon
      />
      <el-button
        v-if="items.length"
        @click="showPartial = !showPartial"
        size="small"
        style="margin-top: 8px;"
      >
        {{ showPartial ? '隐藏' : '查看' }}部分结果 ({{ items.length }} 份)
      </el-button>
      <ItemsTable
        v-if="showPartial && items.length"
        :items="items"
        :show-actions="false"
        style="margin-top: 12px;"
      />
      <el-button type="primary" @click="reset" style="margin-top: 12px;">
        重新开始
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { aiScreeningApi, decisionApi } from '../api'
import ItemsTable from './AiScreeningItemsTable.vue'

const props = defineProps({
  jobId: { type: Number, required: true },
})

const status = ref('idle')   // idle | running | done | failed | cancelled
const loading = ref(false)
const starting = ref(false)
const cancelling = ref(false)
const showPartial = ref(false)

const eligibleCount = ref(0)
const screeningJobId = ref(null)
const total = ref(0)
const processed = ref(0)
const errorMsg = ref('')

const form = ref({ mode: 'count', threshold: 5 })
const items = ref([])
const lastFinishedItems = ref([])

let pollTimer = null

const percent = computed(() => {
  if (total.value === 0) return 0
  return Math.min(100, Math.round((processed.value / total.value) * 100))
})

const passCount = computed(() => items.value.filter((it) => it.pass_flag === 1).length)

const thresholdMax = computed(() => {
  if (form.value.mode === 'ratio') return 100
  return Math.max(1, eligibleCount.value)
})

watch(() => form.value.mode, (m) => {
  // BUG-109: mode 切换时重置 threshold 为典型默认值 (count→5, ratio→20),
  // 避免 "5 人" 切到 ratio 后仍是 5%, 含义彻底变。同时 clamp 不超过上限。
  const def = m === 'ratio' ? 20 : 5
  form.value.threshold = Math.min(def, thresholdMax.value)
})

async function loadPreview() {
  try {
    const r = await aiScreeningApi.preview(props.jobId)
    eligibleCount.value = r.eligible_count
    if (form.value.threshold > eligibleCount.value && form.value.mode === 'count') {
      form.value.threshold = Math.max(1, eligibleCount.value)
    }
  } catch (e) {
    console.error('preview failed', e)
  }
}

async function loadCurrent() {
  loading.value = true
  try {
    const r = await aiScreeningApi.current(props.jobId)
    if (r.status === 'idle') {
      status.value = 'idle'
      await loadPreview()
      return
    }
    screeningJobId.value = r.id
    total.value = r.total || 0
    processed.value = r.processed || 0
    errorMsg.value = r.error_msg || ''
    status.value = r.status
    if (r.status === 'running') {
      startPolling()
    } else if (r.status === 'done') {
      await loadItems()
    } else {
      // failed / cancelled — 拉部分结果方便查看
      await loadItems()
    }
    if (r.status !== 'running') {
      await loadPreview()
    }
  } catch (e) {
    console.error('load current failed', e)
    status.value = 'idle'
  } finally {
    loading.value = false
  }
}

async function loadItems() {
  if (!screeningJobId.value) return
  try {
    const r = await aiScreeningApi.items(screeningJobId.value)
    items.value = r.items || []
    // BUG-147: 仅 status='done' 时同步到 lastFinishedItems。idle 是 reset 后空状态,
    // 之前会把空 items 写进去, 导致折叠面板"上次筛选结果"丢失。
    if (status.value === 'done') {
      lastFinishedItems.value = items.value
    }
  } catch (e) {
    console.error('load items failed', e)
  }
}

function startPolling() {
  stopPolling()
  pollTimer = setInterval(async () => {
    // BUG-110: 标签页/窗口隐藏时跳过本轮请求, 避免不可见时仍每 2 秒打后端
    if (typeof document !== 'undefined' && document.hidden) return
    try {
      const r = await aiScreeningApi.current(props.jobId)
      if (r.status !== 'running') {
        stopPolling()
        screeningJobId.value = r.id
        total.value = r.total || 0
        processed.value = r.processed || 0
        errorMsg.value = r.error_msg || ''
        status.value = r.status
        await loadItems()
        if (r.status === 'done') {
          ElMessage.success(`AI 筛选完成: 通过 ${passCount.value} / ${r.total} 份`)
        } else if (r.status === 'failed') {
          ElMessage.error('AI 筛选失败: ' + (r.error_msg || '未知错误'))
        }
      } else {
        processed.value = r.processed || 0
        total.value = r.total || 0
      }
    } catch (e) {
      console.error('poll error', e)
    }
  }, 2000)
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// BUG-110: 页面 visibility 切换时, 隐藏暂停 polling, 显示恢复 (若仍 running)
function onVisibilityChange() {
  if (typeof document === 'undefined') return
  if (document.hidden) {
    stopPolling()
  } else if (status.value === 'running' && !pollTimer) {
    startPolling()
  }
}

// BUG-149: axios 错误兜底 — 区分网络中断 vs 后端报错, 给用户友好文案而不是
// "Request failed with status code 500" 这种内部细节。
function _friendlyErrorMsg(e, fallback = '操作失败') {
  // 后端有结构化 detail 时优先
  const detail = e?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && detail.error) return detail.error
  // 后端响应了但无 detail
  if (e?.response?.status) {
    const s = e.response.status
    if (s === 401) return '登录已过期, 请重新登录'
    if (s === 403) return '没有权限执行此操作'
    if (s === 404) return '请求的资源不存在'
    if (s === 409) return '操作冲突, 请刷新后重试'
    if (s >= 500) return '服务器内部错误, 请稍后重试或联系管理员'
    return `${fallback} (HTTP ${s})`
  }
  // 完全没有响应 — 网络层面错误
  if (e?.message?.includes('Network')) return '网络异常, 请检查连接后重试'
  return fallback
}

async function onStart() {
  starting.value = true
  try {
    const r = await aiScreeningApi.start(props.jobId, { ...form.value })
    screeningJobId.value = r.screening_job_id
    status.value = 'running'
    processed.value = 0
    // BUG-148: 用 backend 返的 total (权威值), 而不是 stale 的本地 eligibleCount;
    // 兼容老接口若没返 total 时仍用本地值。
    total.value = (typeof r.total === 'number' && r.total > 0) ? r.total : eligibleCount.value
    errorMsg.value = ''
    items.value = []
    startPolling()
    ElMessage.success('已启动 AI 筛选')
  } catch (e) {
    if (e.response?.status === 503) {
      ElMessageBox.alert(
        '本地未检测到 Claude Code CLI。请先安装: npm i -g @anthropic-ai/claude-code, 或设置环境变量 CLAUDE_CLI_PATH 指向 claude 可执行文件。',
        '需要 Claude Code',
        { type: 'error' },
      )
    } else {
      ElMessage.error('启动失败: ' + _friendlyErrorMsg(e, '启动失败'))
    }
  } finally {
    starting.value = false
  }
}

async function onCancel() {
  cancelling.value = true
  try {
    const r = await aiScreeningApi.cancel(screeningJobId.value)
    // BUG-135: terminated=false 表示子进程未被立即杀, 需提示用户当前批次自然结束
    if (r && r.terminated === false) {
      ElMessage.warning('已请求取消, 当前批次将自然结束 (≤5 分钟), 之后停止')
    } else {
      ElMessage.info('已请求取消, 等待当前批次完成...')
    }
  } catch (e) {
    ElMessage.error('取消失败: ' + _friendlyErrorMsg(e, '取消失败'))
  } finally {
    cancelling.value = false
  }
}

async function onDecide(item, action) {
  // action: 'passed' | 'rejected' | null
  try {
    await decisionApi.set(props.jobId, item.candidate_id, action)
    item.decision_action = action
    ElMessage.success(action ? `已${action === 'passed' ? '通过' : '拒绝'}` : '已撤销')
  } catch (e) {
    ElMessage.error('操作失败: ' + _friendlyErrorMsg(e, '操作失败'))
  }
}

function reset() {
  status.value = 'idle'
  screeningJobId.value = null
  items.value = []
  errorMsg.value = ''
  loadPreview()
}

onMounted(() => {
  loadCurrent()
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onVisibilityChange)
  }
})

onUnmounted(() => {
  stopPolling()
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', onVisibilityChange)
  }
})
</script>

<style scoped>
.ai-screening-panel { padding: 0; }
.status-line { display: flex; align-items: center; }
.config-block, .running-block, .done-block, .error-block { padding: 8px 0; }
</style>
