"use client";

import { useEffect, useMemo, useState } from "react";
import { CircleAlert, LoaderCircle, Save, Settings2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  fetchMinimaxSettings,
  fetchMinimaxUpstreamSettings,
  minimaxChangePassword,
  saveMinimaxUpstreamSettings,
} from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type FieldKind = "text" | "long" | "num" | "bool" | "sel";
type Field = { key: string; kind: FieldKind; label: string; options?: string[] };
type Section = { id: string; label: string; fields: Field[] };

/** minimax2api 的 settings 结构，字段与后端 SettingsDTO 一一对应。 */
const SECTIONS: Section[] = [
  {
    id: "server",
    label: "服务端",
    fields: [
      { key: "addr", kind: "text", label: "监听地址" },
      { key: "maxConcurrentRequests", kind: "num", label: "最大并发请求" },
      { key: "adminUsername", kind: "text", label: "管理员账号" },
    ],
  },
  {
    id: "upstream",
    label: "上游",
    fields: [
      { key: "baseURL", kind: "text", label: "上游地址（国际）" },
      { key: "baseURLCN", kind: "text", label: "上游地址（国内）" },
      { key: "streamBaseURL", kind: "text", label: "流式上游地址" },
      { key: "agentID", kind: "text", label: "Agent ID" },
      { key: "sessionPath", kind: "long", label: "会话路径" },
      { key: "messagePath", kind: "long", label: "消息路径" },
      { key: "userInfoPath", kind: "long", label: "用户信息路径" },
      { key: "agentListPath", kind: "long", label: "Agent 列表路径" },
      { key: "configPath", kind: "long", label: "配置路径" },
      { key: "connectionsPath", kind: "long", label: "连接路径" },
      { key: "uploadPreparePath", kind: "long", label: "上传预处理路径" },
      { key: "summariesPath", kind: "long", label: "会话摘要路径" },
      { key: "driveFilePath", kind: "long", label: "云盘文件路径" },
      { key: "modelPayload", kind: "long", label: "模型负载模板" },
      { key: "language", kind: "text", label: "语言" },
      { key: "screenWidth", kind: "num", label: "屏幕宽度" },
      { key: "screenHeight", kind: "num", label: "屏幕高度" },
      { key: "requestTimeoutSec", kind: "num", label: "请求超时（秒）" },
      { key: "streamIdleTimeoutSec", kind: "num", label: "流式空闲超时（秒）" },
      { key: "proxy", kind: "text", label: "出站代理" },
      { key: "userAgent", kind: "long", label: "User-Agent" },
    ],
  },
  {
    id: "routing",
    label: "路由调度",
    fields: [
      {
        key: "strategy",
        kind: "sel",
        label: "调度策略",
        options: ["least_inflight", "round_robin", "priority", "random"],
      },
      { key: "cooldownBaseSec", kind: "num", label: "冷却基数（秒）" },
      { key: "cooldownMaxSec", kind: "num", label: "冷却上限（秒）" },
      { key: "maxAttempts", kind: "num", label: "最大尝试次数" },
      { key: "capacityWaitSec", kind: "num", label: "容量等待（秒）" },
      { key: "stickyTTLSec", kind: "num", label: "会话粘滞 TTL（秒）" },
      { key: "preferIdle", kind: "bool", label: "优先空闲账号" },
    ],
  },
  {
    id: "audit",
    label: "审计",
    fields: [
      { key: "retentionDays", kind: "num", label: "保留天数" },
      { key: "maxRecords", kind: "num", label: "最大记录数" },
      { key: "recordBody", kind: "bool", label: "记录请求体" },
      { key: "bodyLimitBytes", kind: "num", label: "请求体上限（字节）" },
    ],
  },
  {
    id: "media",
    label: "媒体",
    fields: [
      { key: "generatedDir", kind: "text", label: "生成目录" },
      { key: "publicBaseURL", kind: "text", label: "公网地址前缀" },
      { key: "maxTotalSizeMB", kind: "num", label: "总容量上限（MB）" },
      { key: "autoDownload", kind: "bool", label: "自动下载" },
    ],
  },
  {
    id: "signin",
    label: "签到",
    fields: [
      { key: "enabled", kind: "bool", label: "启用自动签到" },
      { key: "hour", kind: "num", label: "签到小时" },
      { key: "minute", kind: "num", label: "签到分钟" },
      { key: "gapSeconds", kind: "num", label: "账号间隔（秒）" },
      { key: "timeoutSec", kind: "num", label: "单号超时（秒）" },
      { key: "skipZeroCredit", kind: "bool", label: "跳过零积分账号" },
      { key: "creditFreshMin", kind: "num", label: "积分有效期（分钟）" },
      { key: "creditRefreshMin", kind: "num", label: "积分刷新间隔（分钟）" },
      { key: "lang", kind: "text", label: "语言" },
      { key: "osName", kind: "text", label: "系统名" },
      { key: "browserName", kind: "text", label: "浏览器名" },
      { key: "browserLanguage", kind: "text", label: "浏览器语言" },
      { key: "browserPlatform", kind: "text", label: "浏览器平台" },
      { key: "deviceMemory", kind: "num", label: "内存（GB）" },
      { key: "cpuCoreNum", kind: "num", label: "CPU 核数" },
      { key: "timezoneOffsetMin", kind: "num", label: "时区偏移（分钟）" },
      { key: "statusPath", kind: "long", label: "状态查询路径" },
      { key: "claimPath", kind: "long", label: "领取路径" },
      { key: "creditPath", kind: "long", label: "积分路径" },
      { key: "creditDetailsPath", kind: "long", label: "积分明细路径" },
    ],
  },
  {
    id: "video",
    label: "视频",
    fields: [
      { key: "pluginName", kind: "text", label: "插件名" },
      { key: "optionsTag", kind: "text", label: "选项标识" },
      {
        key: "defaultRatio",
        kind: "sel",
        label: "默认比例",
        options: ["16:9", "21:9", "4:3", "1:1", "3:4", "9:16"],
      },
      {
        key: "defaultResolution",
        kind: "sel",
        label: "默认分辨率",
        options: ["480P", "768P", "2K"],
      },
      { key: "defaultDuration", kind: "num", label: "默认时长（秒）" },
      { key: "timeoutSec", kind: "num", label: "超时（秒）" },
    ],
  },
];

