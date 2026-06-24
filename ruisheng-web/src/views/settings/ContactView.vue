<script setup lang="ts">
import { ref, onMounted } from "vue";
import {
  listPhones,
  addPhone,
  deletePhone,
  listEmails,
  addEmail,
  deleteEmail,
  type PhoneRecord,
  type EmailRecord,
} from "@/api/orgs";
import { useAuthStore } from "@/stores/auth";
import { useAsync } from "@/composables/useAsync";
import { useToast } from "@/composables/useToast";
import LoadingSkeleton from "@/components/LoadingSkeleton.vue";
import EmptyState from "@/components/EmptyState.vue";

const auth = useAuthStore();
const toast = useToast();
const target = ref(auth.user?.user_name ?? "");

const phones = ref<PhoneRecord[]>([]);
const emails = ref<EmailRecord[]>([]);
const newPhone = ref("");
const newEmail = ref("");
const newEmailPhone = ref("");

const phoneLoader = useAsync(() => listPhones(target.value));
const emailLoader = useAsync(() => listEmails(target.value));

async function reload(): Promise<void> {
  phones.value = await phoneLoader.run();
  emails.value = await emailLoader.run();
  syncEmailPhone();
}

onMounted(() => {
  if (target.value) void reload();
});

function validatePhone(p: string): boolean {
  return /^1[3-9]\d{9}$/.test(p);
}
function validateEmail(e: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(e);
}
function syncEmailPhone(): void {
  if (!phones.value.some((p) => p.phone === newEmailPhone.value)) {
    newEmailPhone.value = phones.value[0]?.phone ?? "";
  }
}

async function submitPhone(): Promise<void> {
  if (!validatePhone(newPhone.value)) {
    toast.error("请输入有效的中国大陆手机号");
    return;
  }
  try {
    await addPhone(target.value, newPhone.value);
    toast.success("已添加手机号");
    newPhone.value = "";
    phones.value = await phoneLoader.run();
    syncEmailPhone();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "添加失败");
  }
}

async function submitEmail(): Promise<void> {
  if (!validatePhone(newEmailPhone.value)) {
    toast.error("请选择关联手机号");
    return;
  }
  if (!validateEmail(newEmail.value)) {
    toast.error("邮箱格式不合法");
    return;
  }
  try {
    await addEmail(target.value, newEmailPhone.value, newEmail.value);
    toast.success("已添加邮箱");
    newEmail.value = "";
    emails.value = await emailLoader.run();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "添加失败");
  }
}

async function delPhone(p: PhoneRecord): Promise<void> {
  try {
    await deletePhone(target.value, p.id);
    toast.success("已删除");
    phones.value = await phoneLoader.run();
    syncEmailPhone();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "删除失败");
  }
}

async function delEmail(em: EmailRecord): Promise<void> {
  try {
    await deleteEmail(target.value, em.id);
    toast.success("已删除");
    emails.value = await emailLoader.run();
  } catch (e) {
    toast.error(e instanceof Error ? e.message : "删除失败");
  }
}
</script>

<template>
  <section class="contacts">
    <header>
      <h2>通讯录</h2>
      <label
        v-permission="['Administrators', 'GroupCompany', 'Company']"
        class="target"
      >
        管理用户 <input v-model="target" type="text" @change="reload" />
      </label>
    </header>

    <div class="grid">
      <section class="panel">
        <h3>手机号</h3>
        <form class="add-form" @submit.prevent="submitPhone">
          <input v-model="newPhone" type="text" placeholder="13800000000" />
          <button type="submit">添加</button>
        </form>
        <LoadingSkeleton v-if="phoneLoader.isPending.value" :lines="2" />
        <EmptyState v-else-if="phones.length === 0" title="暂无手机号" />
        <ul v-else class="list">
          <li v-for="p in phones" :key="p.id">
            <code>{{ p.phone }}</code>
            <button class="del" @click="delPhone(p)">删除</button>
          </li>
        </ul>
      </section>

      <section class="panel">
        <h3>邮箱</h3>
        <form class="add-form" @submit.prevent="submitEmail">
          <select v-model="newEmailPhone">
            <option value="">关联手机号</option>
            <option v-for="p in phones" :key="p.id" :value="p.phone">
              {{ p.phone }}
            </option>
          </select>
          <input
            v-model="newEmail"
            type="email"
            placeholder="user@example.com"
          />
          <button type="submit">添加</button>
        </form>
        <LoadingSkeleton v-if="emailLoader.isPending.value" :lines="2" />
        <EmptyState v-else-if="emails.length === 0" title="暂无邮箱" />
        <ul v-else class="list">
          <li v-for="e in emails" :key="e.id">
            <code>{{ e.email }}</code>
            <span class="label">{{ e.phone_number }}</span>
            <button class="del" @click="delEmail(e)">删除</button>
          </li>
        </ul>
      </section>
    </div>
  </section>
</template>

<style scoped>
.contacts {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
header {
  background: #fff;
  padding: 12px 16px;
  border-radius: 6px;
  display: flex;
  justify-content: space-between;
  align-items: center;
}
h2 {
  font-size: 18px;
}
.target {
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.target input {
  padding: 4px 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.panel {
  background: #fff;
  padding: 16px;
  border-radius: 6px;
}
.panel h3 {
  font-size: 15px;
  margin-bottom: 10px;
}
.add-form {
  display: flex;
  gap: 6px;
  margin-bottom: 12px;
}
.add-form input,
.add-form select {
  flex: 1;
  padding: 4px 8px;
  border: 1px solid #ccc;
  border-radius: 4px;
  min-width: 0;
}
.add-form button {
  background: var(--color-primary);
  color: white;
  border: none;
  padding: 4px 12px;
  border-radius: 4px;
  cursor: pointer;
}
.list {
  list-style: none;
}
.list li {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed #eee;
  font-size: 13px;
}
.label {
  color: var(--color-text-secondary);
  font-size: 12px;
  flex: 1;
}
.del {
  background: #fff;
  border: 1px solid var(--color-error);
  color: var(--color-error);
  padding: 2px 8px;
  border-radius: 3px;
  cursor: pointer;
  font-size: 11px;
}
@media (max-width: 768px) {
  .grid {
    grid-template-columns: 1fr;
  }
}
</style>
