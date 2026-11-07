"use client";

import { useEffect, useState } from "react";
import { CircleAlert, LoaderCircle, Save } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { fetchGrokSettings } from "@/lib/api";
import { useAuthGuard } from "@/lib/use-auth-guard";

type GrokSettings = {
  base_url: string;
  username: string;
  api_key_masked: string;
  configured: boolean;
  auto_tunnel?: boolean;
  ssh_host?: string;
  ssh_remote_port?: number;
  ssh_local_port?: number;
  tunnel_running?: boolean;
};

export default function GrokSettingsPage() {
  const { isCheckingAuth } = useAuthGuard(["admin"]);
  const [settings, setSettings] = useState<GrokSettings | null>(null);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const data = await fetchGrokSettings();
      setSettings(data.settings);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "读取失败");
    }
  };

  useEffect(() => {
    if (!isCheckingAuth) void load();
  }, [isCheckingAuth]);

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
        <h1 className="text-2xl font-semibold tracking-tight text-stone-900">Grok 连接设置</h1>
        <p className="mt-1 text-sm text-stone-500">
          grok2api 跑在 VPS 上且只监听回环地址，页面通过 SSH 隧道访问它。
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

      <div className="flex justify-end">
        <Button onClick={() => void save()} disabled={saving}>
          {saving ? <LoaderCircle className="size-4 animate-spin" /> : <Save className="size-4" />}
          保存
        </Button>
      </div>
    </div>
  );
}
