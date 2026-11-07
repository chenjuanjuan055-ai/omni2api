"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  Activity,
  Boxes,
  CircleAlert,
  Gauge,
  Layers,
  LoaderCircle,
  Network,
  RefreshCw,
  Users,
  Zap,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { httpRequest } from "@/lib/request";
import { useAuthGuard } from "@/lib/use-auth-guard";

type Upstream = {
  key: string;
  name: string;
  href: string;
  accent: string;
  ring: string;
  badge: string;
  accounts?: number;
  healthy?: number;
  detail?: string;
  requests?: number;
  latency?: number;
  ok?: boolean;
};

type HealthSummary = {
  total: number;
  healthy: number;
  dead: number;
  status_counts?: Record<string, number>;
};

type GrokOverview = {
  summary?: {
    available?: number;
    attention?: number;
    providers?: Record<string, number>;
    issues?: Record<string, number>;
  };
  keys?: number;
};

type MinimaxDashboard = {
  activity?: Array<{ id: string; time: string; model: string; status: number; latencyMs: number; account: string }>;
  resources?: Record<string, number>;
  usage?: Record<string, any>;
  topModels?: Array<{ model: string; requests: number; tokens: number }>;
};

type MetricsSummary = {
  total: number;
  failed: number;
  success_rate: number | null;
  p95_ms?: number | null;
  rpm?: number;
};

type RecentCall = {
  source: string;
  model: string;
  account: string;
  status: number;
  latency: number;
  time: string;
};

const RING: Record<string, string> = {
  ok: "bg-emerald-500",
  active: "bg-emerald-500",
  ready: "bg-emerald-500",
  disabled: "bg-stone-400",
  cooldown: "bg-amber-500",
  invalid: "bg-rose-500",
  error: "bg-rose-500",
  expiring: "bg-amber-500",
};

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-xl border border-white/70 bg-white/80 px-3 py-2 shadow-sm">
      <div className="text-[11px] uppercase tracking-wide text-stone-400">{label}</div>
      <div className="mt-0.5 text-lg font-semibold leading-tight text-stone-900">{value}</div>
      {hint ? <div className="text-[11px] text-stone-400">{hint}</div> : null}
    </div>
  );
}

