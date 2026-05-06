<template>
  <div>
    <h2 style="margin-bottom: 24px">工作台</h2>

    <!-- Stats Row -->
    <el-row :gutter="16">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="总简历数" :value="stats.totalResumes" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="已通过" :value="stats.passedResumes" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="已淘汰" :value="stats.rejectedResumes" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="待面试" :value="stats.scheduledInterviews" />
        </el-card>
      </el-col>
    </el-row>

    <!-- System Health Section -->
    <el-card shadow="never" style="margin-top: 24px">
      <template #header>
        <span style="font-weight: 600; font-size: 15px">系统状态</span>
      </template>
      <el-row :gutter="24" align="middle">
        <el-col :span="6" class="health-item">
          <span class="health-label">飞书</span>
          <el-tag :type="health.feishu ? 'success' : 'warning'" size="small">
            {{ health.feishu ? '已配置' : '未配置' }}
          </el-tag>
        </el-col>
        <el-col :span="6" class="health-item">
          <span class="health-label">AI</span>
          <el-tag :type="health.ai ? 'success' : 'warning'" size="small">
            {{ health.ai ? '已配置' : '未配置' }}
          </el-tag>
        </el-col>
        <el-col :span="6" class="health-item">
          <span class="health-label">邮箱</span>
          <el-tag :type="health.email ? 'success' : 'warning'" size="small">
            {{ health.email ? '已配置' : '未配置' }}
          </el-tag>
        </el-col>
        <el-col :span="6" class="health-item">
          <span class="health-label">腾讯会议</span>
          <el-tag :type="health.meeting ? 'success' : 'warning'" size="small">
            {{ health.meeting
              ? `已配置 (${health.meetingAccountCount}个账号)`
              : '未配置' }}
          </el-tag>
        </el-col>
      </el-row>
    </el-card>

    <!-- Quick Start Guide -->
    <el-card shadow="never" style="margin-top: 24px">
      <template #header>
        <span style="font-weight: 600; font-size: 15px">快速开始</span>
      </template>
      <div class="quick-start-list">
        <div
          v-for="step in quickStartSteps"
          :key="step.num"
          class="quick-start-item"
          :class="{ clickable: !!step.path }"
          @click="step.path && router.push(step.path)"
        >
          <span class="step-num">{{ step.num }}</span>
          <span class="step-title">{{ step.title }}</span>
          <el-icon v-if="step.path" class="step-arrow"><ArrowRight /></el-icon>
        </div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight } from '@element-plus/icons-vue'
import { resumeApi, schedulingApi, healthApi } from '../api'
import { listIntakeCandidates } from '../api/intake'

const router = useRouter()

const stats = ref({
  totalResumes: 0,
  passedResumes: 0,
  rejectedResumes: 0,
  scheduledInterviews: 0,
})

const health = ref({
  feishu: false,
  ai: false,
  email: false,
  meeting: false,
  meetingAccountCount: 0,
})

const quickStartSteps = [
  { num: 1, title: '配置系统', path: '/settings' },
  { num: 2, title: '添加面试官', path: '/interviewers' },
  { num: 3, title: '创建岗位', path: '/jobs' },
  { num: 4, title: '安装 Edge 扩展采集简历', path: null },
  { num: 5, title: '筛选 / AI 评估简历', path: '/resumes' },
  { num: 6, title: '安排面试', path: '/interviews' },
]

onMounted(async () => {
  // Fetch stats（4 个统计并行取，首屏更快）
  // 总/通过 走简历库 (四项齐全 IntakeCandidate);
  // 已淘汰走 IntakeCandidate.status='rejected' (rejected 候选不必四项齐全)
  try {
    const [all, passed, rejected, interviews] = await Promise.all([
      resumeApi.list({ page: 1, page_size: 1, intake_status: 'complete' }),
      resumeApi.list({ page: 1, page_size: 1, status: 'passed', intake_status: 'complete' }),
      listIntakeCandidates({ recruit_status: 'rejected', page: 1, size: 1 }),
      schedulingApi.listInterviews({ status: 'scheduled' }),
    ])
    stats.value.totalResumes = all.total
    stats.value.passedResumes = passed.total
    stats.value.rejectedResumes = rejected.total
    stats.value.scheduledInterviews = interviews.total
  } catch (e) {
    console.error(e)
  }

  // Fetch system health
  try {
    const data = await healthApi.check()
    const svc = data.services || {}
    health.value.feishu = !!svc.feishu?.configured
    health.value.ai = !!svc.ai?.configured
    health.value.email = !!svc.email?.configured
    health.value.meeting = !!svc.meeting?.configured
    health.value.meetingAccountCount = svc.meeting?.account_count ?? 0
  } catch (e) {
    console.error('Health check failed:', e)
  }
})
</script>

<style scoped>
.health-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
}

.health-label {
  font-size: 14px;
  color: #606266;
  min-width: 56px;
}

.quick-start-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.quick-start-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 6px;
  transition: background-color 0.15s;
}

.quick-start-item.clickable {
  cursor: pointer;
}

.quick-start-item.clickable:hover {
  background-color: #f5f7fa;
}

.step-num {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background-color: #409eff;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  flex-shrink: 0;
}

.step-title {
  font-size: 14px;
  color: #303133;
  flex: 1;
}

.step-arrow {
  color: #c0c4cc;
  font-size: 14px;
}
</style>
