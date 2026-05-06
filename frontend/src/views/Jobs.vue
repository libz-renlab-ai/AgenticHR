<template>
  <div>
    <div style="display: flex; justify-content: space-between; margin-bottom: 16px">
      <h2>岗位管理</h2>
      <el-button type="primary" @click="openNewJob">新建岗位</el-button>
    </div>

    <el-table :data="jobs" stripe v-loading="loading">
      <el-table-column prop="title" label="岗位名称" min-width="140" />
      <el-table-column prop="department" label="部门" width="90" />
      <el-table-column prop="education_min" label="最低学历" width="80" />
      <el-table-column label="工作年限" width="95">
        <template #default="{ row }">{{ row.work_years_min }}-{{ row.work_years_max }}年</template>
      </el-table-column>
      <el-table-column prop="required_skills" label="必备技能" min-width="140" show-overflow-tooltip />
      <el-table-column label="能力模型" width="95">
        <template #default="{ row }">
          <el-tag v-if="extractingJobIds.has(row.id)" type="info" size="small">
            <el-icon style="vertical-align: middle; animation: rotating 1.5s linear infinite">
              <svg viewBox="0 0 1024 1024" width="12" height="12"><path fill="currentColor" d="M512 64a32 32 0 0 1 32 32v192a32 32 0 0 1-64 0V96a32 32 0 0 1 32-32zm0 640a32 32 0 0 1 32 32v192a32 32 0 0 1-64 0V736a32 32 0 0 1 32-32zm448-192a32 32 0 0 1-32 32H736a32 32 0 0 1 0-64h192a32 32 0 0 1 32 32zm-640 0a32 32 0 0 1-32 32H96a32 32 0 0 1 0-64h192a32 32 0 0 1 32 32zM195.2 195.2a32 32 0 0 1 45.248 0L376.32 331.008a32 32 0 0 1-45.248 45.248L195.2 240.448a32 32 0 0 1 0-45.248zm452.544 452.544a32 32 0 0 1 45.248 0L828.8 783.552a32 32 0 0 1-45.248 45.248L647.744 692.992a32 32 0 0 1 0-45.248zM828.8 195.2a32 32 0 0 1 0 45.248L692.992 376.32a32 32 0 0 1-45.248-45.248L783.552 195.2a32 32 0 0 1 45.248 0zm-452.544 452.544a32 32 0 0 1 0 45.248L240.448 828.8a32 32 0 0 1-45.248-45.248l135.808-135.808a32 32 0 0 1 45.248 0z"/></svg>
            </el-icon>
            抽取中…
          </el-tag>
          <el-tag v-else :type="competencyTagType(row.competency_model_status)" size="small">
            {{ competencyTagText(row.competency_model_status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="70">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="230">
        <template #default="{ row }">
          <div style="display: flex; gap: 4px; flex-wrap: nowrap;">
            <el-button size="small" @click="editJob(row)">编辑</el-button>
            <el-button size="small" type="primary" @click="screenResumes(row.id)">筛选</el-button>
            <el-button size="small" type="danger" link @click="deleteJob(row.id)">删除</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建/编辑岗位弹窗 -->
    <el-dialog v-model="showCreateDialog" :title="editingJob ? '编辑岗位' : '新建岗位'" width="700px">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="基本信息" name="basic">
          <!-- Step 1: JD 输入（新建岗位时） -->
          <div v-if="parseStep === 'input'">
            <el-input
              v-model="jdInput"
              type="textarea"
              :rows="12"
              placeholder="粘贴岗位 JD 原文，系统将自动识别岗位名称、学历要求、薪资范围、必备技能等信息..."
            />
            <div style="margin-top: 12px; display: flex; gap: 8px; align-items: center">
              <el-button type="primary" @click="parseJd" :loading="parsing" :disabled="!jdInput.trim()">
                解析 JD
              </el-button>
              <el-button link @click="parseStep = 'review'">手动填写</el-button>
            </div>
          </div>

          <!-- Step 2: 表单（新建 review + 编辑） -->
          <div v-else>
            <el-button v-if="!editingJob" link @click="parseStep = 'input'" style="margin-bottom: 8px">
              ← 重新粘贴 JD
            </el-button>
            <el-form :model="jobForm" label-width="100px">
              <el-form-item label="岗位名称" required>
                <el-input v-model="jobForm.title" />
              </el-form-item>
              <el-form-item label="部门">
                <el-input v-model="jobForm.department" />
              </el-form-item>
              <el-form-item label="最低学历">
                <el-select v-model="jobForm.education_min" clearable>
                  <el-option label="大专" value="大专" />
                  <el-option label="本科" value="本科" />
                  <el-option label="硕士" value="硕士" />
                  <el-option label="博士" value="博士" />
                </el-select>
              </el-form-item>
              <el-form-item label="院校等级要求">
                <el-select v-model="jobForm.school_tier_min" clearable placeholder="不限">
                  <el-option label="不限" value="" />
                  <el-option label="QS 前 200 / 211 / 985 任一" value="qs_top200" />
                  <el-option label="211 及以上" value="211" />
                  <el-option label="985" value="985" />
                </el-select>
                <div style="font-size:11px;color:#999;margin-top:4px;">
                  匹配候选人会按此门槛过滤；空="不限"不卡门槛。
                </div>
              </el-form-item>
              <el-form-item label="工作年限">
                <el-col :span="11">
                  <el-input-number v-model="jobForm.work_years_min" :min="0" />
                </el-col>
                <el-col :span="2" style="text-align: center">-</el-col>
                <el-col :span="11">
                  <el-input-number v-model="jobForm.work_years_max" :min="0" />
                </el-col>
              </el-form-item>
              <el-form-item label="薪资范围">
                <el-col :span="11">
                  <el-input-number v-model="jobForm.salary_min" :min="0" :step="1000" />
                </el-col>
                <el-col :span="2" style="text-align: center">-</el-col>
                <el-col :span="11">
                  <el-input-number v-model="jobForm.salary_max" :min="0" :step="1000" />
                </el-col>
              </el-form-item>
              <el-form-item label="必备技能">
                <el-input v-model="jobForm.required_skills" placeholder="逗号分隔，如 Python,FastAPI" />
              </el-form-item>
              <el-form-item label="软性要求">
                <el-input v-model="jobForm.soft_requirements" type="textarea" :rows="3" />
              </el-form-item>
              <el-form-item label="打招呼话术">
                <el-input v-model="jobForm.greeting_templates" type="textarea" :rows="2" placeholder="竖线分隔多条" />
              </el-form-item>
              <el-form-item label="JD 原文">
                <el-input v-model="jobForm.jd_text" type="textarea" :rows="5"
                          placeholder="岗位描述原文（可编辑，保存后自动重新抽取能力模型）" />
              </el-form-item>
              <el-form-item label="批量采集标准">
                <div style="display:flex;flex-direction:column;gap:8px;">
                  <div>
                    <span style="font-size:12px;color:#666;margin-right:8px;">学校层次：</span>
                    <el-checkbox
                      v-model="batchSchool985"
                      @change="syncBatchCriteria"
                    >985</el-checkbox>
                    <el-checkbox
                      v-model="batchSchool211"
                      @change="syncBatchCriteria"
                      style="margin-left:8px;"
                    >211</el-checkbox>
                    <el-checkbox
                      v-model="batchSchoolFirst"
                      @change="syncBatchCriteria"
                      style="margin-left:8px;"
                    >双一流</el-checkbox>
                    <span style="font-size:11px;color:#999;margin-left:8px;">（全不选=不限学校）</span>
                  </div>
                  <div>
                    <span style="font-size:12px;color:#666;margin-right:8px;">最低学历：</span>
                    <el-select
                      v-model="batchEduMin"
                      @change="syncBatchCriteria"
                      style="width:120px;"
                    >
                      <el-option label="不限" :value="null" />
                      <el-option label="大专" value="大专" />
                      <el-option label="本科" value="本科" />
                      <el-option label="硕士" value="硕士" />
                      <el-option label="博士" value="博士" />
                    </el-select>
                  </div>
                </div>
              </el-form-item>
            </el-form>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="competencyLabel" name="competency" v-if="currentJobId">
          <CompetencyEditor :job-id="currentJobId" :initial-jd-text="jobForm.jd_text || ''" @status-change="onStatusChange" @extract-background="onExtractBackground" />
        </el-tab-pane>
        <el-tab-pane label="匹配候选人" name="matching" v-if="editingJob">
          <el-alert
            type="info"
            :closable="false"
            show-icon
            title="匹配规则"
            description="本列表显示四项信息齐全（到岗时间/空闲时段/实习时长/PDF）且符合该岗位「最低学历」「院校等级要求」门槛的候选人。两道门槛均为不限时，与简历库一致。"
            style="margin-bottom: 12px;"
          />
          <div v-loading="matching.loading">
            <el-alert
              type="warning" :closable="false" show-icon
              title="人工闸门: 只有标记「通过」的候选人才能进入约面试"
              style="margin-bottom: 8px;"
            />
            <el-empty v-if="!matching.items.length" description="无符合门槛的候选人" />
            <el-table v-else :data="matching.items" border stripe size="small">
              <el-table-column prop="name" label="姓名" min-width="100" />
              <el-table-column prop="phone" label="手机" width="130" />
              <el-table-column prop="email" label="邮箱" min-width="160" />
              <el-table-column prop="education" label="学历" width="70" />
              <el-table-column label="院校" min-width="160">
                <template #default="{ row }">
                  <span>{{ row.master_school || row.bachelor_school || row.phd_school || '—' }}</span>
                </template>
              </el-table-column>
              <el-table-column label="院校等级" width="90">
                <template #default="{ row }">
                  <el-tag v-if="row.school_tier === '985'" type="danger" size="small">985</el-tag>
                  <el-tag v-else-if="row.school_tier === '211'" type="warning" size="small">211</el-tag>
                  <el-tag v-else-if="row.school_tier === 'qs_top200'" type="success" size="small">QS200</el-tag>
                  <span v-else style="color:#999;">—</span>
                </template>
              </el-table-column>
              <el-table-column prop="job_intention" label="求职意向" min-width="120" />
              <el-table-column label="本岗位决策" width="220" align="center">
                <template #default="{ row }">
                  <div v-if="row.job_action === 'passed'">
                    <el-tag type="success" size="small">✓ 已通过</el-tag>
                    <el-button link size="small" @click="setMatchedDecision(row, null)" :loading="row._actionLoading">改</el-button>
                  </div>
                  <div v-else-if="row.job_action === 'rejected'">
                    <el-tag type="danger" size="small">✗ 已淘汰</el-tag>
                    <el-button link size="small" @click="setMatchedDecision(row, null)" :loading="row._actionLoading">改</el-button>
                  </div>
                  <div v-else>
                    <el-button size="small" type="success" plain @click="setMatchedDecision(row, 'passed')" :loading="row._actionLoading">通过</el-button>
                    <el-button size="small" type="danger" plain @click="setMatchedDecision(row, 'rejected')" :loading="row._actionLoading">淘汰</el-button>
                  </div>
                </template>
              </el-table-column>
            </el-table>
          </div>
        </el-tab-pane>
        <el-tab-pane label="五维能力筛选" name="five_dim" v-if="editingJob">
          <el-alert
            v-if="competencyStatus !== 'approved'"
            type="warning" :closable="false" show-icon
            title="尚未启用"
            description="请先在「能力模型」Tab 完成抽取并审核通过后，方可使用五维能力筛选。"
          />
          <template v-else>
            <el-alert
              type="info" :closable="false" show-icon
              title="筛选规则"
              description="先按硬筛通过的候选人(四项齐全 + 学历 + 院校等级)，再按能力模型 5 维(技能/经验/职级/教育/行业)加权打分。点击行可展开详情。"
              style="margin-bottom: 12px;"
            />
            <div class="matching-toolbar">
              <el-button type="primary" plain @click="recomputeFiveDim" :loading="fiveDim.recomputing">
                {{ fiveDim.items.length ? '再次分析' : '开始分析' }}
              </el-button>
              <el-select v-model="fiveDim.tagFilter" placeholder="按标签筛选" clearable @change="loadFiveDim" style="width: 160px;">
                <el-option label="高匹配" value="高匹配" />
                <el-option label="中匹配" value="中匹配" />
                <el-option label="低匹配" value="低匹配" />
                <el-option label="硬门槛未过" value="硬门槛未过" />
              </el-select>
              <span v-if="fiveDim.staleCount > 0" class="stale-warn">
                ⚠ {{ fiveDim.staleCount }} 份分数基于旧能力模型，建议再次分析
              </span>
            </div>

            <el-progress
              v-if="fiveDim.recomputing"
              :percentage="fiveDimPercent"
              :stroke-width="14"
              :format="fiveDimProgressFormat"
              style="margin-bottom: 14px"
            />

            <div v-loading="fiveDim.loading">
              <el-empty v-if="!fiveDim.items.length && !fiveDim.recomputing" description="尚无评分结果，点击「开始分析」对硬筛通过的候选人打分" />

              <div v-for="item in fiveDim.items" :key="item.id" class="matching-row" :class="{ expanded: fiveDim.expandedId === item.id }">
                <div class="matching-head">
                  <div class="m-head-left" @click="toggleFiveDimExpand(item.id)">
                    <span class="m-arrow">▶</span>
                    <span class="m-name">{{ item.resume_name }}</span>
                    <span class="m-score">{{ item.total_score.toFixed(1) }}</span>
                    <div class="m-tags">
                      <el-tag v-for="t in item.tags" :key="t" :type="tagType(t)" size="small">{{ t }}</el-tag>
                      <el-tag v-if="item.stale" type="warning" effect="plain" size="small">⚠ 过时</el-tag>
                    </div>
                  </div>
                </div>

                <transition name="expand">
                  <div v-if="fiveDim.expandedId === item.id" class="matching-detail">
                    <div class="dim-bar" v-for="dim in dimensionList(item)" :key="dim.label">
                      <span class="dim-label">{{ dim.label }} ({{ dim.weight }}%)</span>
                      <el-progress :percentage="dim.score" :color="dim.color" :stroke-width="16" />
                    </div>

                    <div v-if="item.hard_gate_passed === false" class="hard-gate-warn">
                      🛑 硬门槛未过：缺失必须项 {{ item.missing_must_haves.join(', ') }}
                    </div>

                    <div class="evidence-list">
                      <h4>证据片段</h4>
                      <div v-for="(items, dim) in item.evidence" :key="dim">
                        <div v-for="(e, i) in items" :key="i" class="evidence-item">
                          <span class="ev-dim">[{{ dim }}]</span>
                          <span class="ev-text">{{ e.text }}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                </transition>
              </div>

              <el-pagination
                v-if="fiveDim.total > fiveDim.pageSize"
                v-model:current-page="fiveDim.page"
                :page-size="fiveDim.pageSize"
                :total="fiveDim.total"
                layout="total, prev, pager, next"
                @current-change="loadFiveDim"
                style="margin-top: 12px; justify-content: flex-end"
              />
            </div>
          </template>
        </el-tab-pane>
        <el-tab-pane label="AI智能筛选" name="ai_smart" v-if="editingJob">
          <AiScreeningPanel :job-id="editingJob.id" />
        </el-tab-pane>
      </el-tabs>
      <template #footer>
        <el-button @click="showCreateDialog = false">取消</el-button>
        <el-button type="primary" @click="saveJob" v-if="activeTab === 'basic' && parseStep === 'review'">保存</el-button>
      </template>
    </el-dialog>

    <!-- 旧的硬筛 / AI 评估弹窗已废弃，改为 "筛选简历" 直接打开 "匹配候选人" Tab -->
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, onUnmounted } from 'vue'
import { ElMessage, ElMessageBox, ElNotification } from 'element-plus'
import { ArrowRight } from '@element-plus/icons-vue'
import { jobApi, competencyApi, matchingApi, weightsApi, decisionApi } from '../api'
import CompetencyEditor from '../components/CompetencyEditor.vue'
import AiScreeningPanel from '../components/AiScreeningPanel.vue'
import { extractingJobIds } from '../stores/extractingJobs.js'

const jobs = ref([])
const loading = ref(false)
const showCreateDialog = ref(false)
const editingJob = ref(null)
// const showScreenResult / screenResult 已移除 — "筛选简历" 现在直接打开匹配候选人 Tab
const aiLoading = ref(false)

const activeTab = ref('basic')
const currentJobId = ref(null)
const competencyStatus = ref('none')

// JD 解析相关
const jdInput = ref('')
const parseStep = ref('input')   // 'input' | 'review'
const parsing = ref(false)

const competencyLabel = computed(() => {
  if (competencyStatus.value === 'draft') return '能力模型 ●待审'
  if (competencyStatus.value === 'approved') return '能力模型 ✓'
  if (competencyStatus.value === 'rejected') return '能力模型 ✕'
  return '能力模型'
})

function onStatusChange(s) { competencyStatus.value = s }

function onExtractBackground({ jobId, jdText }) {
  showCreateDialog.value = false
  extractingJobIds.add(jobId)
  ElMessage.success('能力模型抽取中，完成后会通知您…')
  competencyApi.extract(jobId, jdText).then((result) => {
    extractingJobIds.delete(jobId)
    loadJobs()
    if (result?.status === 'failed') {
      ElNotification({ title: '能力模型抽取失败', message: '请重新进入岗位编辑页触发抽取', type: 'error', duration: 8000 })
    } else {
      ElNotification({ title: '能力模型已生成，待 HR 审核', message: '请前往「审核队列」完成审核后方可生效', type: 'warning', duration: 6000 })
    }
  }).catch(() => {
    extractingJobIds.delete(jobId)
    loadJobs()
    ElNotification({ title: '能力模型抽取失败', message: '请重新进入岗位编辑页触发抽取', type: 'error', duration: 8000 })
  })
}

function competencyTagType(status) {
  return { none: 'info', draft: 'warning', approved: 'success', rejected: 'danger' }[status] || 'info'
}
function competencyTagText(status) {
  return { none: '未生成', draft: '待审核', approved: '已生效', rejected: '已驳回' }[status] || '未知'
}

const defaultForm = { title: '', department: '', education_min: '', school_tier_min: '', work_years_min: 0, work_years_max: 99, salary_min: 0, salary_max: 0, required_skills: '', soft_requirements: '', greeting_templates: '', jd_text: '', batch_collect_criteria: null }
const jobForm = ref({ ...defaultForm })

const batchSchool985 = ref(false)
const batchSchool211 = ref(false)
const batchSchoolFirst = ref(false)
const batchEduMin = ref(null)

function syncBatchCriteria() {
  const tiers = []
  if (batchSchool985.value) tiers.push('985')
  if (batchSchool211.value) tiers.push('211')
  if (batchSchoolFirst.value) tiers.push('双一流')
  if (tiers.length === 0 && !batchEduMin.value) {
    jobForm.value.batch_collect_criteria = null
  } else {
    jobForm.value.batch_collect_criteria = {
      school_tiers: tiers,
      education_min: batchEduMin.value || null,
    }
  }
}

function loadBatchCriteriaFromForm() {
  const c = jobForm.value.batch_collect_criteria
  if (!c) { batchSchool985.value = false; batchSchool211.value = false; batchSchoolFirst.value = false; batchEduMin.value = null; return; }
  batchSchool985.value = (c.school_tiers || []).includes('985')
  batchSchool211.value = (c.school_tiers || []).includes('211')
  batchSchoolFirst.value = (c.school_tiers || []).includes('双一流')
  batchEduMin.value = c.education_min || null
}

async function loadJobs() {
  loading.value = true
  try {
    const data = await jobApi.list()
    jobs.value = data.items
  } catch (e) {
    ElMessage.error('加载岗位失败')
  } finally {
    loading.value = false
  }
}

function openNewJob() {
  editingJob.value = null
  jobForm.value = { ...defaultForm }
  currentJobId.value = null
  activeTab.value = 'basic'
  jdInput.value = ''
  parseStep.value = 'input'    // 新建从第一步开始
  loadBatchCriteriaFromForm()
  showCreateDialog.value = true
}

function editJob(job) {
  editingJob.value = job
  jobForm.value = { ...job }
  currentJobId.value = job.id
  activeTab.value = 'basic'
  parseStep.value = 'review'   // 编辑直接进表单
  loadBatchCriteriaFromForm()
  showCreateDialog.value = true
}

async function parseJd() {
  if (!jdInput.value.trim()) { ElMessage.warning('请先粘贴 JD 原文'); return }
  parsing.value = true
  try {
    const result = await jobApi.parseJd(jdInput.value)
    if (result.parse_success === false) {
      ElMessage.error('大模型解析失败，请手动填写岗位信息或检查 AI 配置')
    }
    // 预填表单（parse_success=false 时 jd_text 仍保留，其余字段为空需手填）
    jobForm.value = {
      title: result.title || '',
      department: result.department || '',
      education_min: result.education_min || '',
      school_tier_min: result.school_tier_min || '',
      work_years_min: result.work_years_min ?? 0,
      work_years_max: result.work_years_max ?? 99,
      salary_min: result.salary_min ?? 0,
      salary_max: result.salary_max ?? 0,
      required_skills: result.required_skills || '',
      soft_requirements: result.soft_requirements || '',
      greeting_templates: '',
      jd_text: jdInput.value,
      batch_collect_criteria: { school_tiers: [], education_min: null },
    }
    loadBatchCriteriaFromForm()
    parseStep.value = 'review'
  } catch (e) {
    ElMessage.error('解析失败：' + (e.message || e))
  } finally {
    parsing.value = false
  }
}

async function saveJob() {
  const form = jobForm.value
  if (!form.title?.trim()) { ElMessage.warning('请填写岗位名称'); return }
  if (form.work_years_min != null && form.work_years_max != null && form.work_years_max < form.work_years_min) {
    ElMessage.warning('最大工作年限不能小于最小工作年限'); return
  }
  if (form.salary_min != null && form.salary_max != null && form.salary_max > 0 && form.salary_max < form.salary_min) {
    ElMessage.warning('最高薪资不能低于最低薪资'); return
  }
  try {
    let targetJobId = null
    let jdChanged = false
    if (editingJob.value) {
      // 检测 JD 是否变更（变更则需要重抽能力模型）
      jdChanged = (jobForm.value.jd_text || '').trim() !== (editingJob.value.jd_text || '').trim()
      await jobApi.update(editingJob.value.id, jobForm.value)
      ElMessage.success('更新成功')
      showCreateDialog.value = false
      targetJobId = editingJob.value.id
    } else {
      const created = await jobApi.create(jobForm.value)
      showCreateDialog.value = false
      targetJobId = created.id
      jdChanged = !!jobForm.value.jd_text?.trim()
    }
    loadJobs()
    // 自动触发抽取：新建有 JD，或编辑改了 JD
    if (targetJobId && jdChanged && jobForm.value.jd_text?.trim()) {
      extractingJobIds.add(targetJobId)
      ElMessage.success(editingJob.value ? 'JD 已变更，正在重抽能力模型…' : '岗位已创建，正在抽取能力模型…')
      competencyApi.extract(targetJobId, jobForm.value.jd_text).then((result) => {
        extractingJobIds.delete(targetJobId)
        loadJobs()
        if (result?.status === 'failed') {
          ElNotification({ title: '能力模型抽取失败', message: '请进入岗位编辑页手动触发抽取', type: 'error', duration: 8000 })
        } else {
          ElNotification({ title: '能力模型已生成，待 HR 审核', message: '请前往「审核队列」完成审核后方可生效', type: 'warning', duration: 6000 })
        }
      }).catch((e) => {
        extractingJobIds.delete(targetJobId)
        loadJobs()
        const msg = e?.code === 'ECONNABORTED' ? 'LLM 调用超时（>120s），请稍后到岗位编辑页重试' : '请进入岗位编辑页手动触发抽取'
        ElNotification({ title: '能力模型抽取失败', message: msg, type: 'error', duration: 8000 })
      })
    }
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message || '请重试'))
  }
}

