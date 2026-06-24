import { describe, it, expect, beforeEach } from "vitest";
import MockAdapter from "axios-mock-adapter";
import { apiClient } from "@/api/client";
import {
  addEmail,
  addPhone,
  createUser,
  listEmails,
  listPhones,
  listUsers,
  listWxGroups,
  updateUser,
} from "@/api/orgs";

describe("orgs api", () => {
  let mock: MockAdapter;

  beforeEach(() => {
    mock = new MockAdapter(apiClient);
  });

  it("unwraps users from backend items envelope", async () => {
    mock.onGet("/orgs/users").reply(200, {
      code: 0,
      msg: "ok",
      data: {
        total: 1,
        items: [
          {
            user_name: "alice",
            authority: "User",
            usr_group: "g1",
            control_authority: 0,
          },
        ],
      },
    });
    const users = await listUsers();
    expect(users).toHaveLength(1);
    expect(users[0]?.user_name).toBe("alice");
  });

  it("unwraps wx groups from backend items envelope", async () => {
    mock.onGet("/orgs/wx_groups").reply(200, {
      code: 0,
      msg: "ok",
      data: {
        items: [{ usr_group: "g1", company_name: "集团", sys_title: "系统" }],
      },
    });
    const groups = await listWxGroups();
    expect(groups[0]?.company_name).toBe("集团");
  });

  it("posts only backend-supported fields when creating a user", async () => {
    mock.onPost("/orgs/users").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({
        user_name: "alice",
        password: "Admin@2026!",
        authority: "User",
        control_authority: 0,
        company: "润盛",
      });
      return [
        200,
        {
          code: 0,
          msg: "ok",
          data: {
            user_name: "alice",
            authority: "User",
            usr_group: "g1",
            control_authority: 0,
            company: "润盛",
          },
        },
      ];
    });

    await createUser({
      user_name: "alice",
      password: "Admin@2026!",
      authority: "User",
      control_authority: 0,
      company: "润盛",
    });
  });

  it("posts only mutable backend fields when updating a user", async () => {
    mock.onPut("/orgs/users/alice").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({
        authority: "Company",
        control_authority: 1,
      });
      return [
        200,
        {
          code: 0,
          msg: "ok",
          data: {
            user_name: "alice",
            authority: "Company",
            usr_group: "g1",
            control_authority: 1,
          },
        },
      ];
    });

    await updateUser("alice", {
      authority: "Company",
      control_authority: 1,
    });
  });

  it("maps phone_number to phone for the UI", async () => {
    mock.onGet("/orgs/users/alice/phones").reply(200, {
      code: 0,
      msg: "ok",
      data: { items: [{ id: 1, phone_number: "13800000000" }] },
    });
    const phones = await listPhones("alice");
    expect(phones).toEqual([{ id: 1, phone: "13800000000" }]);
  });

  it("posts phone_number when adding a phone", async () => {
    mock.onPost("/orgs/users/alice/phones").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({
        phone_number: "13800000000",
      });
      return [
        200,
        { code: 0, msg: "ok", data: { id: 1, phone_number: "13800000000" } },
      ];
    });
    await expect(addPhone("alice", "13800000000")).resolves.toEqual({
      id: 1,
      phone: "13800000000",
    });
  });

  it("keeps email phone_number and posts it back on create", async () => {
    mock.onGet("/orgs/users/alice/emails").reply(200, {
      code: 0,
      msg: "ok",
      data: {
        items: [
          { id: 2, phone_number: "13800000000", email: "ops@example.com" },
        ],
      },
    });
    expect(await listEmails("alice")).toEqual([
      { id: 2, phone_number: "13800000000", email: "ops@example.com" },
    ]);

    mock.onPost("/orgs/users/alice/emails").reply((config) => {
      expect(JSON.parse(config.data as string)).toEqual({
        phone_number: "13800000000",
        email: "ops@example.com",
      });
      return [
        200,
        {
          code: 0,
          msg: "ok",
          data: {
            id: 2,
            phone_number: "13800000000",
            email: "ops@example.com",
          },
        },
      ];
    });
    await expect(
      addEmail("alice", "13800000000", "ops@example.com"),
    ).resolves.toMatchObject({
      phone_number: "13800000000",
      email: "ops@example.com",
    });
  });
});
