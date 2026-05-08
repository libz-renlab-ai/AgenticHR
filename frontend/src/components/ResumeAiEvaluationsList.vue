<script setup>
import { ref, onMounted, watch } from 'vue'
import api from '../api'  // 项目无 @/ alias；axios 拦截器已 unwrap response.data

const props = defineProps({ resumeId: { type: Number, default: null } })
defineEmits(['open-interview'])

const items = ref([])
const loading = ref(false)

async function fetchItems() {
  if (!props.resumeId) {
    items.value = []
    return
  }
  loading.value = true
  try {
    // 拦截器已 unwrap → r 即 {scorecards: [...]}
    const r = await api.get(`/interview-eval/by-resume/${props.resumeId}`)
    items.value = r.scorecards || []
  } catch (e) {
    console.error('fetch resume ai evaluations failed:', e)
    items.value = []
  } finally {
    loading.value = false
  }
}

const recColor = (rec) => ({
  strong_hire: 'success',
  hire: 'primary',
  hold: 'warning',
  no_hire: 'danger',
}[rec] || 'info')

onMounted(fetchItems)
watch(() => props.resumeId, fetchItems)
</script>

<template>
  <div v-loading="loading" class="resume-ai-evaluations-list">
    <el-empty v-if="!loading && !items.length" description="尚无 AI 面评" :image-size="60" />
    <div v-else>
      <el-card v-for="it in items" :key="it.scorecard_id" class="ai-eval-card" shadow="hover">
        <div class="row">
          <span class="date">{{ (it.interview_date || '').slice(0, 10) }}</span>
          <el-tag :type="recColor(it.hire_recommendation)" size="small" effect="dark">
            {{ it.hire_recommendation }}
          </el-tag>
          <span class="score">总分 {{ it.avg_score }}/10</span>
          <el-button size="small" link type="primary" @click="$emit('open-interview', it.interview_id)">
            查看详情 →
          </el-button>
        </div>
      </el-card>
    </div>
    <div class="ai-disclaimer">此评价为 AI 草稿，仅供参考；最终决定权在 HR/面试官</div>
  </div>
</template>

<style scoped>
.resume-ai-evaluations-list { padding: 4px 0; }
.ai-eval-card { margin-bottom: 8px; }
.row { display: flex; gap: 12px; align-items: center; }
.date { color: #606266; font-size: 13px; min-width: 90px; }
.score { color: #303133; font-weight: 500; }
.ai-disclaimer { color: #e6a23c; font-size: 12px; margin-top: 8px; }
</style>