async function deleteJob(id) {
  try {
    await ElMessageBox.confirm('确定删除该岗位？', '确认')
    await jobApi.delete(id)
    ElMessage.success('已删除')
    loadJobs()
  } catch (e) {
    if (e === 'cancel') return
    if (e.response?.status === 409) {
      ElMessage.warning(e.response.data.detail)
    } else {
      ElMessage.error('删除失败')
    }
  }
}

// AI 评估按钮已废弃 — F2 用 matchingApi.recomputeJob 在岗位详情 "匹配候选人" Tab 触发

// "筛选简历" 直接进入岗位详情的 "匹配候选人" Tab (PR4: 不再需要能力模型已发布)
function screenResumes(jobId) {
  const job = jobs.value.find(j => j.id === jobId)
  if (!job) {
    ElMessage.error('岗位未找到')
    return
  }
  editingJob.value = job
  jobForm.value = { ...job }
  currentJobId.value = job.id
  activeTab.value = 'matching'
  parseStep.value = 'review'
  showCreateDialog.value = true
}

// ── 匹配候选人 Tab ──────────────────────────────────────────────────────────
const matching = ref({
  loading: false,
  items: [],
  total: 0,
  page: 1,
  pageSize: 20,
  tagFilter: '',
  expandedId: null,
  recomputing: false,
  staleCount: 0,
  pollTimer: null,
})

