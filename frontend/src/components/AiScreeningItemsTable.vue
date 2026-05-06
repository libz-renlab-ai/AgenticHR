<template>
  <div class="ai-items-table">
    <div v-for="item in items" :key="item.id" class="ai-item-row" :class="rowClass(item)">
      <div class="ai-item-head">
        <span class="ai-name">{{ item.candidate_name }}</span>
        <el-tag
          :type="scoreTagType(item.score)"
          v-if="item.score !== null && item.score !== undefined"
        >
          {{ item.score }} 分
        </el-tag>
        <el-tag v-else type="info">未评分</el-tag>
        <el-tag v-if="item.pass_flag === 1" type="success" effect="plain" size="small">
          AI 推荐通过
        </el-tag>
        <el-tag v-if="item.error" type="danger" effect="plain" size="small">
          ⚠ {{ item.error }}
        </el-tag>

        <div class="ai-actions" v-if="showActions">
          <el-button
            size="small"
            :type="item.decision_action === 'passed' ? 'success' : ''"
            @click="$emit('decide', item, 'passed')"
          >
            通过
          </el-button>
          <el-button
            size="small"
            :type="item.decision_action === 'rejected' ? 'danger' : ''"
            @click="$emit('decide', item, 'rejected')"
          >
            拒绝
          </el-button>
          <el-button
            v-if="item.decision_action"
            size="small"
            link
            @click="$emit('decide', item, null)"
          >
            撤销
          </el-button>
        </div>
      </div>
      <div class="ai-reason" v-if="item.reason">
        {{ item.reason }}
      </div>
    </div>
    <el-empty v-if="!items.length" description="暂无结果" />
  </div>
</template>

<script setup>
defineProps({
  items: { type: Array, default: () => [] },
  showActions: { type: Boolean, default: true },
})
defineEmits(['decide'])

function scoreTagType(score) {
  if (score >= 90) return 'success'
  if (score >= 75) return 'primary'
  if (score >= 60) return 'warning'
  return 'info'
}

function rowClass(item) {
  return {
    'ai-row-pass': item.decision_action === 'passed',
    'ai-row-reject': item.decision_action === 'rejected',
  }
}
</script>

<style scoped>
.ai-items-table { display: flex; flex-direction: column; gap: 8px; }
.ai-item-row {
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 10px 12px;
  background: #fff;
}
.ai-item-row.ai-row-pass { border-left: 3px solid #67c23a; }
.ai-item-row.ai-row-reject { border-left: 3px solid #f56c6c; opacity: 0.6; }
.ai-item-head {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ai-name { font-weight: 600; min-width: 80px; }
.ai-actions { margin-left: auto; display: flex; gap: 4px; }
.ai-reason {
  margin-top: 6px;
  color: #606266;
  font-size: 13px;
  line-height: 1.5;
}
</style>
