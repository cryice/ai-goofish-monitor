<template>
  <Dialog v-model:open="openRef" @update:open="handleOpenChange">
    <DialogTrigger as-child>
      <slot />
    </DialogTrigger>
    <DialogContent class="max-w-3xl max-h-[90vh] overflow-y-auto">
      <DialogHeader>
        <DialogTitle>任务进度详情 - {{ taskName }}</DialogTitle>
        <DialogDescription>
          实时监控任务执行进度，包括采集页码、商品数量、处理状态等信息
        </DialogDescription>
      </DialogHeader>

      <div class="mt-4">
        <TaskProgress :task-id="taskId" />
      </div>
    </DialogContent>
  </Dialog>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import TaskProgress from './TaskProgress.vue'

interface Props {
  taskId: number
  taskName: string
  open?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  open: false
})

const emit = defineEmits<{
  'update:open': [open: boolean]
}>()

const openRef = computed({
  get: () => props.open,
  set: (value) => emit('update:open', value)
})

const handleOpenChange = (open: boolean) => {
  emit('update:open', open)
}
</script>