// ── 评分权重面板 ────────────────────────────────────────────────────────────
const weightsFields = [
  { key: 'skill_match', label: '技能匹配' },
  { key: 'experience', label: '工作经验' },
  { key: 'seniority', label: '职级对齐' },
  { key: 'education', label: '教育背景' },
  { key: 'industry', label: '行业经验' },
]

const weightsPanel = ref({
  open: false,
  custom: false,
  saving: false,
  resetting: false,
  dirty: false,
  form: { skill_match: 35, experience: 30, seniority: 15, education: 10, industry: 10 },
})

const weightsSum = computed(() => {
  const f = weightsPanel.value.form
  return (f.skill_match || 0) + (f.experience || 0) + (f.seniority || 0) + (f.education || 0) + (f.industry || 0)
})

async function loadJobWeights() {
  if (!editingJob.value) return
  try {
    const data = await weightsApi.getJobWeights(editingJob.value.id)
    weightsPanel.value.custom = data.custom
    weightsPanel.value.form = { ...data.weights }
    weightsPanel.value.dirty = false
  } catch (_e) {
    // non-fatal: fall back to defaults
  }
}

async function saveJobWeights() {
  if (!editingJob.value) return
  if (weightsSum.value !== 100) { ElMessage.warning('权重总和必须为 100'); return }
  weightsPanel.value.saving = true
  try {
    await weightsApi.setJobWeights(editingJob.value.id, weightsPanel.value.form)
    weightsPanel.value.custom = true
    weightsPanel.value.dirty = false
    ElMessage.success('自定义权重已保存，正在重新打分…')
    await recomputeMatching()
    await loadJobWeights()
  } catch (e) {
    ElMessage.error('保存失败：' + (e.response?.data?.detail || e.message || '请重试'))
  } finally {
    weightsPanel.value.saving = false
  }
}

