"use client";

import { useCallback, useEffect, useState } from "react";
import { CircleAlert, LoaderCircle, Play, RefreshCw, Square, Zap } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { httpRequest } from "@/lib/request";
import { useAuthGuard } from "@/lib/use-auth-guard";

type TokenItem = {
  email: string;
  created_at: string;
  token_masked: string;
  token_len: number;
};

type RegStatus = {
  running: boolean;
  total: number;
  done: number;
  ok: number;
  started_at: number;
  finished_at: number;
  log: string[];
  token_count: number;
  recent_tokens: TokenItem[];
  exit_pool?: {
    ready?: boolean;
    ports?: number;
    exits?: number;
    distinct_ips?: number;
  };
  settings?: {
    duck_master_set?: boolean;
    python_exists?: boolean;
    script_exists?: boolean;
    proxy?: string;
    bridge_port?: number;
  };
};

export default function MinimaxRegisterPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [status, setStatus] = useState<RegStatus | null>(null);
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    try {
      const s = await httpRequest<RegStatus>("/api/minimax-reg/status");
      setStatus(s);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
  }, []);

  useEffect(() => {
    if (!isCheckingAuth) void load();
    const timer = setInterval(() => {
      if (!isCheckingAuth) void load();
    }, 5000);
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

  const running = Boolean(status?.running);
  const tokens = status?.recent_tokens ?? [];

  return (
    <div className="space-y-5 pt-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 自动注册</h1>
          <p className="mt-1 text-sm text-stone-500">
            批量注册 MiniMax 账号并产出凭据，支持实时查看进度与结果
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className={
              running
                ? "rounded-full bg-amber-50 px-3 py-1 text-xs text-amber-700"
                : "rounded-full bg-stone-100 px-3 py-1 text-xs text-stone-500"
            }
          >
            {running ? "注册中" : "空闲"}
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
              <Zap className="size-4 text-stone-400" />
              发起注册
            </div>
            <div className="space-y-1 text-sm text-stone-600">
              <div className="flex justify-between">
                <span className="text-stone-400">本次目标</span>
                <span>{status?.total || 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-stone-400">已成功</span>
                <span>{status?.ok ?? 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-stone-400">凭据总数</span>
                <span>{status?.token_count ?? 0}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-stone-400">可用出口</span>
                <span>
                  {status?.exit_pool?.ready
                    ? `${status.exit_pool.exits ?? 0} 条 / ${status.exit_pool.distinct_ips ?? 0} 个不同线路`
                    : "未就绪"}
                </span>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-1 text-xs text-stone-500">
                数量
                <Input
                  type="number"
                  min={1}
                  max={20}
                  value={count}
                  onChange={(e) => setCount(Number(e.target.value) || 1)}
                  className="w-16"
                />
              </label>
              <Button
                size="sm"
                disabled={Boolean(busy) || running}
                onClick={() =>
                  void act("发起注册", () =>
                    httpRequest("/api/minimax-reg/start", { method: "POST", body: { count } }),
                  )
                }
              >
                <Play className="size-4" />开始
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={Boolean(busy) || !running}
                onClick={() => void act("停止", () => httpRequest("/api/minimax-reg/stop", { method: "POST" }))}
              >
                <Square className="size-4" />停止
              </Button>
            </div>
            {status?.settings?.duck_master_set === false ? (
              <p className="text-xs text-amber-600">
                尚未配置邮箱服务主密钥，请先在设置页填写后再发起注册。
              </p>
            ) : null}
          </CardContent>
        </Card>

        <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm lg:col-span-2">
          <CardContent className="space-y-3 p-5">
            <div className="text-sm font-semibold text-stone-900">运行日志</div>
            <div className="max-h-[340px] overflow-auto rounded-xl bg-stone-50 p-3 font-mono text-xs text-stone-600">
              {(status?.log ?? []).length > 0 ? (
                (status?.log ?? []).map((line, idx) => (
                  <div key={idx} className="whitespace-pre-wrap break-all">
                    {line}
                  </div>
                ))
              ) : (
                <div className="text-stone-400">暂无日志</div>
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-3 p-5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm font-semibold text-stone-900">最近产出</div>
            <span className="text-xs text-stone-400">共 {status?.token_count ?? 0} 条</span>
          </div>
          {tokens.length > 0 ? (
            <div className="space-y-1.5">
              {tokens.map((t) => (
                <div
                  key={`${t.email}-${t.created_at}`}
                  className="flex flex-wrap items-center gap-2 rounded-xl bg-stone-50 px-3 py-2 text-xs text-stone-600"
                >
                  <span className="font-mono">{t.email}</span>
                  <span className="rounded-full bg-white px-2 py-0.5">{t.created_at}</span>
                  <span className="font-mono text-stone-400">{t.token_masked}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-stone-400">暂无产出。</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
