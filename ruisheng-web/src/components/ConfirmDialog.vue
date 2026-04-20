<script setup lang="ts">
import { ref, watch, computed } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title?: string
    message: string
    confirmText?: string
    cancelText?: string
    danger?: boolean
    typeToConfirm?: string
  }>(),
  {
    title: '确认操作',
    confirmText: '确认',
    cancelText: '取消',
    danger: false,
  },
)

const emit = defineEmits<{
  'update:modelValue': [boolean]
  confirm: []
  cancel: []
}>()

const typed = ref('')
const canConfirm = computed<boolean>(() =>
  props.typeToConfirm ? typed.value === props.typeToConfirm : true,
)

watch(
  () => props.modelValue,
  (v) => {
    if (v) typed.value = ''
  },
)

function close(): void {
  emit('update:modelValue', false)
  emit('cancel')
}
function ok(): void {
  if (!canConfirm.value) return
  emit('update:modelValue', false)
  emit('confirm')
}
</script>

<template>
  <div v-if="modelValue" class="dialog-backdrop" @click.self="close">
    <div class="dialog" role="dialog" aria-modal="true">
      <h3 class="title" :class="{ danger }">{{ title }}</h3>
      <p class="msg">{{ message }}</p>
      <div v-if="typeToConfirm" class="type-to-confirm">
        <label>请输入 <code>{{ typeToConfirm }}</code> 以确认：</label>
        <input v-model="typed" type="text" />
      </div>
      <div class="actions">
        <button class="cancel" @click="close">{{ cancelText }}</button>
        <button
          class="confirm"
          :class="{ danger }"
          :disabled="!canConfirm"
          @click="ok"
        >
          {{ confirmText }}
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 900;
}
.dialog {
  background: #fff;
  border-radius: 8px;
  padding: 20px;
  width: min(420px, 92vw);
}
.title { font-size: 16px; margin-bottom: 12px; }
.title.danger { color: var(--color-error); }
.msg { font-size: 14px; color: var(--color-text); margin-bottom: 16px; }
.type-to-confirm { margin-bottom: 16px; }
.type-to-confirm label { display: block; font-size: 13px; margin-bottom: 4px; }
.type-to-confirm input {
  width: 100%;
  padding: 6px;
  border: 1px solid #ccc;
  border-radius: 4px;
}
.actions { display: flex; justify-content: flex-end; gap: 8px; }
button { padding: 6px 14px; border-radius: 4px; border: 1px solid #ccc; cursor: pointer; }
.cancel { background: #fff; }
.confirm { background: var(--color-primary); color: white; border-color: var(--color-primary); }
.confirm.danger { background: var(--color-error); border-color: var(--color-error); }
button:disabled { opacity: 0.5; cursor: not-allowed; }
</style>