async function resetJobWeights() {
  if (!editingJob.value) return
  weightsPanel.value.resetting = true
  try {
    await weightsApi.resetJobWeights(editingJob.value.id)
    ElMessage.success('已恢复全局默认权重')
    await loadJobWeights()
    loadMatching()
  } catch (e) {
    ElMessage.error('重置失败：' + (e.message || '请重试'))
  } finally {
    weightsPanel.value.resetting = false
  }
}

// spec 0429-D: 决策状态排序权重 — passed → undecided → rejected
// 匹配 Tab 与五维 Tab 共用此 primary 顺序, 仅 secondary key 不同 (created_at vs total_score)。
function actionOrder(action) {
  if (action === 'passed') return 0
  if (action == null) return 1
  return 2  // 'rejected'
}

function sortMatchingItems(items) {
  return [...items].sort((a, b) => {
    const ao = actionOrder(a.job_action)
    const bo = actionOrder(b.job_action)
    if (ao !== bo) return ao - bo
    return (b.created_at || '').localeCompare(a.created_at || '')
  })
}

function sortFiveDimItems(items) {
  return [...items].sort((a, b) => {
    const ao = actionOrder(a.job_action)
    const bo = actionOrder(b.job_action)
    if (ao !== bo) return ao - bo
    return (b.total_score || 0) - (a.total_score || 0)
  })
}

