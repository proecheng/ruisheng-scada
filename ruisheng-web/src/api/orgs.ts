import { apiClient } from "@/api/client";
import type { Authority } from "@/stores/auth";

export interface OrgUser {
  user_name: string;
  display_name?: string;
  authority: Authority;
  usr_group: string;
  company?: string;
  department?: string;
  control_authority: number;
  deleted_at?: string;
}

export interface WxGroup {
  usr_group: string;
  company_name?: string;
  sys_title?: string;
}

export interface PhoneRecord {
  id: number;
  phone: string;
}

export interface EmailRecord {
  id: number;
  phone_number: string;
  email: string;
}

interface ListEnvelope<T> {
  items: T[];
  total?: number;
}

interface PhoneWire {
  id: number;
  phone_number: string;
}

function itemsOf<T>(payload: T[] | ListEnvelope<T> | undefined): T[] {
  if (Array.isArray(payload)) return payload;
  return payload?.items ?? [];
}

export async function listUsers(): Promise<OrgUser[]> {
  const { data } = await apiClient.get("/orgs/users");
  return itemsOf(data.data as ListEnvelope<OrgUser>);
}

export async function createUser(
  u: Omit<OrgUser, "deleted_at"> & { password: string },
): Promise<OrgUser> {
  const { data } = await apiClient.post("/orgs/users", u);
  return data.data as OrgUser;
}

export async function updateUser(
  userName: string,
  u: Partial<OrgUser>,
): Promise<OrgUser> {
  const { data } = await apiClient.put(
    `/orgs/users/${encodeURIComponent(userName)}`,
    u,
  );
  return data.data as OrgUser;
}

export async function deleteUser(userName: string): Promise<void> {
  await apiClient.delete(`/orgs/users/${encodeURIComponent(userName)}`);
}

export async function listWxGroups(): Promise<WxGroup[]> {
  const { data } = await apiClient.get("/orgs/wx_groups");
  return itemsOf(data.data as ListEnvelope<WxGroup>);
}

export async function listPhones(userName: string): Promise<PhoneRecord[]> {
  const { data } = await apiClient.get(
    `/orgs/users/${encodeURIComponent(userName)}/phones`,
  );
  return itemsOf(data.data as ListEnvelope<PhoneWire>).map((p) => ({
    id: p.id,
    phone: p.phone_number,
  }));
}

export async function addPhone(
  userName: string,
  phone: string,
): Promise<PhoneRecord> {
  const { data } = await apiClient.post(
    `/orgs/users/${encodeURIComponent(userName)}/phones`,
    {
      phone_number: phone,
    },
  );
  const created = data.data as PhoneWire;
  return { id: created.id, phone: created.phone_number };
}

export async function deletePhone(userName: string, id: number): Promise<void> {
  await apiClient.delete(
    `/orgs/users/${encodeURIComponent(userName)}/phones/${id}`,
  );
}

export async function listEmails(userName: string): Promise<EmailRecord[]> {
  const { data } = await apiClient.get(
    `/orgs/users/${encodeURIComponent(userName)}/emails`,
  );
  return itemsOf(data.data as ListEnvelope<EmailRecord>);
}

export async function addEmail(
  userName: string,
  phoneNumber: string,
  email: string,
): Promise<EmailRecord> {
  const { data } = await apiClient.post(
    `/orgs/users/${encodeURIComponent(userName)}/emails`,
    {
      phone_number: phoneNumber,
      email,
    },
  );
  return data.data as EmailRecord;
}

export async function deleteEmail(userName: string, id: number): Promise<void> {
  await apiClient.delete(
    `/orgs/users/${encodeURIComponent(userName)}/emails/${id}`,
  );
}
