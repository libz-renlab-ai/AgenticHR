<template>
  <div>
    <el-alert
      type="info"
      :closable="false"
      show-icon
      title="简历库语义"
      description="本列表显示四项信息齐全（到岗时间、空闲时段、实习时长、PDF 简历）的候选人。正在采集中的候选人请到 /intake 查看。"
      style="margin-bottom: 12px;"
    />
    <!-- 顶部工具栏 -->
    <div class="toolbar">
      <h2 style="margin: 0">简历库</h2>
      <div class="toolbar-actions">
        <el-input v-model="keyword" placeholder="搜索姓名/技能" style="width: 220px" @keyup.enter="loadResumes" clearable />
        <el-select v-model="statusFilter" placeholder="状态" clearable @change="loadResumes" style="width: 120px">
          <el-option label="已通过" value="passed" />
          <el-option label="已淘汰" value="rejected" />
          <el-option label="待筛选" value="pending" />
        </el-select>
        <el-upload
          ref="batchUploadRef"
          :show-file-list="false"
          accept=".pdf"
          multiple
          :auto-upload="false"
          :on-change="onBatchChange"
        >
          <el-button type="primary" :disabled="batchUploading">
            <el-icon v-if="batchUploading" class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
            {{ batchUploading ? `上传中 ${batchDone + batchFailed}/${batchTotal}` : '上传PDF简历（可多选）' }}
          </el-button>
        </el-upload>
        <el-button
          type="warning"
          plain
          @click="startAiParseAll"
          :disabled="aiParseRunning"
        >
          <el-icon v-if="aiParseRunning" class="is-loading" style="margin-right: 4px"><Loading /></el-icon>
          {{ aiParseRunning ? `后台内容解析中 ${aiParseProgress.completed}/${aiParseProgress.total}` : '手动启动内容解析' }}
        </el-button>
        <el-button type="danger" plain @click="clearAll">清空全部</el-button>
      </div>
    </div>

    <!-- 列表（紧凑模式）+ 展开为卡片 -->
    <div v-loading="loading" style="min-height: 200px">
      <el-empty v-if="!visibleResumes.length && !loading" description="暂无简历" />

      <div v-else class="resume-list">
        <div
          v-for="row in visibleResumes"
          :key="row.id"
          class="resume-list-item"
          :class="[`status-${row.status}`, { expanded: expandedId === row.id }]"
        >
          <!-- 紧凑行：一眼看到关键信息 -->
          <div class="row-compact" @click="toggleExpand(row.id)">
            <el-icon class="row-arrow"><ArrowRight /></el-icon>
            <span class="row-name">{{ row.name || '(未填写)' }}</span>
            <span class="row-school">{{ getSchoolDisplay(row) }}</span>
            <span class="row-phone">{{ row.phone || '—' }}</span>
            <div class="row-tags">
              <el-tag v-if="!row.phone && !row.email" type="warning" size="small" effect="plain">缺联系方式</el-tag>
              <el-tag v-if="row.status === 'rejected'" type="danger" size="small">已淘汰</el-tag>
              <el-tag v-else-if="row.status === 'pending'" type="info" size="small">待筛选</el-tag>
              <el-tag v-else type="success" size="small">已通过</el-tag>
              <el-tag v-if="row.ai_parsed === 'parsing'" type="primary" effect="plain" size="small">
                <el-icon class="is-loading" style="margin-right: 3px"><Loading /></el-icon>内容解析中
              </el-tag>
              <el-tag v-else-if="row.ai_parsed === 'failed'" type="danger" effect="plain" size="small">内容解析失败</el-tag>
              <el-tag v-else-if="row.ai_parsed === 'no'" type="info" effect="plain" size="small">待内容解析</el-tag>
            </div>
          </div>

          <!-- 展开后的详情卡片 -->
          <transition name="expand">
            <div v-if="expandedId === row.id" class="row-detail" @click.stop>
              <!-- 警告 + AI状态 -->
              <div v-if="!row.phone && !row.email" class="warn-banner">⚠ 联系方式缺失，无法发送面试通知</div>
              <div v-if="row.ai_parsed === 'failed'" class="ai-banner ai-banner-failed">
                <el-icon><WarningFilled /></el-icon>
                <span>AI 解析失败</span>
                <el-button size="small" type="danger" plain @click.stop="aiParseSingle(row)" :loading="row._aiLoading">点击重试</el-button>
              </div>

              <!-- 主体：左侧编辑区 + 右侧二维码 -->
              <div class="detail-body">
                <div class="detail-grid">
                  <div class="field">
                    <span class="label">姓名</span>
                    <el-input v-model="row.name" @blur="saveField(row, 'name')" placeholder="姓名" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">求职意向</span>
                    <el-input v-model="row.job_intention" @blur="saveField(row, 'job_intention')" placeholder="未填写" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">手机号</span>
                    <el-input v-model="row.phone" @blur="saveField(row, 'phone')" placeholder="扫右侧二维码后填入"
                      :class="{ 'save-success': row._savedField === 'phone' }" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">邮箱</span>
                    <el-input v-model="row.email" @blur="saveField(row, 'email')" placeholder="未填写"
                      :class="{ 'save-success': row._savedField === 'email' }" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">学历</span>
                    <el-select v-model="row.education" @change="saveField(row, 'education')" placeholder="未知" size="small" clearable>
                      <el-option label="博士" value="博士" />
                      <el-option label="硕士" value="硕士" />
                      <el-option label="本科" value="本科" />
                      <el-option label="大专" value="大专" />
                      <el-option label="其他" value="其他" />
                    </el-select>
                  </div>
                  <div class="field">
                    <span class="label">工作年限</span>
                    <el-input-number v-model="row.work_years" :min="0" :max="50" @change="saveField(row, 'work_years')" size="small" style="width:100%" />
                  </div>
                  <div class="field">
                    <span class="label">本科</span>
                    <el-input v-model="row.bachelor_school" @blur="saveField(row, 'bachelor_school')" placeholder="无" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">硕士</span>
                    <el-input v-model="row.master_school" @blur="saveField(row, 'master_school')" placeholder="无" size="small" />
                  </div>
                  <div class="field">
                    <span class="label">博士</span>
                    <el-input v-model="row.phd_school" @blur="saveField(row, 'phd_school')" placeholder="无" size="small" />
                  </div>
                </div>

                <!-- 二维码 -->
                <div class="qr-box">
                  <img v-if="row._qrBlobUrl && !row._qrError" :src="row._qrBlobUrl"
                    @error="() => { row._qrError = true }"
                    @load="() => { row._qrError = false }" alt="" />
                  <div v-else-if="!row._qrError && row._qrLoading" class="qr-placeholder">
                    <el-icon class="is-loading"><Loading /></el-icon>
                    <span>加载中</span>
                  </div>
                  <div v-else class="qr-placeholder" @click.stop="retryQr(row)" title="点击重试">
                    <el-icon><Refresh /></el-icon>
                    <span>点击重试</span>
                  </div>
                  <div class="qr-hint">扫码看手机号</div>
                </div>
              </div>

              <!-- 工作经历 -->
              <div class="work-exp">
                <span class="label">工作经历</span>
                <el-input v-model="row.work_experience" type="textarea" :rows="3"
                  @blur="saveField(row, 'work_experience')" placeholder="未填写" resize="none" size="small" />
              </div>

              <!-- 操作栏 -->
              <div class="detail-footer">
                <el-button-group>
                  <el-button :type="row.status === 'passed' ? 'success' : 'default'" size="small"
                    @click.stop="toggleStatus(row, 'passed')">{{ row.status === 'passed' ? '已通过' : '通过' }}</el-button>
                  <el-button :type="row.status === 'rejected' ? 'danger' : 'default'" size="small"
                    @click.stop="toggleStatus(row, 'rejected')">{{ row.status === 'rejected' ? '已淘汰' : '淘汰' }}</el-button>
                </el-button-group>
                <div>
                  <el-button v-if="row.pdf_path" size="small" link type="primary" @click.stop="viewPdf(row.id)">查看PDF</el-button>
                  <el-button size="small" link @click.stop="viewResume(row)">更多详情</el-button>
                  <el-button size="small" link type="warning" @click.stop="aiParseSingle(row)" :loading="row._aiLoading">简历内容解析</el-button>
                  <el-button size="small" link type="primary" @click.stop="aiScoreSingle(row)" :loading="row._aiScoringLoading">AI评分</el-button>
                  <el-button size="small" link type="danger" @click.stop="deleteResume(row)">删除</el-button>
                </div>
              </div>
            </div>
          </transition>
        </div>
      </div>
    </div>

    <el-pagination
      v-model:current-page="page"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      style="margin-top: 16px; justify-content: flex-end"
      @current-change="loadResumes"
    />

    <!-- 简历详情弹窗（补充信息） -->
    <el-dialog v-model="showDetail" title="简历详情" width="720px">
      <el-descriptions :column="2" border v-if="currentResume">
        <el-descriptions-item label="AI评分" v-if="currentResume.ai_score !== null && currentResume.ai_score !== undefined">
          {{ currentResume.ai_score }}
        </el-descriptions-item>
        <el-descriptions-item label="来源">{{ currentResume.source || '-' }}</el-descriptions-item>
        <el-descriptions-item label="技能" :span="2">{{ currentResume.skills || '-' }}</el-descriptions-item>
        <el-descriptions-item label="项目经历" :span="2" v-if="currentResume.project_experience">
          <pre style="white-space: pre-wrap; margin: 0; font-family: inherit">{{ currentResume.project_experience }}</pre>
        </el-descriptions-item>
        <el-descriptions-item label="自我评价" :span="2" v-if="currentResume.self_evaluation">
          {{ currentResume.self_evaluation }}
        </el-descriptions-item>
        <el-descriptions-item label="AI评价" :span="2" v-if="currentResume.ai_summary">{{ currentResume.ai_summary }}</el-descriptions-item>
        <el-descriptions-item label="简历原文" :span="2" v-if="currentResume.raw_text">
          <el-collapse>
            <el-collapse-item title="展开查看完整简历文本">
              <pre style="white-space: pre-wrap; margin: 0; font-size: 13px; max-height: 400px; overflow-y: auto">{{ currentResume.raw_text }}</pre>
            </el-collapse-item>
          </el-collapse>
        </el-descriptions-item>
      </el-descriptions>
      <div class="matching-block" v-if="currentMatching.length">
        <h4 style="margin: 12px 0 6px; color: #606266">对接岗位分数</h4>
        <el-table :data="currentMatching" size="small" stripe>
          <el-table-column prop="job_title" label="岗位" />
          <el-table-column label="总分" width="80">
            <template #default="{ row }">
              <span :style="{ color: scoreColor(row.total_score), fontWeight: 600 }">
                {{ row.total_score.toFixed(1) }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="标签" width="220">
            <template #default="{ row }">
              <el-tag v-for="t in row.tags" :key="t" size="small" style="margin-right: 4px">{{ t }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" link type="primary" @click="viewMatchingOnJob(row.job_id, row.resume_id)">查看 →</el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div class="ai-eval-block" v-if="currentResume">
        <h4 style="margin: 12px 0 6px; color: #606266">面试 AI 评价</h4>
        <ResumeAiEvaluationsList
          :resume-id="currentResume.id"
          @open-interview="openInterview"
        />
      </div>
      <template #footer>
        <el-button v-if="currentResume?.pdf_path" type="primary" @click="viewPdf(currentResume.id)">查看PDF</el-button>
        <el-button @click="showDetail = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, WarningFilled, Refresh, ArrowRight } from '@element-plus/icons-vue'
import { resumeApi, matchingApi } from '../api'
import ResumeAiEvaluationsList from '../components/ResumeAiEvaluationsList.vue'

const resumes = ref([])
const loading = ref(false)
const page = ref(1)
const pageSize = 12
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const showDetail = ref(false)
const currentResume = ref(null)
const aiParseRunning = ref(false)
const aiParseProgress = ref({ total: 0, completed: 0, failed: 0, current: '' })
const expandedId = ref(null)  // 当前展开的简历 id（只允许一个）
const currentMatching = ref([])

// 批量上传状态
const batchUploadRef = ref(null)
const batchUploading = ref(false)
const batchTotal = ref(0)
const batchDone = ref(0)
const batchFailed = ref(0)
let pendingBatchFiles = []
let batchDebounceTimer = null

const visibleResumes = computed(() => resumes.value)

function toggleExpand(id) {
  expandedId.value = (expandedId.value === id) ? null : id
  if (expandedId.value === id) {
    const row = resumes.value.find(r => r.id === id)
    if (row && !row._qrBlobUrl && !row._qrLoading && !row._qrError) {
      loadQrBlob(row)
    }
  }
}

// `<img>` cannot send Authorization headers and the backend dropped the legacy
// `?token=` query-param escape hatch (BUG-037: leaked JWTs into access logs and
// browser history). Fetch the protected QR PNG with the auth header, turn it
// into an object URL, and bind that to <img :src>.
async function loadAuthBlob(url) {
  const token = localStorage.getItem('token') || ''
  const r = await fetch(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!r.ok) throw new Error(`http ${r.status}`)
  return URL.createObjectURL(await r.blob())
}

async function loadQrBlob(row) {
  row._qrLoading = true
  row._qrError = false
  try {
    const regen = row._qrRegen ? '?regen=1' : ''
    const objectUrl = await loadAuthBlob(`/api/resumes/${row.id}/qr${regen}`)
    if (row._qrBlobUrl) URL.revokeObjectURL(row._qrBlobUrl)
    row._qrBlobUrl = objectUrl
    row._qrRegen = false
  } catch (e) {
    row._qrError = true
  } finally {
    row._qrLoading = false
  }
}

// 根据学历优先级返回"学校"显示：博士 > 硕士 > 本科
function getSchoolDisplay(row) {
  const school = row.phd_school || row.master_school || row.bachelor_school
  const degree = row.phd_school ? '博士' : row.master_school ? '硕士' : row.bachelor_school ? '本科' : (row.education || '')
  if (school && degree) return `${degree} · ${school}`
  if (school) return school
  if (degree) return degree
  return '—'
}

// 列表刷新时保留每行的本地 transient 状态 (`_qrBlobUrl` / `_aiLoading` 等),
// 否则 _qrBlobUrl 丢失 → v-if 失败 → 闪一下变 "点击重试" placeholder.
// 约定: 任何 `_` 前缀的属性都视为本地 UI 状态, 不被服务端响应覆盖.
//
// BUG-120: 服务端 row 被删除时, 旧 row 上的 _qrBlobUrl 不再有 newItem 接管,
// 必须在合并时主动 URL.revokeObjectURL 防 Blob URL 累积内存泄漏。
function mergeTransientState(newItems, oldItems) {
  if (!oldItems || oldItems.length === 0) return newItems
  const oldMap = new Map(oldItems.map(r => [r.id, r]))
  const newIds = new Set(newItems.map(r => r.id))
  // 服务端已删的 row → 立即 revoke 其 _qrBlobUrl
  for (const o of oldItems) {
    if (!newIds.has(o.id) && o._qrBlobUrl) {
      try { URL.revokeObjectURL(o._qrBlobUrl) } catch {}
      o._qrBlobUrl = null
    }
  }
  for (const n of newItems) {
    const o = oldMap.get(n.id)
    if (!o) continue
    for (const k of Object.keys(o)) {
      if (k.startsWith('_')) n[k] = o[k]
    }
  }
  return newItems
}

async function loadResumes() {
  loading.value = true
  try {
    const data = await resumeApi.list({
      page: page.value,
      page_size: pageSize,
      keyword: keyword.value || undefined,
      status: statusFilter.value || undefined,
    })
    resumes.value = mergeTransientState(data.items, resumes.value)
    total.value = data.total
    ensurePollingIfNeeded()
  } catch (e) {
    ElMessage.error('加载简历失败')
  } finally {
    loading.value = false
  }
}

function ensurePollingIfNeeded() {
  const hasPending = resumes.value.some(r => r.ai_parsed === 'no' || r.ai_parsed === 'parsing')
  if (hasPending && !aiPollTimer) {
    pollAiParseStatus()
  } else if (!hasPending && aiPollTimer) {
    clearInterval(aiPollTimer)
    aiPollTimer = null
    aiParseRunning.value = false
  }
}

function retryQr(row) {
  // 点击"重试"会强制服务端重跑提取算法（旧缓存丢弃）
  row._qrError = false
  row._qrRegen = true
  loadQrBlob(row)
}

async function saveField(row, field) {
  if (field === 'phone' && row.phone && !/^1[3-9]\d{9}$/.test(row.phone)) {
    ElMessage.warning('手机号格式不正确，需为11位中国手机号')
    return
  }
  if (field === 'email' && row.email && !/^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(row.email)) {
    ElMessage.warning('邮箱格式不正确')
    return
  }
  try {
    await resumeApi.update(row.id, { [field]: row[field] })
    row._savedField = field
    setTimeout(() => { row._savedField = null }, 1500)
  } catch (e) {
    ElMessage.error('保存失败，已回滚')
    loadResumes()
  }
}

async function viewResume(row) {
  currentResume.value = row
  showDetail.value = true
  currentMatching.value = []
  try {
    const data = await matchingApi.listByResume(row.id)
    currentMatching.value = data.items || []
  } catch {
    currentMatching.value = []
  }
}

function scoreColor(s) {
  if (s >= 80) return '#67c23a'
  if (s >= 60) return '#409eff'
  if (s >= 40) return '#e6a23c'
  return '#f56c6c'
}

function viewMatchingOnJob(jobId, resumeId) {
  window.open(`/#/jobs/${jobId}?tab=matching&highlight_resume=${resumeId}`, '_blank')
}

// 面试 AI 评价 → 跳转到 Interviews 页（与 viewMatchingOnJob 一致风格：新窗口 hash 路由）
function openInterview(interviewId) {
  if (!interviewId) return
  window.open(`/#/interviews?highlight_id=${interviewId}`, '_blank')
}

async function viewPdf(resumeId) {
  // Same constraint as QR: window.open can't carry an Authorization header.
  // Fetch the PDF with the auth header, then open the resulting blob URL.
  try {
    const objectUrl = await loadAuthBlob(`/api/resumes/${resumeId}/pdf`)
    window.open(objectUrl, '_blank')
    // Revoke after a delay so the new tab has time to load it. Object URLs
    // tied to PDFs need to remain valid until the viewer reads them.
    setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
  } catch (e) {
    ElMessage.error('打开 PDF 失败：' + (e.message || '请重新登录'))
  }
}

async function toggleStatus(row, targetStatus) {
  if (row.status === targetStatus) return  // 已经是这个状态，不做动作
  if (targetStatus === 'rejected') {
    try {
      await ElMessageBox.confirm(
        `确定将 "${row.name}" 标记为已淘汰？`,
        '确认淘汰',
        { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  try {
    await resumeApi.update(row.id, { status: targetStatus })
    row.status = targetStatus
    const msgs = { passed: '已标记为通过', rejected: '已标记为淘汰' }
    ElMessage.success(msgs[targetStatus])
  } catch (e) {
    ElMessage.error('操作失败')
  }
}

async function deleteResume(row) {
  try {
    await ElMessageBox.confirm(`确定删除 "${row.name}" 的简历？此操作不可恢复。`, '确认删除', {
      type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消',
    })
    await resumeApi.delete(row.id)
    ElMessage.success('已删除')
    if (expandedId.value === row.id) expandedId.value = null
    loadResumes()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('删除失败')
  }
}

let aiPollTimer = null
let lastProgressTime = null
let lastProgressCount = null

async function startAiParseAll() {
  try {
    await resumeApi.aiParseAll()
    aiParseRunning.value = true
    ElMessage.success('AI 解析任务已在后台启动')
    lastProgressTime = Date.now()
    lastProgressCount = null
    pollAiParseStatus()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || 'AI 解析启动失败')
  }
}

function pollAiParseStatus() {
  if (aiPollTimer) return
  aiPollTimer = setInterval(async () => {
    try {
      const status = await resumeApi.aiParseStatus()
      aiParseProgress.value = status
      aiParseRunning.value = status.running

      const currentCount = status.completed
      if (lastProgressCount === null || currentCount !== lastProgressCount) {
        lastProgressCount = currentCount
        lastProgressTime = Date.now()
      } else if (Date.now() - lastProgressTime > 180000) {
        clearInterval(aiPollTimer)
        aiPollTimer = null
        aiParseRunning.value = false
        lastProgressTime = null
        lastProgressCount = null
        ElMessage.warning('AI 解析已超过 3 分钟无进展，已停止等待，请检查后台状态')
        return
      }

      const data = await resumeApi.list({
        page: page.value, page_size: pageSize,
        keyword: keyword.value || undefined, status: statusFilter.value || undefined,
      })
      resumes.value = mergeTransientState(data.items, resumes.value)
      total.value = data.total
      const stillPending = resumes.value.some(r => r.ai_parsed === 'no' || r.ai_parsed === 'parsing')
      if (!stillPending && !status.running) {
        clearInterval(aiPollTimer)
        aiPollTimer = null
        lastProgressTime = null
        lastProgressCount = null
      }
    } catch {
      clearInterval(aiPollTimer)
      aiPollTimer = null
      aiParseRunning.value = false
      lastProgressTime = null
      lastProgressCount = null
    }
  }, 3000)
}

async function aiParseSingle(row) {
  row._aiLoading = true
  try {
    const result = await resumeApi.aiParseSingle(row.id)
    Object.assign(row, result)
    row.ai_parsed = 'yes'
    ElMessage.success(`${row.name} AI解析完成`)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || 'AI解析失败')
  } finally {
    row._aiLoading = false
  }
}

async function aiScoreSingle(row) {
  // 对该简历 × 所有 is_active + 能力模型已通过的岗位打分 (F2)
  row._aiScoringLoading = true
  try {
    const { task_id, total } = await matchingApi.recomputeResume(row.id)
    if (!total || total === 0) {
      ElMessage.warning('暂无启用中的岗位（需要岗位 is_active + 能力模型已发布）')
      return
    }
    ElMessage.success(`已启动评分任务（共 ${total} 个岗位）`)
    // 轮询进度直到完成
    const pollStart = Date.now()
    while (Date.now() - pollStart < 300000) {  // 最多等 5 分钟
      await new Promise(r => setTimeout(r, 2000))
      const s = await matchingApi.recomputeStatus(task_id)
      if (!s.running) {
        ElMessage.success(`评分完成：${s.completed} 成功 / ${s.failed} 失败`)
        return
      }
    }
    ElMessage.warning('评分超时，请稍后到岗位页"匹配候选人"Tab 查看结果')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || 'AI 评分启动失败')
  } finally {
    row._aiScoringLoading = false
  }
}

async function clearAll() {
  try {
    await ElMessageBox.prompt(
      '此操作将永久删除所有简历、面试和通知记录，且不可恢复。\n请输入「确认清空」以继续：',
      '危险操作',
      {
        confirmButtonText: '清空', cancelButtonText: '取消', type: 'error',
        inputValidator: (val) => val === '确认清空' || '请输入「确认清空」',
        inputPlaceholder: '确认清空',
      }
    )
    const result = await resumeApi.clearAll()
    ElMessage.success(`已清空 ${result.deleted} 份简历`)
    expandedId.value = null
    loadResumes()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error('清空失败')
  }
}

// el-upload 的 on-change 对每个选中文件各触发一次（auto-upload=false）。
// 用 50ms debounce 把"一次多选"的 N 次回调收敛成一次批量上传。
function onBatchChange(_file, fileList) {
  if (batchUploading.value) return
  pendingBatchFiles = fileList.filter(f => f.raw)
  if (batchDebounceTimer) clearTimeout(batchDebounceTimer)
  batchDebounceTimer = setTimeout(() => {
    batchDebounceTimer = null
    const files = pendingBatchFiles.slice()
    pendingBatchFiles = []
    if (files.length) runBatchUpload(files)
  }, 50)
}

// 逐份顺序上传：进度清晰、不会同时打满后端、单份失败互不影响。
async function runBatchUpload(files) {
  batchUploading.value = true
  batchTotal.value = files.length
  batchDone.value = 0
  batchFailed.value = 0
  const failedNames = []
  for (const f of files) {
    try {
      await resumeApi.upload(f.raw)
      batchDone.value++
    } catch (e) {
      batchFailed.value++
      failedNames.push(`${f.name}：${e.response?.data?.detail || e.message || '上传失败'}`)
    }
  }
  batchUploadRef.value?.clearFiles()
  if (batchFailed.value === 0) {
    ElMessage.success(`上传完成：${batchDone.value} 份全部成功`)
  } else {
    ElMessageBox.alert(
      `成功 ${batchDone.value} 份，失败 ${batchFailed.value} 份。\n\n失败明细：\n${failedNames.join('\n')}`,
      '批量上传结果',
      { type: 'warning', confirmButtonText: '知道了' }
    )
  }
  batchUploading.value = false
  loadResumes()
}

onMounted(loadResumes)

onUnmounted(() => {
  if (aiPollTimer) {
    clearInterval(aiPollTimer)
    aiPollTimer = null
  }
  // Release any QR blob object URLs we minted so they don't leak.
  for (const r of resumes.value) {
    if (r._qrBlobUrl) {
      try { URL.revokeObjectURL(r._qrBlobUrl) } catch {}
      r._qrBlobUrl = null
    }
  }
})
</script>

<style scoped>
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}
.toolbar h2 {
  flex-shrink: 0;
  white-space: nowrap;
  margin: 0;
}

.toolbar-actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

/* ── 列表容器 ── */
.resume-list {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 6px;
  overflow: hidden;
}

.resume-list-item {
  border-bottom: 1px solid #f0f2f5;
  border-left: 3px solid transparent;
  transition: background 0.15s, border-left-color 0.15s;
}
.resume-list-item:last-child { border-bottom: none; }
.resume-list-item.status-passed { border-left-color: #67c23a; }
.resume-list-item.status-pending { border-left-color: #e6a23c; }
.resume-list-item.status-rejected { border-left-color: #f56c6c; opacity: 0.65; }
.resume-list-item.expanded {
  background: #fafbfc;
  border-left-color: #409eff !important;
}

/* ── 紧凑行 ── */
.row-compact {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  cursor: pointer;
  user-select: none;
  transition: background 0.1s;
}
.row-compact:hover { background: #f5f7fa; }

.row-arrow {
  font-size: 12px;
  color: #909399;
  transition: transform 0.2s;
  flex-shrink: 0;
}
.expanded .row-arrow { transform: rotate(90deg); color: #409eff; }

.row-name {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
  min-width: 80px;
  flex-shrink: 0;
}

.row-school {
  color: #606266;
  font-size: 13px;
  flex: 1;
  min-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-phone {
  color: #606266;
  font-size: 13px;
  font-family: 'SF Mono', Monaco, monospace;
  min-width: 110px;
  flex-shrink: 0;
}

.row-tags {
  display: flex;
  gap: 6px;
  flex-shrink: 0;
  align-items: center;
}

/* ── 展开详情 ── */
.row-detail {
  padding: 12px 16px 14px 40px;
  background: #fff;
  border-top: 1px solid #f0f2f5;
}

.warn-banner {
  background: #fdf6ec;
  color: #e6a23c;
  padding: 6px 12px;
  font-size: 12px;
  border-left: 3px solid #e6a23c;
  margin-bottom: 10px;
  border-radius: 2px;
}

.ai-banner {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; margin-bottom: 10px;
  border-radius: 4px; font-size: 13px; font-weight: 500;
}
.ai-banner-failed {
  background: #fef0f0; color: #c45656;
  border: 1px solid #fbc4c4; justify-content: space-between;
}
.ai-banner-failed > span { flex: 1; }

.detail-body {
  display: flex;
  gap: 16px;
  margin-bottom: 10px;
}

.detail-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: 8px 12px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}
.field .label {
  font-size: 11px;
  color: #909399;
}
.field :deep(.el-input),
.field :deep(.el-select),
.field :deep(.el-input-number) {
  width: 100%;
}
.field :deep(.el-input__wrapper),
.field :deep(.el-select__wrapper) {
  box-shadow: none;
  background: #f5f7fa;
  padding: 2px 8px;
}
.field :deep(.el-input__wrapper.is-focus),
.field :deep(.el-select__wrapper.is-focus) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset !important;
  background: #fff;
}

.qr-box {
  width: 96px;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}
.qr-box img {
  width: 96px; height: 96px;
  object-fit: contain;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  background: #fff;
}
.qr-placeholder {
  width: 96px; height: 96px;
  border: 1px dashed #d4d7de;
  border-radius: 4px;
  background: #fafbfc;
  display: flex; flex-direction: column;
  align-items: center; justify-content: center;
  gap: 4px; color: #909399; font-size: 11px;
  cursor: pointer;
  transition: all 0.15s;
}
.qr-placeholder:hover {
  border-color: #409eff; background: #ecf5ff; color: #409eff;
}
.qr-placeholder .el-icon { font-size: 20px; }
.qr-hint {
  font-size: 11px; color: #909399;
  text-align: center; line-height: 1.2;
}

.work-exp {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  margin-bottom: 10px;
}
.work-exp .label {
  font-size: 12px;
  color: #909399;
  min-width: 52px;
  padding-top: 6px;
  text-align: right;
  flex-shrink: 0;
}
.work-exp > :deep(.el-textarea) { flex: 1; }
.work-exp :deep(.el-textarea__inner) {
  box-shadow: none;
  background: #f5f7fa;
  font-size: 13px;
}
.work-exp :deep(.el-textarea__inner:focus) {
  box-shadow: 0 0 0 1px var(--el-color-primary) inset;
  background: #fff;
}

.detail-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 8px;
  border-top: 1px solid #f0f2f5;
}

/* 展开过渡动画 */
.expand-enter-active,
.expand-leave-active {
  transition: all 0.2s ease-out;
  overflow: hidden;
}
.expand-enter-from,
.expand-leave-to {
  max-height: 0;
  opacity: 0;
  padding-top: 0;
  padding-bottom: 0;
}
.expand-enter-to,
.expand-leave-from {
  max-height: 600px;
  opacity: 1;
}

/* 保存闪烁 */
.save-success :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px #67c23a inset !important;
  border-color: #67c23a;
  transition: box-shadow 0.3s;
}
</style>