async function loadMatching() {
  if (!editingJob.value) return
  matching.value.loading = true
  try {
    // PR4: 新逻辑 — 直接拉"四项齐全 ∩ 学历门槛 ∩ 院校等级门槛"列表
    // spec 0429-D: 后端注入 job_action; 前端按 passed → null → rejected 排序
    const items = await matchingApi.listPassedForJob(editingJob.value.id)
    matching.value.items = sortMatchingItems(items)
    matching.value.total = items.length
    matching.value.staleCount = 0
  } catch (e) {
    ElMessage.error('加载匹配候选人失败')
  } finally {
    matching.value.loading = false
  }
}

async function setMatchedDecision(row, action) {
  if (row._actionLoading) return
  if (action === 'rejected' && row.job_action !== 'rejected') {
    try {
      await ElMessageBox.confirm(
        `确定将 "${row.name}" 在本岗位标记为淘汰？淘汰后该候选人将不能被约面试。`,
        '确认淘汰',
        { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  row._actionLoading = true
  try {
    await decisionApi.set(editingJob.value.id, row.id, action)
    row.job_action = action
    ElMessage.success(action === 'passed' ? '已标记本岗位通过' : action === 'rejected' ? '已标记本岗位淘汰' : '已清除本岗位决策')
    matching.value.items = sortMatchingItems(matching.value.items)
  } catch (e) {
    ElMessage.error('操作失败：' + (e.response?.data?.detail || e.message || '请重试'))
  } finally {
    row._actionLoading = false
  }
}

async function setJobAction(item, action) {
  if (item._actionLoading) return
  if (action === 'rejected' && item.job_action !== 'rejected') {
    try {
      await ElMessageBox.confirm(
        `确定将 "${item.resume_name}" 在本岗位标记为淘汰？淘汰后该候选人将不能被约面试。`,
        '确认淘汰',
        { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' }
      )
    } catch { return }
  }
  item._actionLoading = true
  try {
    // spec 0429-D: 优先走新决策端点 (按 candidate_id), 缺 candidate_id 则回退旧端点。
    if (item.candidate_id) {
      await decisionApi.set(editingJob.value.id, item.candidate_id, action)
    } else {
      await matchingApi.setAction(item.id, action)
    }
    item.job_action = action
    ElMessage.success(action === 'passed' ? '已标记本岗位通过' : action === 'rejected' ? '已标记本岗位淘汰' : '已清除本岗位决策')
    // spec 0429-D 收尾 P3-a: 走共享 sortFiveDimItems, 与匹配 Tab primary 顺序一致
    matching.value.items = sortFiveDimItems(matching.value.items)
  } catch (e) {
    ElMessage.error('操作失败')
  } finally {
    item._actionLoading = false
  }
}

function toggleMatchingExpand(id) {
  matching.value.expandedId = matching.value.expandedId === id ? null : id
}

function dimensionList(item) {
  return [
    { label: '技能匹配', score: item.skill_score, weight: 35, color: scoreColor(item.skill_score) },
    { label: '工作经验', score: item.experience_score, weight: 30, color: scoreColor(item.experience_score) },
    { label: '职级对齐', score: item.seniority_score, weight: 15, color: scoreColor(item.seniority_score) },
    { label: '教育背景', score: item.education_score, weight: 10, color: scoreColor(item.education_score) },
    { label: '行业经验', score: item.industry_score, weight: 10, color: scoreColor(item.industry_score) },
  ]
}

function scoreColor(s) {
  if (s >= 80) return '#67c23a'
  if (s >= 60) return '#409eff'
  if (s >= 40) return '#e6a23c'
  return '#f56c6c'
}

function tagType(tag) {
  if (tag === '高匹配') return 'success'
  if (tag === '中匹配') return 'primary'
  if (tag === '低匹配') return 'warning'
  if (tag === '不匹配' || tag.startsWith('硬门槛') || tag.startsWith('必须项缺失-')) return 'danger'
  return 'info'
}

async function recomputeMatching() {
  if (!editingJob.value) return
  try {
    matching.value.recomputing = true
    const { task_id } = await matchingApi.recomputeJob(editingJob.value.id)
    matching.value.pollTimer = setInterval(async () => {
      const s = await matchingApi.recomputeStatus(task_id)
      if (!s.running) {
        clearInterval(matching.value.pollTimer)
        matching.value.pollTimer = null
        matching.value.recomputing = false
        ElMessage.success(`打分完成：${s.completed}/${s.total}`)
        loadMatching()
      }
    }, 2000)
  } catch (e) {
    matching.value.recomputing = false
    ElMessage.error('启动打分失败')
  }
}

// ── 五维能力筛选 Tab ────────────────────────────────────────────────────────
const fiveDim = ref({
  loading: false,
  items: [],
  total: 0,
  page: 1,
  pageSize: 20,
  tagFilter: '',
  expandedId: null,
  recomputing: false,
  staleCount: 0,
  pollTimer: null,
  taskTotal: 0,
  taskCompleted: 0,
  taskFailed: 0,
})

const fiveDimPercent = computed(() => {
  const t = fiveDim.value.taskTotal
  if (!t) return 0
  return Math.min(100, Math.round((fiveDim.value.taskCompleted / t) * 100))
})

function fiveDimProgressFormat() {
  const c = fiveDim.value.taskCompleted
  const t = fiveDim.value.taskTotal
  return t ? `${c} / ${t}` : '准备中…'
}

async function loadFiveDim() {
  if (!editingJob.value) return
  fiveDim.value.loading = true
  try {
    const data = await matchingApi.listByJob(editingJob.value.id, {
      page: fiveDim.value.page,
      page_size: fiveDim.value.pageSize,
      tag: fiveDim.value.tagFilter || undefined,
    })
    fiveDim.value.items = data.items
    fiveDim.value.total = data.total
    fiveDim.value.staleCount = (data.items || []).filter(i => i.stale).length
  } catch (e) {
    ElMessage.error('加载五维评分结果失败')
  } finally {
    fiveDim.value.loading = false
  }
}

function toggleFiveDimExpand(id) {
  fiveDim.value.expandedId = fiveDim.value.expandedId === id ? null : id
}

async function recomputeFiveDim() {
  if (!editingJob.value) return
  if (competencyStatus.value !== 'approved') {
    ElMessage.warning('请先发布能力模型')
    return
  }
  try {
    fiveDim.value.recomputing = true
    fiveDim.value.taskCompleted = 0
    fiveDim.value.taskFailed = 0
    const { task_id, total } = await matchingApi.recomputeJob(editingJob.value.id)
    fiveDim.value.taskTotal = total
    if (!total) {
      fiveDim.value.recomputing = false
      ElMessage.warning('当前无候选人通过硬筛，可在「匹配候选人」Tab 检查门槛设置')
      return
    }
    fiveDim.value.pollTimer = setInterval(async () => {
      try {
        const s = await matchingApi.recomputeStatus(task_id)
        fiveDim.value.taskCompleted = s.completed
        fiveDim.value.taskFailed = s.failed
        fiveDim.value.taskTotal = s.total
        if (!s.running) {
          clearInterval(fiveDim.value.pollTimer)
          fiveDim.value.pollTimer = null
          fiveDim.value.recomputing = false
          ElMessage.success(`分析完成：${s.completed}/${s.total}${s.failed ? `（失败 ${s.failed}）` : ''}`)
          loadFiveDim()
        }
      } catch (_e) {
        // 单次轮询失败不致命，继续等
      }
    }, 2000)
  } catch (e) {
    fiveDim.value.recomputing = false
    ElMessage.error('启动分析失败：' + (e.response?.data?.detail || e.message || '请重试'))
  }
}

function jumpToResume(resumeId, source, offset) {
  const [start, end] = offset
  window.open(`/#/resumes/${resumeId}?highlight=${start},${end}&source=${source}`, '_blank')
}

watch(activeTab, async (tab) => {
  if (tab === 'matching' && editingJob.value) {
    loadMatching()
    loadJobWeights()
    weightsPanel.value.open = false
  }
  if (tab === 'five_dim' && editingJob.value && competencyStatus.value === 'approved') {
    await loadFiveDim()
    // 列表为空 → 自动启动一次分析（首次进入），有结果时只展示不重算
    if (!fiveDim.value.items.length && !fiveDim.value.recomputing) {
      recomputeFiveDim()
    }
  }
})

onUnmounted(() => {
  if (matching.value.pollTimer) clearInterval(matching.value.pollTimer)
  if (fiveDim.value.pollTimer) clearInterval(fiveDim.value.pollTimer)
})

onMounted(loadJobs)
</script>

<style scoped>
@keyframes rotating {
  from { transform: rotate(0deg); }
  to   { transform: rotate(360deg); }
}

.matching-toolbar {
  display: flex; gap: 8px; align-items: center;
  margin-bottom: 16px;
}
.stale-warn { color: #e6a23c; font-size: 13px; margin-left: 12px; }

.matching-row {
  border: 1px solid #ebeef5; border-radius: 6px;
  margin-bottom: 8px; overflow: hidden;
}
.matching-row.expanded { border-color: #409eff; }
.matching-head {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 16px;
  transition: background 0.1s;
}
.m-head-left {
  flex: 1; display: flex; align-items: center; gap: 12px;
  cursor: pointer; min-width: 0;
}
.m-head-left:hover { opacity: 0.85; }
.m-arrow {
  font-size: 12px; color: #909399; transition: transform 0.2s; flex-shrink: 0;
}
.matching-row.expanded .m-arrow { transform: rotate(90deg); color: #409eff; }
.m-head-actions {
  display: flex; gap: 4px; align-items: center; flex-shrink: 0;
}
.m-name { font-weight: 600; min-width: 80px; }
.m-score { font-size: 20px; color: #409eff; font-weight: 700; min-width: 60px; }
.m-tags { display: flex; gap: 4px; flex-wrap: wrap; }

.matching-detail { padding: 12px 16px; background: #fafbfc; border-top: 1px solid #f0f2f5; }
.dim-bar { display: flex; align-items: center; gap: 12px; margin-bottom: 6px; }
.dim-label { width: 140px; font-size: 12px; color: #606266; }
.dim-bar :deep(.el-progress) { flex: 1; }

.hard-gate-warn {
  margin-top: 10px; padding: 8px 12px;
  background: #fef0f0; color: #c45656;
  border-radius: 4px; font-size: 13px;
}
.evidence-list { margin-top: 12px; }
.evidence-list h4 { margin: 6px 0; color: #606266; font-size: 13px; }
.evidence-item { display: flex; gap: 6px; align-items: center; font-size: 13px; margin: 3px 0; }
.ev-dim { color: #909399; font-size: 11px; min-width: 70px; }
.ev-text { flex: 1; }

.job-action-bar {
  display: flex; align-items: center; gap: 8px;
  margin-top: 14px; padding-top: 10px;
  border-top: 1px dashed #e8e8e8;
}
.job-action-label { font-size: 12px; color: #909399; }

.expand-enter-active, .expand-leave-active { transition: all 0.2s ease-out; overflow: hidden; }
.expand-enter-from, .expand-leave-to { max-height: 0; opacity: 0; }
.expand-enter-to, .expand-leave-from { max-height: 800px; opacity: 1; }

/* 评分权重面板 */
.weights-panel {
  border: 1px solid #e4e7ed;
  border-radius: 6px;
  background: #fafbfc;
  padding: 14px 16px;
  margin-bottom: 14px;
}
.weights-status { margin-bottom: 10px; font-size: 13px; }
.weights-status-custom { color: #67c23a; font-weight: 600; }
.weights-status-global { color: #909399; }
.weights-inputs {
  display: flex; flex-wrap: wrap; gap: 12px 24px;
  margin-bottom: 8px;
}
.weights-field {
  display: flex; align-items: center; gap: 6px;
}
.weights-field label { font-size: 12px; color: #606266; min-width: 60px; }
.weights-sum-hint {
  font-size: 12px; color: #909399; margin-bottom: 10px;
}
.weights-sum-error { color: #f56c6c; font-weight: 600; }
.weights-actions { display: flex; gap: 8px; }
</style>
