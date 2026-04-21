<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { login } from '@/api/auth'

const router = useRouter()
const auth = useAuthStore()

const userName = ref('')
const password = ref('')
const loading = ref(false)
const errorMsg = ref<string | null>(null)

async function submit(): Promise<void> {
  if (!userName.value || !password.value) {
    errorMsg.value = '请输入用户名和密码'
    return
  }
  loading.value = true
  errorMsg.value = null
  try {
    const session = await login({ user_name: userName.value, password: password.value })
    auth.setSession(session)
    await router.push('/dashboard')
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <form class="login-form" @submit.prevent="submit">
    <label class="field">
      <span>用户名</span>
      <input v-model="userName" data-testid="login-username" type="text" autocomplete="username" required />
    </label>
    <label class="field">
      <span>密码</span>
      <input v-model="password" data-testid="login-password" type="password" autocomplete="current-password" required />
    </label>
    <p v-if="errorMsg" data-testid="login-error" class="err" role="alert">{{ errorMsg }}</p>
    <button type="submit" data-testid="login-submit" :disabled="loading" class="submit">
      {{ loading ? '登录中…' : '登录' }}
    </button>
  </form>
</template>

<style scoped>
.login-form { display: flex; flex-direction: column; gap: 16px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field input {
  padding: 8px 12px;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 14px;
}
.err { color: var(--color-error); font-size: 13px; }
.submit {
  padding: 10px;
  background: var(--color-primary);
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 14px;
  cursor: pointer;
}
.submit:disabled { opacity: 0.6; cursor: not-allowed; }
</style>
