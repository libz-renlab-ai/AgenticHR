<script setup>
import { ref, onMounted, onUnmounted, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import api from '../api'  // axios 实例（项目内 api/index.js 默认导出，拦截器已 unwrap response.data）

const props = defineProps({ interviewId: { type: Number, required: true } })

const job = ref(null)
const scorecard = ref(null)
const transcript = ref([])
const polling = ref(null)
const videoRef = ref(null)

const statusText = computed(() => ({
  pending: '等待开始',
  downloading: '下载录像中…',
  transcribing: '转录中…',
  scoring: 'AI 评分中…',
  done: '已完成',
  failed: '失败',
  cancelled: '已取消',
}[job.value?.status] || '未触发'))

const statusColor = computed(() => ({
  pending: 'info', downloading: 'primary', transcribing: 'primary',
  scoring: 'primary', done: 'success', failed: 'danger', cancelled: 'info',
}[job.value?.status] || 'info'))

const avgScore = computed(() => {
  if (!scorecard.value?.dimensions?.length) return 0
  const sum = scorecard.value.dimensions.reduce((a, d) => a + d.score, 0)
  return (sum / scorecard.value.dimensions.length).toFixed(1)
})

const recommendationColor = computed(() => ({
  strong_hire: 'success', hire: 'primary',
  hold: 'warning', no_hire: 'danger',
}[scorecard.value?.hire_recommendation] || 'info'))

async function fetchJob() {
  try {
    // 拦截器已 unwrap → 直接拿到 {job: {...}|null}
    const r = await api.get(`/interview-eval/by-interview/${props.interviewId}`)
    job.value = r.job
    if (job.value?.status === 'done') {
      await fetchScorecard()
      await fetchTranscript()
      stopPoll()
    } else if (['failed', 'cancelled'].includes(job.value?.status)) {
      stopPoll()
    } else if (job.value) {
      startPoll()
    }
  } catch (e) {
    console.error('fetchJob failed:', e)
  }
}

async function fetchScorecard() {
  try {
    scorecard.value = await api.get(`/interview-eval/${job.value.id}/scorecard`)
  } catch (e) {
    // 404 = scorecard 尚未生成；其他错误吞掉避免污染面板
    if (e?.response?.status !== 404) console.error('fetchScorecard failed:', e)
  }
}

async function fetchTranscript() {
  if (!scorecard.value?.transcript_available) return
  try {
    transcript.value = await api.get(`/interview-eval/${job.value.id}/transcript`)
  } catch (e) {
    if (e?.response?.status !== 404) console.error('fetchTranscript failed:', e)
  }
}

function startPoll() {
  stopPoll()
  polling.value = setInterval(fetchJob, 5000)
}
function stopPoll() {
  if (polling.value) { clearInterval(polling.value); polling.value = null }
}

async function startAnalyze() {
  try {
    await api.post('/interview-eval/start', { interview_id: props.interviewId })
    ElMessage.success('已开始分析，请稍候')
    fetchJob()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '启动失败')
  }
}

async function cancelJob() {
  try {
    await ElMessageBox.confirm('确认取消该 AI 面评任务？', '提示', { type: 'warning' })
  } catch {
    return  // 用户取消确认框
  }
  try {
    await api.post(`/interview-eval/${job.value.id}/cancel`)
    ElMessage.info('已请求取消')
    fetchJob()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || '取消失败')
  }
}

function jumpTo(ms) {
  if (videoRef.value) {
    videoRef.value.currentTime = ms / 1000
    videoRef.value.play().catch(() => {})
  }
}

onMounted(fetchJob)
onUnmounted(stopPoll)
watch(() => props.interviewId, fetchJob)
</script>