export default function HomePage() {
  const { isCheckingAuth } = useAuthGuard();
  const [health, setHealth] = useState<HealthSummary | null>(null);
  const [grok, setGrok] = useState<GrokOverview | null>(null);
  const [minimax, setMinimax] = useState<MinimaxDashboard | null>(null);
  const [metrics, setMetrics] = useState<MetricsSummary | null>(null);
  const [egress, setEgress] = useState<Record<string, any> | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const [h, g, m, ms, e] = await Promise.all([
        httpRequest<HealthSummary>("/api/accounts/health-summary"),
        httpRequest<GrokOverview>("/api/grok/overview").catch(() => null),
        httpRequest<MinimaxDashboard>("/api/minimax/dashboard").catch(() => null),
        httpRequest<MetricsSummary>("/api/metrics/summary?window_seconds=3600").catch(() => null),
        httpRequest<Record<string, any>>("/api/proxy/egress").catch(() => null),
      ]);
      setHealth(h);
      setGrok(g);
      setMinimax(m);
      setMetrics(ms);
      setEgress(e);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (isCheckingAuth) return;
    void load();
    const timer = setInterval(() => void load(), 30000);
    return () => clearInterval(timer);
  }, [isCheckingAuth]);

  const upstreams: Upstream[] = useMemo(() => {
    const gptTotal = health?.total ?? 0;
    const gptHealthy = health?.healthy ?? 0;
    const grokAvail = grok?.summary?.available ?? 0;
    const grokAttn = grok?.summary?.attention ?? 0;
    const mmRes = minimax?.resources || {};
    const mmTotal = Number(mmRes.totalAccounts ?? 0);
    const mmActive = Number(mmRes.activeAccounts ?? 0);
    const mmUsage = minimax?.usage || {};
    return [
      {
        key: "gpt",
        name: "GPT",
        href: "/accounts",
        accent: "from-indigo-500 to-indigo-600",
        ring: "ring-indigo-100",
        badge: "bg-indigo-50 text-indigo-700",
        accounts: gptTotal,
        healthy: gptHealthy,
        detail: `死号 ${health?.dead ?? 0} · 限流 ${health?.status_counts?.limited ?? 0}`,
        requests: metrics?.total ?? 0,
        latency: metrics?.p95_ms ?? undefined,
        ok: (health?.dead ?? 0) === 0,
      },
      {
        key: "grok",
        name: "Grok",
        href: "/grok",
        accent: "from-amber-400 to-amber-500",
        ring: "ring-amber-100",
        badge: "bg-amber-50 text-amber-700",
        accounts: Number(grok?.summary?.providers ? Object.values(grok.summary.providers).reduce((a, b) => a + Number(b || 0), 0) : 0),
        healthy: grokAvail,
        detail: `待处理 ${grokAttn} · 密钥 ${grok?.keys ?? 0}`,
        ok: grokAttn === 0,
      },
      {
        key: "minimax",
        name: "MiniMax",
        href: "/minimax",
        accent: "from-emerald-500 to-teal-600",
        ring: "ring-emerald-100",
        badge: "bg-emerald-50 text-emerald-700",
        accounts: mmTotal,
        healthy: mmActive,
        detail: `无效 ${Number(mmRes.invalidAccounts ?? 0)} · 模型 ${Number(mmRes.enabledModels ?? 0)}`,
        requests: Number(mmUsage.requests ?? 0),
        latency: Number(mmUsage.averageLatencyMs ?? 0) || undefined,
        ok: Number(mmRes.invalidAccounts ?? 0) === 0,
      },
    ];
  }, [health, grok, minimax, metrics]);

  const recent: RecentCall[] = useMemo(() => {
    const items: RecentCall[] = []
    for (const row of minimax?.activity || []) {
      items.push({
        source: "MiniMax",
        model: row.model,
        account: row.account,
        status: row.status,
        latency: row.latencyMs,
        time: String(row.time || "").slice(5, 19).replace("T", " "),
      });
    }
    return items.slice(0, 8);
  }, [minimax]);

  const totalAccounts = upstreams.reduce((sum, u) => sum + (u.accounts || 0), 0);
  const totalRequests = upstreams.reduce((sum, u) => sum + (u.requests || 0), 0);
  const healthRows = Object.entries(health?.status_counts || {});

  return (
    <div className="space-y-6 pb-10 pt-6">
      {/* hero */}
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-slate-900 via-slate-900 to-indigo-950 px-7 py-8 shadow-lg">
        <div className="pointer-events-none absolute -right-24 -top-24 size-72 rounded-full bg-indigo-500/20 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-32 left-1/3 size-72 rounded-full bg-emerald-500/10 blur-3xl" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div>
            <div className="text-xs font-medium uppercase tracking-[0.2em] text-indigo-300/80">
              Multi-Provider Gateway
            </div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight text-white">
              Omni2API 总览
            </h1>
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-slate-300">
              ChatGPT / Grok / MiniMax 已合并进同一个网关与同一套控制台。
              号池治理、出口调度、指标告警与自动化注册一处统一管理，持续接入更多上游。
            </p>
            <div className="mt-5 flex flex-wrap gap-2">
              {upstreams.map((u) => (
                <Link
                  key={u.key}
                  href={u.href}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium text-white ring-1 ring-white/20 transition hover:ring-white/40 ${u.accent}`}
                >
                  <span className="size-1.5 rounded-full bg-white/90" />
                  {u.name}
                </Link>
              ))}
            </div>
          </div>
          <div className="flex gap-3">
            <div className="rounded-2xl bg-white/10 px-5 py-4 ring-1 ring-white/15 backdrop-blur">
              <div className="text-[11px] uppercase tracking-wide text-slate-300">接入号池</div>
              <div className="mt-1 text-3xl font-semibold text-white">{totalAccounts.toLocaleString()}</div>
            </div>
            <div className="rounded-2xl bg-white/10 px-5 py-4 ring-1 ring-white/15 backdrop-blur">
              <div className="text-[11px] uppercase tracking-wide text-slate-300">请求总数</div>
              <div className="mt-1 text-3xl font-semibold text-white">{totalRequests.toLocaleString()}</div>
            </div>
          </div>
        </div>
      </section>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-rose-700">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </CardContent>
        </Card>
      ) : null}

      {/* 三个上游 */}
      <section className="grid gap-4 lg:grid-cols-3">
        {upstreams.map((u) => (
          <Link key={u.key} href={u.href} className="group">
            <Card
              className={`h-full overflow-hidden border-white/70 bg-white/90 shadow-sm transition group-hover:-translate-y-0.5 group-hover:shadow-md ring-1 ${u.ring}`}
            >
              <div className={`h-1.5 bg-gradient-to-r ${u.accent}`} />
              <CardContent className="space-y-4 p-5">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-2">
                      <h2 className="text-lg font-semibold text-stone-900">{u.name}</h2>
                      <span className={`rounded-md px-1.5 py-0.5 text-[10px] font-medium ${u.badge}`}>
                        {u.ok ? "正常" : "有异常"}
                      </span>
                    </div>
                    <p className="mt-0.5 text-xs text-stone-400">{u.detail}</p>
                  </div>
                  <span className="text-xs text-stone-300 transition group-hover:text-stone-500">进入 →</span>
                </div>

                <div className="grid grid-cols-2 gap-2">
                  <Stat label="账号" value={(u.accounts ?? 0).toLocaleString()} />
                  <Stat label="可用" value={(u.healthy ?? 0).toLocaleString()} />
                  <Stat label="请求" value={(u.requests ?? 0).toLocaleString()} />
                  <Stat
                    label="P95 延迟"
                    value={u.latency ? `${Math.round(u.latency)} ms` : "—"}
                    hint={u.key === "minimax" ? "平均" : undefined}
                  />
                </div>

                <div className="flex h-1.5 overflow-hidden rounded-full bg-stone-100">
                  {u.key === "gpt"
                    ? healthRows.map(([k, v]) => (
                        <div
                          key={k}
                          className={RING[k] || "bg-stone-300"}
                          style={{ width: `${Math.max(3, ((v as number) / Math.max(1, health?.total || 1)) * 100)}%` }}
                          title={`${k}: ${v}`}
                        />
                      ))
                    : (
                        <div
                          className="bg-emerald-500"
                          style={{
                            width: `${Math.min(100, ((u.healthy ?? 0) / Math.max(1, u.accounts || 1)) * 100)}%`,
                          }}
                        />
                      )}
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </section>

      {/* 中部：最近请求 + 出口池 */}
      <section className="grid gap-4 lg:grid-cols-5">
        <Card className="rounded-2xl border-white/70 bg-white/90 shadow-sm lg:col-span-3">
          <CardContent className="p-0">
            <div className="flex items-center justify-between px-5 py-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Activity className="size-4 text-stone-400" />
                最近请求
              </div>
              <span className="text-xs text-stone-400">实时</span>
            </div>
            {recent.length === 0 ? (
              <p className="px-5 pb-6 text-sm text-stone-400">暂无请求</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="bg-stone-50/80 text-xs text-stone-400">
                    <tr>
                      <th className="px-5 py-2.5 font-normal">上游</th>
                      <th className="px-4 py-2.5 font-normal">模型</th>
                      <th className="px-4 py-2.5 font-normal">账号</th>
                      <th className="px-4 py-2.5 font-normal">状态</th>
                      <th className="px-4 py-2.5 font-normal">延迟</th>
                      <th className="px-4 py-2.5 font-normal">时间</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-stone-100">
                    {recent.map((row) => (
                      <tr key={`${row.source}-${row.model}-${row.time}`} className="text-stone-600">
                        <td className="px-5 py-2.5 text-xs">
                          <span className="rounded-md bg-stone-100 px-1.5 py-0.5">{row.source}</span>
                        </td>
                        <td className="px-4 py-2.5 text-xs">{row.model}</td>
                        <td className="px-4 py-2.5 text-xs">{row.account}</td>
                        <td className="px-4 py-2.5">
                          <span className={row.status >= 400 ? "text-rose-500" : "text-emerald-600"}>{row.status}</span>
                        </td>
                        <td className="px-4 py-2.5 text-xs">{row.latency} ms</td>
                        <td className="px-4 py-2.5 text-xs text-stone-400">{row.time}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </CardContent>
        </Card>

        <div className="space-y-4 lg:col-span-2">
          <Card className="rounded-2xl border-white/70 bg-white/90 shadow-sm">
            <CardContent className="space-y-4 p-5">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Network className="size-4 text-stone-400" />
                出口治理
              </div>
              <div className="grid grid-cols-2 gap-2">
                <Stat
                  label="出口模式"
                  value={String(egress?.egress_mode ?? "-")}
                  hint={egress?.enabled ? "已启用" : "未启用"}
                />
                <Stat
                  label="自动切换"
                  value={egress?.settings?.auto_failover ? "开" : "关"}
                  hint={`最大尝试 ${egress?.settings?.max_attempts ?? "-"}`}
                />
              </div>
            </CardContent>
          </Card>

          <Card className="rounded-2xl border-white/70 bg-white/90 shadow-sm">
            <CardContent className="space-y-4 p-5">
              <div className="flex items-center gap-2 text-sm font-semibold text-stone-900">
                <Layers className="size-4 text-stone-400" />
                模型调用 Top
              </div>
              {(minimax?.topModels || []).length === 0 ? (
                <p className="text-sm text-stone-400">暂无数据</p>
              ) : (
                <div className="space-y-2">
                  {(minimax?.topModels || []).map((m) => (
                    <div key={m.model} className="flex items-center gap-3 text-sm">
                      <span className="w-32 shrink-0 truncate text-stone-600">{m.model}</span>
                      <div className="h-2 flex-1 overflow-hidden rounded-full bg-stone-100">
                        <div
                          className="h-full rounded-full bg-emerald-500"
                          style={{
                            width: `${Math.min(100, (m.requests / Math.max(1, (minimax?.topModels?.[0]?.requests || 1))) * 100)}%`,
                          }}
                        />
                      </div>
                      <span className="w-24 shrink-0 text-right text-xs text-stone-400">
                        {m.requests} 次 / {m.tokens} tok
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </section>

      {/* 底部快捷入口 */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[
          { href: "/accounts", label: "GPT 账号池", icon: Users, note: "导入 / 健康 / 分组" },
          { href: "/monitor", label: "实时监控", icon: Gauge, note: "请求与错误分类" },
          { href: "/grok/register", label: "自动注册", icon: Zap, note: "Grok 批量注册" },
          { href: "/minimax/browser-login", label: "浏览器登录", icon: Boxes, note: "MiniMax 账号登录" },
        ].map((item) => {
          const Icon = item.icon;
          return (
            <Link key={item.href} href={item.href}>
              <Card className="h-full border-white/70 bg-white/90 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md">
                <CardContent className="flex items-center gap-3 p-4">
                  <div className="flex size-9 items-center justify-center rounded-xl bg-stone-100 text-stone-600">
                    <Icon className="size-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium text-stone-800">{item.label}</div>
                    <div className="truncate text-xs text-stone-400">{item.note}</div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          );
        })}
      </section>

      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => void load()} disabled={loading}>
          {loading ? <LoaderCircle className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          立即刷新
        </Button>
      </div>
    </div>
  );
}
