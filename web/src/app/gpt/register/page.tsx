"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleAlert, Cpu, LoaderCircle, Play, RefreshCw, Square, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { httpRequest } from "@/lib/request";
import { cn } from "@/lib/utils";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Status = {
  ok: boolean;
  error?: string;
  count?: number;
  accounts?: Array<Record<string, any>>;
};

type CodexState = Record<string, any>;

export default function GptRegisterPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [status, setStatus] = useState<Status | null>(null);
  const [codex, setCodex] = useState<CodexState | null>(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([
        httpRequest<Status>("/api/gpt-register/status"),
        httpRequest<CodexState>("/api/gpt-register/codex").catch(() => null),
      ]);
      setStatus(s);
      setCodex(c);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
  }, []);

  useEffect(() => {
    if (!isCheckingAuth) void load();
    const timer = setInterval(() => {
      if (!isCheckingAuth) void load();
    }, 15000);
    return () => clearInterval(timer);
  }, [isCheckingAuth, load]);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await fn();
      setNotice(`${label} 已执行`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : `${label} 失败`);
    } finally {
      setBusy("");
    }
  };

  const running = Boolean(codex?.running ?? codex?.status === "running");
  const accounts = status?.accounts || [];

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">GPT 自动注册</h1>
          <p className="mt-1 text-sm text-stone-500">
            纯协议注册 + 指纹浏览器，支持 Codex 授权、CPA 批量导出与 Sub2API 上传
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
              status?.ok ? "bg-emerald-50 text-emerald-700" : "bg-rose-50 text-rose-700",
            )}
          >
            <span className={cn("size-1.5 rounded-full", status?.ok ? "bg-emerald-500" : "bg-rose-500")} />
            {status?.ok ? "运行中" : "未运行"}
          </span>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            {busy ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
            刷新
          </Button>
        </div>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-rose-700">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </CardContent>
        </Card>
      ) : null}
      {notice ? (
        <Card className="rounded-2xl border-emerald-200 bg-emerald-50/70">
          <CardContent className="p-4 text-sm text-emerald-700">{notice}</CardContent>
        </Card>
      ) : null}

      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-1">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
              <Cpu className="size-4 text-stone-400" />
              Codex 授权任务
            </div>
            <div className="space-y-1 text-sm text-stone-600">
              <div className="flex justify-between">
                <span className="text-stone-400">状态</span>
                <span>{running ? "执行中" : codex?.status ? String(codex.status) : "空闲"}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-stone-400">账号总数</span>
                <span>{status?.count ?? "-"}</span>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                onClick={() => void act("重试授权", () => httpRequest("/api/gpt-register/codex/retry", { method: "POST", body: {} }))}
                disabled={Boolean(busy)}
              >
                <Play className="size-4" />重试
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void act("停止任务", () => httpRequest("/api/gpt-register/codex/stop", { method: "POST", body: {} }))}
                disabled={Boolean(busy)}
              >
                <Square className="size-4" />停止
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => void act("清理归档", () => httpRequest("/api/gpt-register/codex/archive", { method: "POST", body: {} }))}
                disabled={Boolean(busy)}
              >
                归档
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-2">
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
              <Zap className="size-4 text-stone-400" />
              已注册账号
              <span className="text-xs font-normal text-stone-400">{accounts.length}</span>
            </div>
            {accounts.length === 0 ? (
              <p className="text-sm text-stone-400">暂无账号</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-stone-50/80 text-xs text-stone-400">
                    <tr>
                      <th className="px-4 py-2 font-normal">邮箱</th>
                      <th className="px-4 py-2 font-normal">状态</th>
                      <th className="px-4 py-2 font-normal">最近使用</th>
                      <th className="px-4 py-2 font-normal">操作</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {accounts.map((row, index) => (
                      <tr key={String(row.id ?? index)} className="text-stone-600">
                        <td className="px-4 py-2 text-xs">
                          {String(row.email ?? row.identifier ?? row.id ?? "-")}
                        </td>
                        <td className="px-4 py-2 text-xs">{String(row.status ?? row.state ?? "-")}</td>
                        <td className="px-4 py-2 text-xs">
                          {String(row.lastUsedAt ?? row.last_used_at ?? "").slice(0, 19).replace("T", " ") || "-"}
                        </td>
                        <td className="px-4 py-2">
                          <div className="flex gap-1">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                void act("探活", () =>
                                  httpRequest(`/api/gpt-register/accounts/${row.id}/probe`, { method: "POST", body: {} }),
                                )
                              }
                              disabled={Boolean(busy)}
                            >
                              探活
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() =>
                                void act("Codex 授权", () =>
                                  httpRequest(`/api/gpt-register/accounts/${row.id}/codex-agent`, { method: "POST", body: {} }),
                                )
                              }
                              disabled={Boolean(busy)}
                            >
                              授权
                            </Button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-2 p-5">
          <div className="text-sm font-semibold text-stone-900">接入说明</div>
          <p className="text-sm text-stone-500">
            注册机内置于本项目，随网关一同启动。完整面板（批量导出 CPA / 上传 Sub2API / 换邮箱 / 绑定 TOTP / 巡检）
            在注册机自身页面，账号与凭据也由它自己管理 —— 控制台只做统一视图与常用操作。
          </p>
          <Input
            readOnly
            value="注册机面板由 turb-gpt-free-register 提供（Flask，默认 http://127.0.0.1:5000）"
            className="font-mono text-xs"
          />
        </CardContent>
      </Card>
    </div>
  );
}
