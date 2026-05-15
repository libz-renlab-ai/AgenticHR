<template>
  <div class="intake-page">
    <el-card shadow="never" class="automation-card" style="margin-bottom: 12px">
      <div class="automation-row">
        <div class="automation-target">
          <span class="label">目标候选人数</span>
          <el-input-number v-model="settingsForm.target_count" :min="0" :max="1000" :step="5"
                           controls-position="right" style="width: 140px" />
          <el-button size="small" type="primary" @click="saveTarget" :loading="savingTarget">
            保存
          </el-button>
        </div>
        <div class="automation-progress">
          <el-progress :percentage="progressPercent" :stroke-width="12"
                       :status="progressStatus"
                       :format="() => `${settings.complete_count} / ${settings.target_count}`" />
        </div>
        <div class="automation-action">
          <el-tag :type="runningTagType" style="margin-right: 8px">{{ runningText }}</el-tag>
          <el-button v-if="settings.enabled" type="warning" @click="toggleEnabled(false)"
                     :loading="togglingEnabled">暂停</el-button>
          <el-button v-else type="success" @click="toggleEnabled(true)"
                     :loading="togglingEnabled">开始</el-button>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" class="daily-cap-card">
      <div class="daily-cap">
        <span class="daily-cap-label">今日自动采集额度</span>
        <span class="daily-cap-value">
          <strong>{{ dailyCap.used }}</strong> / {{ dailyCap.cap }}
          <span class="daily-cap-remaining">（剩余 {{ dailyCap.remaining }}）</span>
        </span>
        <el-button size="small" link @click="loadDailyCap">刷新</el-button>
      </div>
    </el-card>

    <!-- 候选人列表 -->
    <el-card shadow="never">
      <div class="filter-bar">
        <el-select
          v-model="statusFilter"
          placeholder="全部状态"
          clearable
          style="width: 180px"
          @change="reload"
        >
          <el-option label="收集中" value="collecting" />
          <el-option label="等待回复" value="awaiting_reply" />
          <el-option label="待人工" value="pending_human" />
          <el-option label="已完成" value="complete" />
          <el-option label="已放弃" value="abandoned" />
          <el-option label="超时未回复" value="timed_out" />
        </el-select>
        <el-input
          v-model="search"
          placeholder="按姓名/Boss ID 搜索"
          clearable
          style="width: 240px; margin-left: 12px"
          @clear="reload"
          @keyup.enter="reload"
        />
        <el-button type="primary" size="default" style="margin-left: 12px" @click="reload">
          搜索
        </el-button>
        <el-button
          type="primary"
          size="default"
          style="margin-left: 12px"
          @click="onAiClassify"
          :loading="aiClassifying"
          :disabled="unmatchedCount === 0"
        >
          🤖 AI 分类目标岗位({{ unmatchedCount }} 个未分配)
        </el-button>
      </div>
      <el-alert
        v-if="lastClassifyResult"
        type="success"
        :closable="false"
        show-icon
        style="margin-bottom: 12px;"
      >
        分类完成: 共 {{ lastClassifyResult.total }} 人 →
        精确匹配 {{ lastClassifyResult.exact_matched }} 人,
        AI 判断 {{ lastClassifyResult.llm_matched }} 人,
        无匹配 {{ lastClassifyResult.no_match }} 人{{
          lastClassifyResult.errors ? `, 失败 ${lastClassifyResult.errors} 人` : ''
        }}
      </el-alert>

      <el-table
        :data="filteredItems"
        v-loading="loading"
        border
        row-key="resume_id"
        @expand-change="handleExpandChange"
      >
        <el-table-column type="expand">
          <template #default="{ row }">
            <SlotsPanel :resume-id="row.resume_id" />
          </template>
        </el-table-column>
        <el-table-column prop="name" label="姓名" min-width="120" />
        <el-table-column prop="boss_id" label="Boss ID" min-width="160" />
        <el-table-column prop="job_title" label="目标岗位" min-width="160">
          <template #default="{ row }">
            <span v-if="row.job_title">{{ row.job_title }}</span>
            <span v-else style="color: #c0c4cc">未匹配</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-select
              :model-value="row.intake_status"
              size="small"
              style="width: 130px"
              :loading="statusUpdating === row.resume_id"
              @change="(val) => doUpdateStatus(row, val)"
            >
              <el-option
                v-for="opt in STATUS_OPTIONS"
                :key="opt.value"
                :label="opt.label"
                :value="opt.value"
              />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="进度" width="160">
          <template #default="{ row }">
            <el-progress
              :percentage="progressPct(row)"
              :stroke-width="10"
              :format="() => `${row.progress_done}/${row.progress_total}`"
            />
          </template>
        </el-table-column>
        <el-table-column label="最近活动" width="170">
          <template #default="{ row }">
            {{ row.last_activity_at ? formatTime(row.last_activity_at) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="280" fixed="right">
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              @click="handleStartConversation(row)"
            >开始沟通</el-button>
            <el-button
              v-if="!['complete', 'abandoned'].includes(row.intake_status)"
              size="small"
              type="warning"
              link
              :loading="row._reextracting"
              @click="doReextract(row)"
            >重抽</el-button>
            <el-button
              v-if="!['complete', 'abandoned'].includes(row.intake_status)"
              size="small"
              type="success"
              link
              @click="doForceComplete(row)"
            >标记完成</el-button>
            <el-button
              v-if="!['abandoned', 'timed_out'].includes(row.intake_status)"
              size="small"
              type="danger"
              link
              @click="doAbandon(row)"
            >放弃</el-button>
            <el-button
              size="small"
              type="danger"
              link
              @click="doDelete(row)"
            >删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-pagination
        style="margin-top: 16px; justify-content: flex-end; display: flex"
        v-model:current-page="page"
        v-model:page-size="size"
        :total="total"
        :page-sizes="[20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        @current-change="loadCandidates"
        @size-change="loadCandidates"
      />
    </el-card>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import SlotsPanel from './SlotsPanel.vue'
import {
  listIntakeCandidates,
  updateStatus,
  abandonCandidate,
  forceComplete,
  deleteCandidate,
  startConversation,
  reextract,
  getDailyCap,
  batchClassify,
} from '../api/intake'
import { getIntakeSettings, updateIntakeSettings } from '../api/intakeSettings'
import { resumeApi } from '../api'

const STATUS_OPTIONS = [
  { value: 'collecting', label: '收集中' },
  { value: 'awaiting_reply', label: '等待回复' },
  { value: 'pending_human', label: '待人工' },
  { value: 'complete', label: '已完成' },
  { value: 'abandoned', label: '已放弃' },
  { value: 'timed_out', label: '超时未回复' },
]

const loading = ref(false)
const statusUpdating = ref(null)
const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(20)
const statusFilter = ref('')
const search = ref('')
const dailyCap = ref({ used: 0, cap: 0, remaining: 0 })

const settings = ref({ enabled: false, target_count: 0, complete_count: 0, is_running: false })
const settingsForm = ref({ target_count: 0 })
const savingTarget = ref(false)
const togglingEnabled = ref(false)

const aiClassifying = ref(false)
const lastClassifyResult = ref(null)
const unmatchedCount = computed(() =>
  (items.value || []).filter((it) => !it.job_id).length
)

async function onAiClassify() {
  aiClassifying.value = true
  lastClassifyResult.value = null
  try {
    const r = await batchClassify()
    lastClassifyResult.value = r
    ElMessage.success(
      `已完成: ${r.exact_matched + r.llm_matched}/${r.total} 个候选人已分配岗位`
    )
    await loadCandidates()
  } catch (e) {
    ElMessage.error('AI 分类失败: ' + (e.response?.data?.detail || e.message || '请重试'))
  } finally {
    aiClassifying.value = false
  }
}

const progressPercent = computed(() => {
  const t = settings.value.target_count
  if (!t) return 0
  return Math.min(100, Math.round((settings.value.complete_count / t) * 100))
})
const progressStatus = computed(() => {
  if (settings.value.target_count > 0 &&
      settings.value.complete_count >= settings.value.target_count) return 'success'
  return ''
})
const runningTagType = computed(() => {
  if (settings.value.is_running) return 'success'
  if (settings.value.target_count > 0 &&
      settings.value.complete_count >= settings.value.target_count) return 'info'
  return 'warning'
})
const runningText = computed(() => {
  if (settings.value.is_running) return '运行中'
  if (settings.value.target_count > 0 &&
      settings.value.complete_count >= settings.value.target_count) return '已达标'
  if (!settings.value.enabled) return '已暂停'
  return '未配置'
})

async function loadSettings() {
  try {
    const s = await getIntakeSettings()
    settings.value = s
    settingsForm.value.target_count = s.target_count
  } catch (e) {
    ElMessage.error('加载自动采集设置失败')
  }
}

async function saveTarget() {
  savingTarget.value = true
  try {
    const s = await updateIntakeSettings({ target_count: settingsForm.value.target_count })
    settings.value = s
    ElMessage.success('目标已保存')
  } catch (e) {
    ElMessage.error('保存失败')
  } finally {
    savingTarget.value = false
  }
}

async function toggleEnabled(on) {
  if (on && settings.value.target_count <= 0) {
    ElMessage.warning('请先设置目标候选人数（>0）')
    return
  }
  togglingEnabled.value = true
  try {
    const s = await updateIntakeSettings({ enabled: on })
    settings.value = s
    ElMessage.success(on ? '已开始自动采集' : '已暂停')
  } catch (e) {
    ElMessage.error('操作失败')
  } finally {
    togglingEnabled.value = false
  }
}

async function loadDailyCap() {
  try {
    dailyCap.value = await getDailyCap()
  } catch (e) {
    // Non-fatal; leave defaults
  }
}

const filteredItems = computed(() => {
  const kw = (search.value || '').trim().toLowerCase()
  if (!kw) return items.value
  return items.value.filter(
    (it) =>
      (it.name || '').toLowerCase().includes(kw) ||
      (it.boss_id || '').toLowerCase().includes(kw)
  )
})

function progressPct(row) {
  if (!row.progress_total) return 0
  return Math.round((row.progress_done / row.progress_total) * 100)
}

function statusTagType(s) {
  return {
    collecting: 'primary',
    awaiting_reply: 'warning',
    pending_human: 'danger',
    complete: 'success',
    abandoned: 'info',
    timed_out: 'danger',
  }[s] || ''
}

function statusText(s) {
  return {
    collecting: '收集中',
    awaiting_reply: '等待回复',
    pending_human: '待人工',
    complete: '已完成',
    abandoned: '已放弃',
    timed_out: '超时未回复',
  }[s] || s
}

function formatTime(t) {
  if (!t) return ''
  try {
    const d = new Date(t)
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch {
    return t
  }
}

async function loadCandidates() {
  loading.value = true
  try {
    const params = { page: page.value, size: size.value }
    if (statusFilter.value) params.status = statusFilter.value
    const res = await listIntakeCandidates(params)
    items.value = res.items || []
    total.value = res.total || 0
  } catch (e) {
    ElMessage.error('加载候选人列表失败')
  } finally {
    loading.value = false
  }
}

function reload() {
  page.value = 1
  loadCandidates()
}

function handleExpandChange() {
  // SlotsPanel mounts on expand and self-loads; nothing to do here.
}

async function doAbandon(row) {
  try {
    await ElMessageBox.confirm(`确定放弃候选人 ${row.name} 吗？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await abandonCandidate(row.resume_id)
    ElMessage.success('已放弃')
    loadCandidates()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function handleStartConversation(row) {
  try {
    const resp = await startConversation(row.resume_id)
    window.open(resp.deep_link, '_blank')
    ElMessage.info('已跳转 Boss 直聘，插件将自动接管')
  } catch (e) {
    ElMessage.error(`启动沟通失败: ${e.message || e}`)
  }
}

async function doDelete(row) {
  try {
    await ElMessageBox.confirm(`确定删除候选人 ${row.name}？此操作不可恢复。`, '确认删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    if (row.promoted_resume_id) {
      // 已完成采集 → 通过简历库删除（同时级联删除候选人记录）
      await resumeApi.delete(row.promoted_resume_id)
    } else {
      await deleteCandidate(row.resume_id)
    }
    ElMessage.success('已删除')
    loadCandidates()
  } catch (e) {
    const detail = e.response?.data?.detail || e.message || String(e)
    ElMessage.error(`删除失败: ${detail}`)
  }
}

async function doUpdateStatus(row, newStatus) {
  if (newStatus === row.intake_status) return
  statusUpdating.value = row.resume_id
  try {
    await updateStatus(row.resume_id, newStatus)
    ElMessage.success(`${row.name} 状态已更新为「${STATUS_OPTIONS.find(o => o.value === newStatus)?.label || newStatus}」`)
    loadCandidates()
  } catch (e) {
    ElMessage.error(`状态更新失败: ${e.response?.data?.detail || e.message || e}`)
  } finally {
    statusUpdating.value = null
  }
}

async function doForceComplete(row) {
  try {
    await ElMessageBox.confirm(`确定标记 ${row.name} 为已完成吗？`, '提示', { type: 'warning' })
  } catch {
    return
  }
  try {
    await forceComplete(row.resume_id)
    ElMessage.success('已标记完成')
    loadCandidates()
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function doReextract(row) {
  if (row._reextracting) return
  row._reextracting = true
  try {
    const r = await reextract(row.resume_id)
    if (r.skipped === 'no_messages') {
      ElMessage.warning('无聊天记录可抽')
    } else if (r.skipped === 'all_hard_filled') {
      ElMessage.info('四项已齐，无需重抽')
    } else if (r.filled && r.filled.length) {
      ElMessage.success(`重抽成功: ${r.filled.join(', ')}`)
      loadCandidates()
    } else {
      ElMessage.info('LLM 未抽到新值（候选人可能未明确回答）')
    }
  } catch (e) {
    if (e.response && e.response.status === 503) {
      ElMessage.error('LLM 未配置')
    } else {
      ElMessage.error('重抽失败')
    }
  } finally {
    row._reextracting = false
  }
}

onMounted(() => {
  loadCandidates()
  loadDailyCap()
  loadSettings()
})
</script>

<style scoped>
.intake-page {
  padding: 0;
}
.filter-bar {
  display: flex;
  align-items: center;
  margin-bottom: 16px;
}
.daily-cap-card {
  margin-bottom: 12px;
}
.daily-cap {
  display: flex;
  align-items: center;
  gap: 12px;
  font-size: 14px;
}
.daily-cap-label {
  color: #606266;
}
.daily-cap-value strong {
  font-size: 18px;
  color: #409eff;
}
.daily-cap-remaining {
  color: #909399;
  margin-left: 4px;
}
.automation-card .automation-row {
  display: flex;
  align-items: center;
  gap: 24px;
}
.automation-card .automation-target {
  display: flex; align-items: center; gap: 10px;
}
.automation-card .automation-target .label {
  font-size: 14px; color: #606266;
}
.automation-card .automation-progress {
  flex: 1;
}
.automation-card .automation-action {
  display: flex; align-items: center;
}
</style>
