# GPT 自动注册：收藏项目评估（2026-11-08）

皇上收藏里 GPT 注册相关的项目，逐一评估后的结论。

## 候选清单

| 项目 | ★ | 语言 | 最近更新 | 形态 | 结论 |
|---|---|---|---|---|---|
| **myfanhua/turb-gpt-free-register** | 1777 | Python | 2026-11-03 | **自带 WebUI**（Flask，76 个 REST 路由） | ✅ **首选** |
| **2951461586/GPT-Register-Tool** | 604 | .NET WPF + Python | 2026-11-02 | 桌面应用（有 win-x64 release） | ⚠️ 功能最全但接入成本最高 |
| **akihitohyh/chatgpt-register-sub2api** | 308 | Python | 2026-07-02 | CLI 流水线 | ✅ 轻量闭环，但 4 个月没更新 |
| **daimon3332/OutlookRegister** | 1117 | Python | 2026-07-27 | CLI | ○ 配套（邮箱是注册前提） |
| **tiantianGPU/reg-factory** | 2080 | Python | 2026-10-19 | CLI + windows release | ○ 是 **Claude** 注册，非 GPT |

---

## 首选：turb-gpt-free-register

**已在本机实测跑通**：`D:\xi\turb-gpt-free-register`，venv + 依赖装好，
`python web.py --port 5000` → WebUI 正常起来（Flask，临时授权码）。

### 它是什么

- **纯协议**注册（curl_cffi 做 TLS 指纹伪装）+ **指纹浏览器**（cloakbrowser / playwright-stealth）
- 自带 `sentinel/` 目录（OpenAI sentinel 校验逻辑）
- `core/` 62 个模块、`tests/` 67 个用例（工程化程度不错）
- 依赖 15 个（curl_cffi / wreq / pyotp / cloakbrowser / playwright / selenium…），`uv` 装完约 1 分钟

### 76 个 REST 路由覆盖的能力

**注册与账号治理**
```
/api/accounts                      账号列表
/api/accounts/<id>/archive          归档
/api/accounts/<id>/change-email     换邮箱（+ 日志）
/api/accounts/<id>/note             备注
/api/accounts/<id>/secret           取凭据
/api/accounts/<id>/totp-setup       绑定 TOTP
/api/accounts/<id>/delete
/api/accounts/archive-bulk / delete-bulk / note-bulk / secret-bulk / totp-setup-bulk
```

**Codex 授权与导出（关键）**
```
/api/codex                         Codex 任务
/api/accounts/<id>/codex-agent     Codex 授权
/api/accounts/<id>/codex-agent/upload-sub2   ← 上传到 Sub2API
/api/accounts/<id>/codex-agent/download      ← 下载 CPA
/api/accounts/download-cpa-bulk              批量导出 CPA
/api/codex/download-bulk / download-from-cpa
```

**体检与运维**
```
/api/accounts/check-plan(-bulk)    套餐检查
/api/accounts/check-live-bulk      存活检查
/api/accounts/live-check-log       存活日志
/api/accounts/inspections          巡检
/api/accounts/plan-check-status
/api/accounts/totp-setup-log / change-email-log
/api/codex/retry(-bulk) / stop / retry-log / archive / reset-export
/api/cloudmail/domains / gen-token
```

### 为什么它最合适

1. **形态与 grok-register 一致** —— 自带 WebUI + REST API，
   可以像 grok-register 那样并入 `upstreams/`，我们控制台再加一页代理它。
2. **能直接接上我们已有的通道** —— `upload-sub2` / `download-cpa` 与
   chatgpt-pool-ng 的 **sub2api / CPA 导入**是天然闭环。
3. 纯 Python，依赖轻，Windows 上直接跑。

---

## 次选：chatgpt-register-sub2api（★308）

**从 basketikun/chatgpt2api（就是我们的基座）提取的注册机核心**，只有 2 个依赖
（curl-cffi + pyyaml）。

流水线：
```
[1] 注册账号（Outlook 邮箱池接验证码）
[2] 加入 K12 母号 workspace
[3] 重新登录 + 选 Team 空间 → 拿 AT_team / RT_team / ID_team
[4] 导出 Sub2API JSON
```

配置就是一个 `config.yaml`（邮箱池 / 代理 / K12 workspace ID / 线程数）。

⚠️ 自带警告：**「Team 空间重新登录步骤需要实际 OpenAI 环境调试确认 workspace 选择 API」**
—— 第 3 步作者自己没验证过。而这一步恰恰是拿到 team scope token 的关键。
⇒ 可以用，但第 3 步要自己啃。

---

## GPT-Register-Tool（★604）：功能最全，但接入成本最高

- **.NET 8 WPF 桌面应用**（`GPTRegisterTool.slnx`）+ Python 辅助脚本
- **SmsWorkbench**（135 文件）—— **短信接码**
- **sms_tool/**（335 文件）
- 支付方式配置（`payment_methods.json`）、代理池（`start_proxy_pool.py` / `verify_proxy.py`）
- 官方有 `GPT-Register-Tool-win-x64-v2026.11.02.zip` release

⇒ 能力最全（邮箱 OTP + 短信 + 支付 + 代理池 + 账号管理），
但它是**桌面应用**不是服务，要接进我们的控制台得走 IPC（仓库里有 `ipc_schema.json`）
或重写成服务。工作量最大。

---

## 建议的接入顺序

1. **先并 turb**（自带 WebUI，形态已知）→ `upstreams/gpt-register/` + 控制台加「GPT → 注册」页
2. **再补 GPT 账号导出闭环**：turb 的 `upload-sub2` 对我们现有的 sub2api 导入通道
3. **chatgpt-register-sub2api 作为备选/补充**：它专攻 K12 workspace + Team scope，
   turb 若覆盖不到就补它
4. **GPT-Register-Tool 放最后**（需要重写/包 IPC），除非确实需要短信接码和支付
5. 邮箱侧可顺带看 `OutlookRegister`（微软账号批量注册 + OAuth2 refresh_token），
   补齐注册所需的邮箱池

## 待验证（下一步）

- turb 的注册主流程在当前环境能不能跑通（需要：可用邮箱池 + 住宅出口 IP + 代理池）
- 现有 4 个 ChatGPT 号的 refresh_token 能不能直接喂给 turb 当邮箱池
- K12 workspace 那条线（chatgpt-register-sub2api）与 turb 的关系：功能重叠还是互补