<template>
  <div class="ai-interview-eval-panel">
    <div class="status-bar">
      <el-tag :type="statusColor" size="large">{{ statusText }}</el-tag>
      <el-button v-if="!job" type="primary" @click="startAnalyze">分析面试</el-button>
      <el-button
        v-if="['pending','downloading','transcribing','scoring'].includes(job?.status)"
        @click="cancelJob"
      >取消</el-button>
      <el-button v-if="['failed','cancelled'].includes(job?.status)" type="primary" @click="startAnalyze">
        重跑
      </el-button>
      <span v-if="job?.error_msg" class="err-msg">{{ job.error_msg }}</span>
    </div>

    <div v-if="scorecard" class="result-area">
      <div class="left-pane">
        <div class="hire-banner">
          <el-tag :type="recommendationColor" size="large" effect="dark">
            {{ scorecard.hire_recommendation }}
          </el-tag>
          <span class="avg">总分 {{ avgScore }} / 10</span>
        </div>

        <h3>维度评分</h3>
        <div v-for="(d, i) in scorecard.dimensions" :key="i" class="dim-card">
          <div class="dim-head">
            <strong>{{ d.name }}</strong>
            <el-progress :percentage="d.score * 10" :format="() => d.score + '/10'" />
          </div>
          <div class="dim-reason">{{ d.reasoning }}</div>
          <div class="evidence">
            <el-tag
              v-for="(ev, j) in d.evidence" :key="j"
              size="small" @click="jumpTo(ev.start_ms)" class="ev-chip"
            >▶ {{ (ev.start_ms / 1000).toFixed(1) }}s · {{ ev.text.slice(0, 30) }}…</el-tag>
          </div>
        </div>

        <div class="three-cols">
          <div><h4>优势</h4><ul><li v-for="(s, i) in scorecard.strengths" :key="i">{{ s }}</li></ul></div>
          <div><h4>风险</h4><ul><li v-for="(s, i) in scorecard.risks" :key="i">{{ s }}</li></ul></div>
          <div><h4>追问点</h4><ul><li v-for="(s, i) in scorecard.followups" :key="i">{{ s }}</li></ul></div>
        </div>

        <el-collapse>
          <el-collapse-item title="完整转录稿">
            <div v-for="(seg, i) in transcript" :key="i"
                 :class="['transcript-bubble', seg.speaker]" @click="jumpTo(seg.start_ms)">
              <span class="t-time">[{{ (seg.start_ms / 1000).toFixed(1) }}s]</span>
              <span class="t-speaker">{{ seg.speaker === 'interviewer' ? '面试官' : '候选人' }}</span>
              <span class="t-text">{{ seg.text }}</span>
            </div>
          </el-collapse-item>
        </el-collapse>

        <div class="ai-disclaimer">此评价为 AI 草稿，仅供参考；最终决定权在 HR/面试官</div>
      </div>

      <div class="right-pane" v-if="scorecard.recording_available">
        <video ref="videoRef" :src="`/api/interview-eval/${job.id}/recording`"
               controls style="width:100%;max-height:480px"></video>
      </div>
    </div>
  </div>
</template>

<style scoped>
.ai-interview-eval-panel { padding: 16px; }
.status-bar { display: flex; gap: 12px; align-items: center; margin-bottom: 16px; }
.err-msg { color: #f56c6c; font-size: 13px; }
.result-area { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.hire-banner { display: flex; align-items: center; gap: 16px; margin-bottom: 16px; }
.avg { font-size: 18px; color: #303133; font-weight: 500; }
.dim-card { background: #f5f7fa; padding: 12px; margin-bottom: 12px; border-radius: 4px; }
.dim-head { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.dim-reason { font-size: 13px; color: #606266; margin: 6px 0; }
.evidence { display: flex; flex-wrap: wrap; gap: 6px; }
.ev-chip { cursor: pointer; }
.three-cols { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 16px 0; }
.transcript-bubble { padding: 6px 10px; margin: 4px 0; border-radius: 4px; cursor: pointer; }
.transcript-bubble.interviewer { background: #ecf5ff; }
.transcript-bubble.candidate { background: #f0f9eb; }
.t-time { color: #909399; font-size: 12px; margin-right: 6px; }
.t-speaker { font-weight: 500; margin-right: 6px; }
.ai-disclaimer { color: #e6a23c; font-size: 13px; margin-top: 16px; }
</style>