export default function MinimaxSettingsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [settings, setSettings] = useState<Record<string, any> | null>(null);
  const [remote, setRemote] = useState<Record<string, any>>({});
  const [saving, setSaving] = useState("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [oldPassword, setOldPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [open, setOpen] = useState<string[]>(["server", "routing"]);

  const load = async () => {
    try {
      const data = await fetchMinimaxSettings();
      setSettings(data.settings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
    try {
      const data = await fetchMinimaxUpstreamSettings();
      setRemote(data as Record<string, any>);
    } catch {
      setRemote({});
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth]);

  const setField = (section: string, key: string, value: any) => {
    setRemote((prev) => ({
      ...prev,
      [section]: { ...(prev[section] || {}), [key]: value },
    }));
  };

  const saveRemote = async () => {
    setSaving("upstream");
    try {
      await saveMinimaxUpstreamSettings(remote);
      setMessage("上游设置已保存");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "保存失败");
    } finally {
      setSaving("");
    }
  };

  const tunnel = async (action: "start" | "stop") => {
    try {
      await minimaxTunnel(action);
      await load();
      setMessage(action === "start" ? "隧道已启动" : "隧道已关闭");
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败");
    }
  };

  const toggleSection = (id: string) => {
    setOpen((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const fieldCount = useMemo(
    () => SECTIONS.reduce((sum, s) => sum + s.fields.length, 0),
    [],
  );

  if (!settings) {
    return (
      <div className="pt-6 text-sm text-stone-400">
        {error ? <span className="text-rose-500">{error}</span> : <LoaderCircle className="size-4 animate-spin" />}
      </div>
    );
  }

  return (
    <div className="space-y-5 pt-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">MiniMax 连接设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          minimax2api 是独立部署的号池网关，这里配置它的连接方式与上游参数。
        </p>
      </div>

      {error ? (
        <Card className="rounded-2xl border-rose-200 bg-rose-50/70">
          <CardContent className="flex items-start gap-2 p-4 text-sm text-rose-700">
            <CircleAlert className="mt-0.5 size-4 shrink-0" />
            <span>{error}</span>
          </CardContent>
        </Card>
      ) : null}
      {message ? (
        <Card className="rounded-2xl border-emerald-200 bg-emerald-50/70">
          <CardContent className="p-4 text-sm text-emerald-700">{message}</CardContent>
        </Card>
      ) : null}

      <div className="flex items-center gap-2 pt-2">
        <Settings2 className="size-4 text-stone-400" />
        <span className="text-sm font-semibold text-stone-900">运行参数</span>
        <span className="text-xs text-stone-400">
          {SECTIONS.length} 个分区 · {fieldCount} 个字段 · 直接改 minimax2api 的 settings
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => void load()}>重新读取</Button>
          <Button size="sm" onClick={() => void saveRemote()} disabled={saving !== ""}>
            {saving === "upstream" ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
            保存上游设置
          </Button>
        </div>
      </div>

      {SECTIONS.map((section) => {
        const values = (remote[section.id] || {}) as Record<string, any>;
        const expanded = open.includes(section.id);
        return (
          <Card key={section.id} className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
            <button
              type="button"
              onClick={() => toggleSection(section.id)}
              className="flex w-full items-center justify-between px-5 py-4 text-left"
            >
              <span className="text-sm font-semibold text-stone-900">
                {section.label}
                <span className="ml-2 text-xs font-normal text-stone-400">{section.fields.length} 项</span>
              </span>
              <span className="text-xs text-stone-400">{expanded ? "收起" : "展开"}</span>
            </button>
            {expanded ? (
              <CardContent className="grid gap-4 pt-0 md:grid-cols-2 lg:grid-cols-3">
                {section.fields.map((field) => {
                  const raw = values[field.key];
                  const value = raw === undefined || raw === null ? "" : raw;
                  const id = `mm-${section.id}-${field.key}`;
                  return (
                    <div key={field.key} className={`space-y-1.5 ${field.kind === "long" ? "md:col-span-2 lg:col-span-3" : ""}`}>
                      <Label htmlFor={id}>{field.label}</Label>
                      {field.kind === "bool" ? (
                        <label className="flex h-9 items-center gap-2 text-sm text-stone-600">
                          <input
                            id={id}
                            type="checkbox"
                            checked={Boolean(value)}
                            onChange={(e) => setField(section.id, field.key, e.target.checked)}
                          />
                          {String(value) ? "开" : "关"}
                        </label>
                      ) : field.kind === "sel" ? (
                        <Select
                          value={String(value)}
                          onValueChange={(v) => setField(section.id, field.key, v)}
                        >
                          <SelectTrigger id={id} className="h-9">
                            <SelectValue placeholder="未设置" />
                          </SelectTrigger>
                          <SelectContent>
                            {(field.options || []).map((opt) => (
                              <SelectItem key={opt} value={opt}>{opt}</SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      ) : field.kind === "long" ? (
                        <Textarea
                          id={id}
                          value={String(value)}
                          onChange={(e) => setField(section.id, field.key, e.target.value)}
                          className="min-h-16 font-mono text-xs"
                        />
                      ) : (
                        <Input
                          id={id}
                          type={field.kind === "num" ? "number" : "text"}
                          value={String(value)}
                          onChange={(e) =>
                            setField(
                              section.id,
                              field.key,
                              field.kind === "num"
                                ? Number(e.target.value.replace(/\D/g, "")) || 0
                                : e.target.value,
                            )
                          }
                          className="h-9"
                        />
                      )}
                    </div>
                  );
                })}
              </CardContent>
            ) : null}
          </Card>
        );
      })}

      <Card className="rounded-2xl border-white/80 bg-white/90 shadow-sm">
        <CardContent className="space-y-4 p-5">
          <div className="text-sm font-semibold text-stone-900">管理员口令</div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="mm-old">原密码</Label>
              <Input id="mm-old" type="password" value={oldPassword} onChange={(e) => setOldPassword(e.target.value)} className="h-9 w-48" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="mm-new">新密码</Label>
              <Input id="mm-new" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} className="h-9 w-48" />
            </div>
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                void (async () => {
                  try {
                    await minimaxChangePassword(oldPassword, newPassword);
                    setOldPassword("");
                    setNewPassword("");
                    setMessage("密码已修改");
                    setError("");
                  } catch (err) {
                    setError(err instanceof Error ? err.message : "改密失败");
                  }
                })();
              }}
            >
              修改密码
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
