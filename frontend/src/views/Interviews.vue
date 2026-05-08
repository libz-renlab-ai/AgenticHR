<template>
  <div>
    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px">
      <h2>面试安排</h2>
      <div>
        <el-button type="primary" size="large" @click="openDialog()">+ 新建面试</el-button>
        <el-button type="danger" plain size="small" @click="clearAllInterviews" style="margin-left: 12px">清空全部</el-button>
      </div>
    </div>

    <!-- 面试卡片列表 -->
    <div v-loading="loading">
      <div v-if="interviews.length === 0" style="text-align: center; padding: 60px 0; color: #999">
        暂无面试安排，点击右上角"新建面试"开始
      </div>

      <div v-for="iv in interviews" :key="iv.id" class="interview-card" :class="'status-' + iv.status">
        <!-- 卡片头部：候选人 + 状态 -->
        <div class="card-header">
          <div class="candidate-info">
            <span class="candidate-name">{{ iv.resume_name || getResumeName(iv.resume_id) }}</span>
            <el-tag :type="iv.status === 'scheduled' ? 'primary' : iv.status === 'completed' ? 'success' : 'info'" size="small" style="margin-left: 8px">
              {{ iv.status === 'scheduled' ? '待面试' : iv.status === 'completed' ? '已完成' : '已取消' }}
            </el-tag>
          </div>
          <div class="card-actions-mini">
            <el-button size="small" link @click="openDialog(iv)">编辑</el-button>
            <el-button size="small" type="danger" link @click="deleteInterview(iv)">删除</el-button>
          </div>
        </div>

        <!-- 候选人信息：学校/学历/手机/邮箱 2x2 紧凑网格 -->
        <div class="candidate-block" v-if="getResume(iv.resume_id)">
          <div class="cb-grid">
            <div class="cb-item">
              <span class="cb-label">学校</span>
              <span class="cb-value">{{ getEduInfo(getResume(iv.resume_id)).school || '—' }}</span>
            </div>
            <div class="cb-item">
              <span class="cb-label">学历</span>
              <span class="cb-value">{{ getEduInfo(getResume(iv.resume_id)).degree || '—' }}</span>
            </div>
            <div class="cb-item">
              <span class="cb-label">手机</span>
              <span class="cb-value">{{ getResume(iv.resume_id).phone || '—' }}</span>
            </div>
            <div class="cb-item">
              <span class="cb-label">邮箱</span>
              <span class="cb-value">{{ getResume(iv.resume_id).email || '—' }}</span>
            </div>
          </div>
        </div>

        <!-- 卡片内容：关键信息 -->
        <div class="card-body">
          <div class="info-row">
            <span class="info-label">面试官</span>
            <span class="info-value">{{ getInterviewerName(iv.interviewer_id) }}</span>
          </div>
          <div class="info-row">
            <span class="info-label">面试时间</span>
            <span class="info-value highlight">{{ formatTime(iv.start_time) }}</span>
          </div>
          <div class="info-row" v-if="iv.meeting_link">
            <span class="info-label">会议链接</span>
            <a class="info-value link" :href="iv.meeting_link" target="_blank">{{ iv.meeting_link }}</a>
          </div>
          <div class="info-row" v-if="iv.meeting_account">
            <span class="info-label">主持账号</span>
            <span class="info-value">
              <el-tag size="small" type="info" effect="plain">{{ iv.meeting_account }}</el-tag>
            </span>
          </div>
          <div class="info-row" v-if="iv.notes">
            <span class="info-label">反馈/备注</span>
            <span class="info-value notes">{{ iv.notes }}</span>
          </div>
        </div>

        <!-- 卡片底部：操作按钮（分组） -->
        <div class="card-footer" v-if="iv.status === 'scheduled'">
          <div class="action-group">
            <span class="group-label">会议</span>
            <el-button size="small" type="success" plain @click="autoCreateMeeting(iv)">
              {{ iv.meeting_link ? '重建会议' : '创建腾讯会议' }}
            </el-button>
            <el-button size="small" plain @click="openInvitationDialog(iv)">复制邀请信息</el-button>
          </div>
          <div class="action-group">
            <span class="group-label">通知</span>
            <el-button size="small" type="primary" plain @click="sendNotification(iv)">发送面试通知</el-button>
          </div>
          <div class="action-group">
            <span class="group-label">AI</span>
            <el-button size="small" type="warning" plain @click="openAiEvalDialog(iv)">AI 面评</el-button>
          </div>
          <div class="action-group">
            <span class="group-label">状态</span>
            <el-button size="small" plain @click="cancelInterview(iv.id)">取消面试</el-button>
          </div>
        </div>

        <!-- 已完成面试也允许查看 AI 面评 -->
        <div class="card-footer" v-else-if="iv.status === 'completed'">
          <div class="action-group">
            <span class="group-label">AI</span>
            <el-button size="small" type="warning" plain @click="openAiEvalDialog(iv)">AI 面评</el-button>
          </div>
        </div>
      </div>
    </div>

    <!-- 新建/编辑面试弹窗 -->
    <el-dialog v-model="showDialog" :title="editingId ? '编辑面试' : '新建面试'" width="900px" top="3vh">
      <el-form :model="form" label-width="100px">
        <el-row :gutter="16">
          <el-col :span="10">
            <el-form-item label="目标岗位" required>
              <el-select v-model="form.job_id" filterable placeholder="请选择岗位" style="width: 100%"
                @change="onJobChange">
                <el-option v-for="j in jobOptions" :key="j.id"
                  :label="`${j.title}${j.department ? '（' + j.department + '）' : ''}`"
                  :value="j.id" />
              </el-select>
            </el-form-item>

            <el-form-item label="候选人" required>
              <el-select v-model="form.resume_id" filterable
                :placeholder="form.job_id ? (passedCandidatesLoading ? '加载中…' : '选择通过候选人') : '请先选择岗位'"
                :disabled="!form.job_id"
                style="width: 100%">
                <template v-if="form.job_id && !passedCandidatesLoading && passedCandidatesForJob.length === 0">
                  <el-option disabled :value="null" label="该岗位下还没有标记'通过'的候选人" />
                </template>
                <el-option v-for="r in passedCandidatesForJob" :key="r.id"
                  :label="`${r.name}${r.phone ? '（' + r.phone + '）' : ''}`"
                  :value="r.id" />
              </el-select>
              <div v-if="form.job_id && !passedCandidatesLoading && passedCandidatesForJob.length === 0"
                style="font-size: 11px; color: #f56c6c; margin-top: 4px; line-height: 1.4">
                该岗位下还没有标记"通过"的候选人。请先去岗位详情的"匹配候选人"Tab 标记。
              </div>
            </el-form-item>

            <el-alert v-if="selectedCandidate && !selectedCandidate.phone && !selectedCandidate.email"
              title="该候选人无联系方式，将无法发送面试通知"
              type="warning" :closable="false" show-icon style="margin-bottom: 10px;" />

            <!-- 候选人信息：学校/学历/手机/邮箱 2x2 紧凑网格 -->
            <div v-if="selectedCandidate" class="candidate-preview">
              <div class="cp-grid">
                <div class="cp-item">
                  <span class="cp-label">学校</span>
                  <span class="cp-value">{{ getEduInfo(selectedCandidate).school || '—' }}</span>
                </div>
                <div class="cp-item">
                  <span class="cp-label">学历</span>
                  <span class="cp-value">{{ getEduInfo(selectedCandidate).degree || '—' }}</span>
                </div>
                <div class="cp-item">
                  <span class="cp-label">手机</span>
                  <span class="cp-value">{{ selectedCandidate.phone || '—' }}</span>
                </div>
                <div class="cp-item">
                  <span class="cp-label">邮箱</span>
                  <span class="cp-value">{{ selectedCandidate.email || '—' }}</span>
                </div>
              </div>
            </div>
            <el-form-item label="面试官" required>
              <el-select v-model="form.interviewer_id" filterable placeholder="搜索面试官" style="width: 100%"
                @change="onInterviewerChange">
                <el-option v-for="i in interviewerOptions" :key="i.id"
                  :label="`${i.name}${i.department ? '（' + i.department + '）' : ''}`"
                  :value="i.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="已选时间">
              <div v-if="form.timeRange" style="font-size: 14px; color: #1677ff; font-weight: 500">
                {{ formatSelectedTime() }}
              </div>
              <div v-else style="font-size: 13px; color: #999">
                请在右侧日历上拖拽选择时间
              </div>
            </el-form-item>
            <el-form-item label="会议名称">
              <el-input v-model="form.meeting_topic" maxlength="100" show-word-limit
                placeholder="选了候选人和面试官后自动生成，可修改"
                @input="meetingTopicEdited = true" />
              <div style="font-size: 11px; color: #999; margin-top: 2px">
                创建腾讯会议时使用此名称；留空则用默认格式"面试-候选人-面试官"
              </div>
            </el-form-item>
            <el-form-item label="会议链接">
              <el-input v-model="form.meeting_link" placeholder="创建会议后自动填入" />
            </el-form-item>
            <el-form-item label="会议密码">
              <el-input v-model="form.meeting_password" placeholder="可选" />
            </el-form-item>
            <el-form-item label="状态" v-if="editingId">
              <el-select v-model="form.status" style="width: 100%">
                <el-option label="待面试" value="scheduled" />
                <el-option label="已完成" value="completed" />
                <el-option label="已取消" value="cancelled" />
              </el-select>
            </el-form-item>
            <el-form-item label="备注">
              <el-input v-model="form.notes" type="textarea" :rows="2" placeholder="可选" />
            </el-form-item>
          </el-col>
          <el-col :span="14">
            <div class="calendar-wrap">
              <div class="calendar-container">
                <FullCalendar ref="calendarRef" :options="calendarOptions" />
              </div>
              <!-- 未选面试官：提示引导 -->
              <div v-if="!form.interviewer_id" class="calendar-overlay idle-overlay">
                <el-icon class="overlay-icon"><UserFilled /></el-icon>
                <div class="overlay-title">请先选择面试官</div>
                <div class="overlay-hint">选择后将自动读取对应的飞书日历</div>
              </div>
              <!-- 选了面试官正在拉日历：loading 动画 -->
              <div v-else-if="calendarLoading" class="calendar-overlay loading-overlay">
                <el-icon class="overlay-icon spinning"><Loading /></el-icon>
                <div class="overlay-title">正在同步飞书日历…</div>
                <div class="overlay-hint">读取 {{ getInterviewerName(form.interviewer_id) }} 的忙闲状态</div>
              </div>
            </div>
            <div style="text-align: center; font-size: 11px; color: #bbb; margin-top: 4px">
              拖拽选择面试时间段（可跨越已占用时段） · 蓝色=当前会议 · 橙色=其他面试 · 红色=飞书忙碌
            </div>
          </el-col>
        </el-row>
      </el-form>
      <template #footer>
        <el-button @click="showDialog = false">取消</el-button>
        <el-button type="primary" @click="saveInterview" :disabled="!form.timeRange">
          {{ editingId ? '保存修改' : '确认安排' }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 邀请信息编辑对话框（可实时修改后复制） -->
    <el-dialog v-model="showInvitationDialog" title="复制邀请信息" width="560px">
      <div style="font-size: 12px; color: #909399; margin-bottom: 8px;">
        可自由修改下方内容，点击"复制"后粘贴给候选人或面试官。
      </div>
      <el-input
        v-model="invitationText"
        type="textarea"
        :rows="10"
        resize="vertical"
        placeholder="邀请信息"
      />
      <template #footer>
        <el-button @click="showInvitationDialog = false">关闭</el-button>
        <el-button @click="resetInvitationText">恢复默认模板</el-button>
        <el-button type="primary" @click="copyInvitationText">复制到剪贴板</el-button>
      </template>
    </el-dialog>

    <!-- AI 面评对话框 -->
    <el-dialog v-model="showAiEvalDialog" title="AI 面评" width="1100px" top="3vh" destroy-on-close>
      <AiInterviewEvalPanel v-if="showAiEvalDialog && aiEvalInterviewId" :interview-id="aiEvalInterviewId" />
      <template #footer>
        <el-button @click="showAiEvalDialog = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Loading, UserFilled } from '@element-plus/icons-vue'
import { schedulingApi, notificationApi, resumeApi, meetingApi, jobApi, matchingApi } from '../api'
import FullCalendar from '@fullcalendar/vue3'
import timeGridPlugin from '@fullcalendar/timegrid'
import interactionPlugin from '@fullcalendar/interaction'
import AiInterviewEvalPanel from '../components/AiInterviewEvalPanel.vue'

const interviews = ref([])
const loading = ref(false)
const showDialog = ref(false)
const editingId = ref(null)
const calendarLoading = ref(false)
let freeBusySeq = 0  // 竞争防护：只采纳最新一次请求的结果
const candidateOptions = ref([])
const interviewerOptions = ref([])
const resumeMap = ref({})
const interviewerMap = ref({})
const calendarRef = ref(null)
const calendarEvents = ref([])
const jobOptions = ref([])
const passedCandidatesForJob = ref([])
const passedCandidatesLoading = ref(false)

const form = ref({
  job_id: null, resume_id: null, interviewer_id: null, timeRange: null,
  meeting_topic: '', meeting_link: '', meeting_password: '', status: 'scheduled', notes: ''
})
// 追踪用户是否手动改过会议名称，没改过时跟随候选人/面试官自动更新
const meetingTopicEdited = ref(false)

const today = new Date()
const calendarStart = today.toISOString().split('T')[0]

const calendarOptions = computed(() => ({
  plugins: [timeGridPlugin, interactionPlugin],
  initialView: 'timeGrid',
  initialDate: calendarStart,
  duration: { days: 5 },
  locale: 'zh-cn',
  height: 500,
  headerToolbar: { left: '', center: 'title', right: '' },
  titleFormat: { year: 'numeric', month: 'long' },
  slotMinTime: '08:00:00',
  slotMaxTime: '21:00:00',
  slotDuration: '00:30:00',
  slotLabelInterval: '01:00:00',
  snapDuration: '00:05:00',
  allDaySlot: false,
  selectable: true,
  selectMirror: true,
  // 允许选区覆盖已有事件——尤其是"想把会议延后 30 分钟"这种部分重叠的场景
  selectOverlap: true,
  eventOverlap: true,
  nowIndicator: true,
  weekends: true,
  dayHeaderFormat: { weekday: 'short', month: 'numeric', day: 'numeric' },
  slotLabelFormat: { hour: '2-digit', minute: '2-digit', hour12: false },
  events: calendarEvents.value,
  selectAllow: (selectInfo) => selectInfo.start >= new Date(),
  select: (info) => { form.value.timeRange = [info.start, info.end] },
  eventColor: '#ff6a00',
  eventTextColor: '#fff',
}))

function formatSelectedTime() {
  if (!form.value.timeRange) return ''
  const [s, e] = form.value.timeRange
  const fmt = (d) => (d instanceof Date ? d : new Date(d)).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  return `${fmt(s)} - ${fmt(e)}`
}

async function onInterviewerChange() {
  calendarEvents.value = []
  if (!form.value.interviewer_id) {
    calendarLoading.value = false
    return
  }
  calendarLoading.value = true
  const seq = ++freeBusySeq
  try {
    const data = await schedulingApi.getFreeBusy(form.value.interviewer_id, 7)
    if (seq !== freeBusySeq) return  // 已被更新的请求覆盖，丢弃
    const slots = data.busy_slots || []

    // 编辑模式下：过滤掉"就是当前正在改的这场面试"的 busy 槽
    // 匹配规则：时间戳完全一致（系统内 Interview 记录会精确匹配；
    // 飞书侧事件可能略有偏差，暂按精确匹配处理）
    let filtered = slots
    if (editingId.value && form.value.timeRange) {
      const myStart = new Date(form.value.timeRange[0]).getTime()
      const myEnd = new Date(form.value.timeRange[1]).getTime()
      filtered = slots.filter(s => {
        const sStart = new Date(s.start).getTime()
        const sEnd = new Date(s.end).getTime()
        return !(sStart === myStart && sEnd === myEnd)
      })
    }

    // 关键：display='background' 让 busy 块变成穿透鼠标的着色背景，
    // 这样即便选区要覆盖在已有事件上也能正常拖选
    calendarEvents.value = filtered.map(s => ({
      title: s.type === 'interview' ? '已有面试' : '忙碌',
      start: s.start,
      end: s.end,
      color: s.type === 'interview' ? '#ff9500' : '#ff4d4f',
      display: 'background',
      editable: false,
    }))

    // 编辑模式：把当前会议作为一个蓝色背景块加回去，让用户一眼看到
    // "这就是我正在改的"，同时它也是 background 不影响拖选
    if (editingId.value && form.value.timeRange) {
      calendarEvents.value.push({
        title: '当前会议',
        start: form.value.timeRange[0],
        end: form.value.timeRange[1],
        color: '#1677ff',
        display: 'background',
        editable: false,
      })
    }
  } catch (e) {
    if (seq === freeBusySeq) ElMessage.error('加载面试官日历失败，请重试')
  } finally {
    if (seq === freeBusySeq) calendarLoading.value = false
  }
}

// === data loading ===
async function loadInterviews() {
  loading.value = true
  try {
    interviews.value = (await schedulingApi.listInterviews()).items
  } catch (e) {
    console.error('loadInterviews failed:', e)
  } finally {
    loading.value = false
  }
}
async function loadOptions() {
  // 加载所有简历（存完整对象到 resumeMap 用于卡片展示）
  // 后端 page_size 上限 100，翻页拉取直到取完。
  try {
    const all = []
    let pg = 1
    while (true) {
      const d = await resumeApi.list({ page: pg, page_size: 100 })
      all.push(...d.items)
      if (all.length >= d.total || d.items.length === 0) break
      pg += 1
      if (pg > 20) break  // 硬保险，最多 2000 条
    }
    all.forEach(r => { resumeMap.value[r.id] = r })
    candidateOptions.value = all.filter(r => r.status === 'passed')
  } catch (e) {
    console.error('loadOptions failed:', e)
  }
  try {
    const d = await schedulingApi.listInterviewers()
    interviewerOptions.value = d.items
    d.items.forEach(i => { interviewerMap.value[i.id] = i.name })
  } catch (e) {
    console.error('listInterviewers failed:', e)
  }
  try {
    const d = await jobApi.list()
    jobOptions.value = (d.items || []).filter(j => j.is_active && j.competency_model_status === 'approved')
  } catch (e) {
    console.error('loadJobs failed:', e)
  }
}

async function onJobChange(jobId) {
  form.value.resume_id = null
  passedCandidatesForJob.value = []
  if (!jobId) return
  passedCandidatesLoading.value = true
  try {
    // spec 0429-D: 死卡 — 只列人工标"通过"的候选人
    const data = await matchingApi.listPassedForJob(jobId, { action: 'passed' })
    passedCandidatesForJob.value = data
    // Also update resumeMap with these candidates so card display works
    data.forEach(r => { if (!resumeMap.value[r.id]) resumeMap.value[r.id] = r })
  } catch (e) {
    console.error('listPassedForJob failed:', e)
    passedCandidatesForJob.value = []
  } finally {
    passedCandidatesLoading.value = false
  }
}
function getResume(id) { return resumeMap.value[id] || null }
function getResumeName(id) { const r = resumeMap.value[id]; return r ? r.name : `候选人#${id}` }
function getInterviewerName(id) { return interviewerMap.value[id] || `面试官#${id}` }
function formatTime(t) {
  // BUG #3 fix: 后端 SQLite 存 naive datetime (strip 时区, 数值保留为用户本地时间).
  // 不带 tz 标记时按本地时间解析, 避免被当 UTC 二次转换 (+8 偏移).
  if (!t) return ''
  return new Date(t).toLocaleString('zh-CN')
}

// 取候选人最高学历 + 对应学校（博士 > 硕士 > 本科；找不到则 fallback 到 education 字段）
function getEduInfo(resume) {
  if (!resume) return { degree: '', school: '' }
  if (resume.phd_school) return { degree: '博士', school: resume.phd_school }
  if (resume.master_school) return { degree: '硕士', school: resume.master_school }
  if (resume.bachelor_school) return { degree: '本科', school: resume.bachelor_school }
  return { degree: resume.education || '', school: '' }
}

// 选中候选人的完整对象（给新建面试弹窗用）
const selectedCandidate = computed(() => {
  if (!form.value.resume_id) return null
  return passedCandidatesForJob.value.find(r => r.id === form.value.resume_id)
    || candidateOptions.value.find(r => r.id === form.value.resume_id)
    || resumeMap.value[form.value.resume_id]
    || null
})

// === dialog ===
function openDialog(row) {
  calendarEvents.value = []; calendarLoading.value = false; loadOptions()
  passedCandidatesForJob.value = []
  if (row) {
    editingId.value = row.id
    form.value = { job_id: row.job_id || null, resume_id: row.resume_id, interviewer_id: row.interviewer_id, timeRange: [new Date(row.start_time + 'Z'), new Date(row.end_time + 'Z')], meeting_topic: row.meeting_topic || '', meeting_link: row.meeting_link || '', meeting_password: row.meeting_password || '', status: row.status, notes: row.notes || '' }
    // 编辑模式：视已存储的名称为"用户已设置"，不再自动覆盖
    meetingTopicEdited.value = !!row.meeting_topic
    if (row.interviewer_id) onInterviewerChange()
    // 编辑模式：如有 job_id，预加载通过候选人
    if (row.job_id) onJobChange(row.job_id)
  } else {
    editingId.value = null
    form.value = { job_id: null, resume_id: null, interviewer_id: null, timeRange: null, meeting_topic: '', meeting_link: '', meeting_password: '', status: 'scheduled', notes: '' }
    meetingTopicEdited.value = false
  }
  showDialog.value = true
}

// 候选人/面试官变动时，如果用户没改过会议名称，自动填默认
watch(() => [form.value.resume_id, form.value.interviewer_id], ([rid, iid]) => {
  if (meetingTopicEdited.value) return
  if (!rid || !iid) { form.value.meeting_topic = ''; return }
  const candidate = resumeMap.value[rid]?.name || candidateOptions.value.find(c => c.id === rid)?.name
  const interviewer = interviewerMap.value[iid]?.name || interviewerOptions.value.find(i => i.id === iid)?.name
  if (candidate && interviewer) {
    form.value.meeting_topic = `面试-${candidate}-${interviewer}`
  }
})

async function saveInterview() {
  if (!form.value.job_id) { ElMessage.warning('请先选择目标岗位'); return }
  if (!form.value.resume_id || !form.value.interviewer_id || !form.value.timeRange) { ElMessage.warning('请填写完整信息'); return }
  const [s, e] = form.value.timeRange
  const sd = s instanceof Date ? s : new Date(s), ed = e instanceof Date ? e : new Date(e)
  try {
    if (editingId.value) {
      await schedulingApi.updateInterview(editingId.value, { start_time: sd.toISOString(), end_time: ed.toISOString(), meeting_topic: form.value.meeting_topic, meeting_link: form.value.meeting_link, meeting_password: form.value.meeting_password, status: form.value.status, notes: form.value.notes })
      ElMessage.success('已更新')
    } else {
      await schedulingApi.createInterview({ job_id: form.value.job_id, resume_id: form.value.resume_id, interviewer_id: form.value.interviewer_id, start_time: sd.toISOString(), end_time: ed.toISOString(), meeting_topic: form.value.meeting_topic, meeting_link: form.value.meeting_link, meeting_password: form.value.meeting_password })
      ElMessage.success('已安排')
    }
    showDialog.value = false; loadInterviews()
  } catch (e) {
    if (e.response?.status === 409) { ElMessage.warning(e.response.data.detail); return }
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

// === actions ===
function buildNotificationSummaryHtml(results) {
  if (!results || results.length === 0) return '<p>无发送结果</p>'
  let html = '<table style="width:100%;border-collapse:collapse;font-size:13px;">'
  html += '<tr style="background:#f5f5f5;"><th style="padding:6px 10px;text-align:left;border-bottom:1px solid #e8e8e8;">渠道</th><th style="padding:6px 10px;text-align:left;border-bottom:1px solid #e8e8e8;">状态</th><th style="padding:6px 10px;text-align:left;border-bottom:1px solid #e8e8e8;">详情</th></tr>'
  for (const item of results) {
    const ok = item.success || item.status === 'ok' || item.status === 'success' || item.status === 'sent' || item.status === 'generated'
    const icon = ok ? '&#9989;' : '&#10060;'
    const channelMap = { email: '邮件', feishu: '飞书消息', feishu_pdf: '简历PDF', calendar: '飞书日程', template: '消息模板' }
    const channel = channelMap[item.channel] || item.channel || item.type || '未知'
    const statusMap = { sent: '已发送', generated: '已生成', failed: '发送失败' }
    const detail = statusMap[item.status] || item.message || item.detail || item.error || (ok ? '发送成功' : '发送失败')
    html += `<tr><td style="padding:4px 10px;border-bottom:1px solid #f0f0f0;">${channel}</td><td style="padding:4px 10px;border-bottom:1px solid #f0f0f0;">${icon}</td><td style="padding:4px 10px;border-bottom:1px solid #f0f0f0;">${detail}</td></tr>`
  }
  html += '</table>'
  return html
}

async function checkAlreadySent(interviewId) {
  try {
    const logs = await notificationApi.listLogs({ interview_id: interviewId })
    const items = logs.items || logs || []
    return items.length > 0
  } catch (e) {
    ElMessage.warning('无法查询通知记录，请确认后再发送')
    throw e  // 中断发送流程，不误判为"未发送"
  }
}

async function sendNotification(row) {
  if (!row.meeting_link) {
    try {
      await ElMessageBox.confirm('还没有创建腾讯会议，是否先创建会议再发送通知？', '提示', { confirmButtonText: '创建会议并发送', cancelButtonText: '取消' })
      ElMessage.info('正在创建会议…')
      await meetingApi.autoCreate(row.id)
      await loadInterviews()
      const updated = interviews.value.find(iv => iv.id === row.id)
      if (updated && updated.meeting_link) {
        const r = await notificationApi.send({ interview_id: row.id })
        ElMessageBox.alert(buildNotificationSummaryHtml(r.results), '会议已创建，通知发送结果', { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' })
        loadInterviews()
      }
    } catch (e) { if (e !== 'cancel') ElMessage.error(e.response?.data?.detail || '操作失败') }
    return
  }

  // 重复发送检测
  const alreadySent = await checkAlreadySent(row.id)
  if (alreadySent) {
    try {
      await ElMessageBox.confirm('该面试已发送过通知，确定要重新发送吗？', '重复发送提醒', { confirmButtonText: '重新发送', cancelButtonText: '取消', type: 'warning' })
    } catch { return }
  }

  try {
    const r = await notificationApi.send({ interview_id: row.id })
    ElMessageBox.alert(buildNotificationSummaryHtml(r.results), '通知发送结果', { dangerouslyUseHTMLString: true, confirmButtonText: '知道了' })
    loadInterviews()
  } catch (e) { ElMessage.error(e.response?.data?.detail || '发送失败') }
}
async function autoCreateMeeting(row) {
  try {
    await ElMessageBox.confirm('将自动创建腾讯会议，确认？', '创建会议')
    ElMessage.info('正在创建…')
    const r = await meetingApi.autoCreate(row.id)
    ElMessage.success(r.account ? `会议已创建（主持账号: ${r.account}）` : '会议已创建')
    if (r.warning) { ElMessage.warning(r.warning) }
    loadInterviews()
  } catch (e) {
    if (e === 'cancel') return
    const status = e?.response?.status
    if (status === 409) {
      ElMessageBox.alert('当前时段所有会议账号已占用，请调整面试时间或等待其他会议结束', '账号池不足', {
        type: 'warning',
        confirmButtonText: '知道了',
      })
    } else {
      ElMessage.error(e?.response?.data?.detail || '创建失败')
    }
  }
}
// === AI 面评对话框 ===
const showAiEvalDialog = ref(false)
const aiEvalInterviewId = ref(null)

function openAiEvalDialog(row) {
  aiEvalInterviewId.value = row.id
  showAiEvalDialog.value = true
}

// === 邀请信息编辑对话框 ===
const showInvitationDialog = ref(false)
const invitationText = ref('')
const invitationCurrentRow = ref(null)

function buildDefaultInvitationText(row) {
  const name = getResumeName(row.resume_id)
  const time = formatTime(row.start_time)
  const link = row.meeting_link || '待定'
  const pwd = row.meeting_password ? `\n会议密码：${row.meeting_password}` : ''
  return `面试邀请\n\n您好，诚邀您参加面试：\n候选人：${name}\n面试时间：${time}\n面试方式：线上视频面试\n会议链接：${link}${pwd}\n\n请提前5分钟入会，祝面试顺利！`
}

function openInvitationDialog(row) {
  invitationCurrentRow.value = row
  invitationText.value = buildDefaultInvitationText(row)
  showInvitationDialog.value = true
}

function resetInvitationText() {
  if (invitationCurrentRow.value) {
    invitationText.value = buildDefaultInvitationText(invitationCurrentRow.value)
    ElMessage.info('已恢复默认模板')
  }
}

function copyInvitationText() {
  const text = invitationText.value
  if (!text.trim()) { ElMessage.warning('内容为空'); return }
  const done = () => { ElMessage.success('已复制到剪贴板'); showInvitationDialog.value = false }
  navigator.clipboard.writeText(text).then(done).catch(() => {
    const ta = document.createElement('textarea')
    ta.value = text
    document.body.appendChild(ta)
    ta.select()
    document.execCommand('copy')
    document.body.removeChild(ta)
    done()
  })
}
// "确认面试官时间"按钮目前从 UI 上隐藏，后端 API 和此函数保留以便后续重新启用
// eslint-disable-next-line no-unused-vars
async function askInterviewerTime(row) {
  try {
    await ElMessageBox.confirm(`向 ${getInterviewerName(row.interviewer_id)} 发送时间确认？`, '确认')
    await schedulingApi.askInterviewerTime(row.id)
    ElMessage.success('已发送')
  } catch (e) {
    if (e === 'cancel') return
    const detail = e?.response?.data?.detail || '发送失败'
    ElMessage.error(detail)
  }
}
async function cancelInterview(id) {
  try { await ElMessageBox.confirm('确定取消？', '确认'); await schedulingApi.cancelInterview(id); ElMessage.success('已取消'); loadInterviews() } catch (e) { if (e !== 'cancel') ElMessage.error('取消失败') }
}
async function deleteInterview(row) {
  try { await ElMessageBox.confirm('确定删除？', '确认', { type: 'warning' }); await schedulingApi.deleteInterview(row.id); ElMessage.success('已删除'); loadInterviews() } catch (e) { if (e !== 'cancel') ElMessage.error('删除失败') }
}
async function clearAllInterviews() {
  try {
    const { value } = await ElMessageBox.prompt(
      '此操作将删除所有面试记录且不可恢复，请输入"确认清空"以继续',
      '危险操作',
      { confirmButtonText: '清空', cancelButtonText: '取消', type: 'error', inputPattern: /.*/, inputPlaceholder: '请输入"确认清空"' }
    )
    if (value !== '确认清空') { ElMessage.warning('输入不匹配，已取消操作'); return }
    const r = await schedulingApi.clearAllInterviews()
    ElMessage.success(`已清空 ${r.deleted} 条`)
    loadInterviews()
  } catch (e) { if (e !== 'cancel') ElMessage.error('清空失败') }
}

let timer = null
onMounted(() => { loadInterviews(); loadOptions(); timer = setInterval(loadInterviews, 15000) })
onUnmounted(() => { if (timer) clearInterval(timer) })

// 弹窗打开时暂停自动刷新，关闭时恢复
watch(showDialog, (open) => {
  if (open) {
    if (timer) { clearInterval(timer); timer = null }
  } else {
    if (!timer) { timer = setInterval(loadInterviews, 15000) }
  }
})
</script>

<style scoped>
/* 面试卡片 */
.interview-card {
  background: #fff;
  border: 1px solid #e8e8e8;
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 16px;
  transition: box-shadow 0.2s;
}
.interview-card:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,0.08);
}
.interview-card.status-cancelled {
  opacity: 0.6;
}

.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.candidate-name {
  font-size: 18px;
  font-weight: 600;
  color: #1a1a1a;
}

.card-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 24px;
  margin-bottom: 16px;
  padding-bottom: 16px;
  border-bottom: 1px solid #f0f0f0;
}
.info-row {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.info-label {
  font-size: 12px;
  color: #8c8c8c;
}
.info-value {
  font-size: 14px;
  color: #333;
}
.info-value.highlight {
  color: #1677ff;
  font-weight: 500;
}
.info-value.link {
  color: #1677ff;
  text-decoration: none;
  word-break: break-all;
  font-size: 13px;
}
.info-value.notes {
  white-space: pre-line;
  font-size: 13px;
  color: #666;
  grid-column: 1 / -1;
}

.card-footer {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}
.action-group {
  display: flex;
  align-items: center;
  gap: 6px;
}
.group-label {
  font-size: 11px;
  color: #bbb;
  background: #f5f5f5;
  padding: 2px 8px;
  border-radius: 4px;
  white-space: nowrap;
}
.card-actions-mini {
  display: flex;
  gap: 4px;
}

/* 候选人信息区块（卡片内）—— 2x2 紧凑网格 */
.candidate-block {
  background: #fafbfc;
  border-left: 3px solid #1677ff;
  border-radius: 4px;
  padding: 8px 12px;
  margin-bottom: 12px;
  font-size: 12px;
}
.cb-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 16px;
}
.cb-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.cb-label {
  color: #8c8c8c;
  font-size: 11px;
  min-width: 28px;
  flex-shrink: 0;
}
.cb-value {
  color: #333;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 新建面试弹窗候选人预览 —— 2x2 紧凑网格 */
.candidate-preview {
  background: #f7f9fc;
  border: 1px solid #e4e9f2;
  border-radius: 6px;
  padding: 10px 12px;
  margin-bottom: 14px;
  font-size: 12px;
}
.cp-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 14px;
}
.cp-item {
  display: flex;
  align-items: baseline;
  gap: 6px;
  min-width: 0;
}
.cp-label {
  color: #8c8c8c;
  font-size: 11px;
  min-width: 28px;
  flex-shrink: 0;
}
.cp-value {
  color: #333;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
  min-width: 0;
}
</style>

<style>
/* 日历样式（不能 scoped） */
.calendar-wrap {
  position: relative;
}
.calendar-container {
  border: 1px solid #e8e8e8;
  border-radius: 8px;
  background: #fff;
  overflow: hidden;
}
.calendar-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  z-index: 10;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.88);
  backdrop-filter: blur(3px);
  -webkit-backdrop-filter: blur(3px);
  animation: fadeIn 0.25s ease;
}
.calendar-overlay .overlay-icon {
  font-size: 42px;
  color: #1677ff;
}
.calendar-overlay.idle-overlay .overlay-icon {
  color: #8ba7c9;
  animation: bob 2.5s ease-in-out infinite;
}
.calendar-overlay .overlay-title {
  font-size: 15px;
  font-weight: 600;
  color: #303133;
}
.calendar-overlay .overlay-hint {
  font-size: 12px;
  color: #909399;
}
.calendar-overlay .spinning {
  animation: cal-rotate 1.1s linear infinite;
}
@keyframes cal-rotate {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
@keyframes bob {
  0%, 100% { transform: translateY(0); }
  50% { transform: translateY(-4px); }
}
@keyframes fadeIn {
  from { opacity: 0; }
  to { opacity: 1; }
}
.fc { font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 12px; }
.fc .fc-toolbar-title { font-size: 14px !important; font-weight: 600; }
.fc .fc-col-header-cell { background: #f7f8fa; font-weight: 500; padding: 4px 0; font-size: 12px; }
.fc .fc-timegrid-slot { height: 18px; }
.fc .fc-timegrid-slot-label { font-size: 10px; color: #8f959e; }
.fc .fc-highlight { background: rgba(22, 119, 255, 0.18) !important; }
.fc .fc-event { border-radius: 3px !important; border: none !important; font-size: 10px; padding: 1px 3px; }
.fc .fc-now-indicator-line { border-color: #ff4d4f; }
.fc .fc-day-today { background: rgba(22, 119, 255, 0.03) !important; }
.fc .fc-toolbar { margin-bottom: 4px !important; }
.fc .fc-scrollgrid { border: none !important; }
</style>
