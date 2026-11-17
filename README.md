# 语音输入（voice-type）

在 Ubuntu 24.04 + GNOME Wayland 上使用的本地听写工具：**按住键盘右侧 Alt 说话，松开后自动识别，并把文字输入到当前光标位置**。

识别后端二选一：

- **本地模型**：faster-whisper，CPU 运行，完全离线（默认 `large-v3-turbo`）
- **在线 API**：任何 OpenAI 兼容的 `/v1/audio/transcriptions` 服务（OpenAI、SiliconFlow、Groq、vLLM、自建 faster-whisper-server 等）

提供 **deb 安装包**（内置完整运行环境，离线安装），首次启动有设置向导，可选模型下载来源：**Hugging Face（国外）/ hf-mirror 镜像 / 魔搭 ModelScope（国内）**。

程序不会开机自启动、没有系统托盘；需要时从应用菜单打开，关闭窗口即退出。

> 当前版本：**v0.2.0** · [下载 deb 安装包](https://github.com/kangfp/voice-type/releases/latest)

---

## 目录

- [功能特性](#功能特性)
- [环境要求](#环境要求)
- [安装](#安装)
- [首次启动向导](#首次启动向导)
- [日常使用](#日常使用)
- [设置说明](#设置说明)
- [在线 API 配置](#在线-api-配置)
- [配置文件](#配置文件)
- [系统架构](#系统架构)
- [模块说明](#模块说明)
- [关键实现细节](#关键实现细节)
- [命令行](#命令行)
- [识别准确率与性能](#识别准确率与性能)
- [故障排查](#故障排查)
- [隐私与数据](#隐私与数据)
- [已知限制](#已知限制)
- [卸载](#卸载)
- [开发与测试](#开发与测试)

---

## 功能特性

- **deb 安装包**：内置 Python 运行环境与全部依赖，`dpkg -i` 离线安装，无需 uv/pip/联网装依赖
- **首次启动向导**：环境检查（含一键修复权限）→ 模型来源选择 → 模型下载 → 快捷键注册
- **三个模型下载源**：Hugging Face / hf-mirror 镜像 / 魔搭 ModelScope，自动探测网络并推荐可达的源
- **右 Alt 按住说话**：按下右 Alt 开始录音，松开自动停止、识别并输入
- **备用快捷键 `Super+Alt+V`**：按一次开始、再按一次停止，适合长段听写
- 中文 / 英文 / 中英混合，自动补标点，中文半角标点自动转全角
- 本地模型可选 `tiny / base / small / medium / large-v3-turbo / large-v3`
- **内置 uinput 虚拟键盘注入**：不依赖 ydotool 守护进程（ydotool 仅作备用）
- 默认开启 VAD 静音过滤，避免在静音或噪声上凭空生成文字
- 可配置识别提示词（专业术语、人名、项目名），提升专有名词识别率
- 在线模式支持任意 Base URL、API Key、模型名
- 录音期间窗口保持可见并显示状态，松开后自动隐藏，把焦点还给目标应用
- 粘贴后自动恢复原剪贴板内容
- 桌面通知反馈录音 / 识别 / 输入结果
- GTK4 + libadwaita 控制窗口：开始/停止、切换后端、模型预下载、环境自检
- 单实例运行：重复打开只会唤出已有窗口

---

## 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Ubuntu 24.04 amd64（GNOME 46，Wayland；X11 理论上也可用） |
| 音频 | PipeWire（`pw-record` 可用，Ubuntu 24.04 默认满足） |
| 用户组 | 当前用户在 `input` 组（读取键盘事件、写 `/dev/uinput` 都需要；向导会引导修复） |
| 系统工具 | `wl-clipboard`、`libnotify-bin`、`pipewire-bin`（deb 依赖自动安装） |
| GTK | `gir1.2-gtk-4.0`、`gir1.2-adw-1`、`python3-gi`（deb 依赖自动安装） |

- **deb 安装**：Python 运行环境和 faster-whisper 等依赖已内置在安装包里，不需要预装 Python 库或 uv
- **源码安装**：需要 Python 3.12 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)
- `ydotool` 为可选备用注入方式，不装也能用（内置 uinput 注入）

> 加入 `input` 组后**必须注销并重新登录**才会生效。

---

## 安装

### 方式一：deb 安装包（推荐）

从 [Releases 页面](https://github.com/kangfp/voice-type/releases/latest) 下载 `voice-type_0.2.0_amd64.deb`（约 99MB），然后：

```bash
sudo dpkg -i voice-type_0.2.0_amd64.deb
sudo apt-get install -f -y   # 自动补齐系统依赖
```

安装包内容：

- `/opt/voice-type/venv`：完整 Python 运行环境（faster-whisper、modelscope 等，离线可用）
- `/usr/bin/voice-type`：命令行入口
- 应用菜单入口「语音输入」、图标
- `/usr/lib/udev/rules.d/60-voice-type-uinput.rules`：允许 `input` 组写 `/dev/uinput`

装完从应用菜单打开「语音输入」，**首次启动自动进入设置向导**（见下节）。

### 方式二：源码安装（开发者）

```bash
git clone https://github.com/kangfp/voice-type.git
cd voice-type
bash install.sh
```

安装脚本会：apt 装系统依赖 → 创建 `.venv`（复用系统 PyGObject）→ 写入 `~/.local/bin/voice-type` → 安装菜单入口与图标 → 注册备用快捷键 `Super+Alt+V`。

**两种方式都不会创建开机自启动项。**

### 模型下载

模型在首次启动向导里选择来源并下载（默认 `large-v3-turbo`，约 1.6GB）。也可以命令行提前下载：

```bash
voice-type download-model                        # 按配置的来源下载当前模型
voice-type download-model --source modelscope    # 指定魔搭源
voice-type download-model --model small --source huggingface
```

三个来源：

| 来源 | 适用 | 说明 |
|---|---|---|
| `huggingface` | 国外用户 | 官方源 huggingface.co |
| `hf-mirror` | 国内备用 | 社区镜像 hf-mirror.com，稳定性因网络而异 |
| `modelscope` | 国内用户（推荐） | 魔搭官方源，CTranslate2 格式仓库与 HF 同源，实测速度快且稳定 |

---

## 首次启动向导

配置里 `setup_completed=false` 时（首次安装或手动重跑），打开程序会进入四步向导：

1. **环境检查**：列出依赖与权限检查结果；缺少 `input` 组权限时提供「修复权限」按钮（通过 pkexec 提权执行 `usermod -aG input`，修复后需注销重登）
2. **模型来源**：三选一；进入本步时自动探测三个源的连通性并给出推荐（只推荐、不覆盖已选来源，可点「采用推荐」切换）
3. **下载模型**：选择模型大小（显示体积与说明）、磁盘空间检查、进度条、可取消、断点续传；已缓存的模型直接跳过；也可以打开「跳过下载，使用在线 API」
4. **快捷键与完成**：一键注册 `Super+Alt+V`，点「完成」保存设置进入主界面

主界面菜单里有「重新运行设置向导」，可随时重走一遍（换来源、换模型、修权限）。

---

## 日常使用

### 打开与退出

- **打开**：应用菜单搜索「语音输入」，或终端运行 `voice-type show`
- **使用**：打开后程序在后台待命，同时显示控制窗口
- **退出**：直接点窗口右上角关闭按钮，或菜单「退出」。关闭即彻底退出，右 Alt 和快捷键随之停止

程序启动后不会出现在 GNOME 顶部状态栏（GNOME 46 没有系统托盘），运行期间只出现在任务栏（Dash）和 `Alt+Tab` 中。

### 按住右 Alt 听写（推荐）

1. 在任意应用（编辑器、浏览器、聊天窗口、终端……）里把光标放到要输入的位置
2. **按住键盘右侧的 Alt 键（AltGr 位置）**，看到「正在录音」通知后开始说话
3. 说完**松开右 Alt**
4. 窗口自动隐藏，稍等片刻，文字出现在光标处

录音期间控制窗口保持可见并显示「正在录音…」，松开后才隐藏。

### 使用 Super+Alt+V 听写

1. 按一次 `Super+Alt+V` 开始录音
2. 再按一次停止并识别

`Super` 就是键盘上的 Windows 徽标键。此方式适合长段听写，程序必须已经打开。

### 在终端里使用

终端里 `Ctrl+V` 通常不是粘贴，需要在设置里把「粘贴快捷键」改为 **Ctrl+Shift+V**。

---

## 设置说明

打开控制窗口后，在「识别设置」分组修改，点最下面的 **「保存设置」** 立即生效，无需重启。

| 设置项 | 说明 | 默认值 |
|---|---|---|
| 识别方式 | 本地模型 / 在线 API | 本地模型 |
| 本地模型 | 越大越准越慢；推荐 `large-v3-turbo` | `large-v3-turbo` |
| 模型下载来源 | Hugging Face / hf-mirror 镜像 / 魔搭 ModelScope | `hf-mirror`（向导里可改） |
| 语言 | `zh` / `en` / `auto` | `zh` |
| 识别提示词 | 补充术语、人名、项目名，作为解码偏置 | 空 |
| 麦克风设备 | 留空使用系统默认；可用 `pactl list short sources` 查看 | 空 |
| API Base URL | 在线服务根地址（程序自动补 `/audio/transcriptions`） | 空 |
| API Key | Bearer Token，自建服务可留空 | 空 |
| 在线模型名 | 服务端要求的模型名 | `whisper-1` |
| 粘贴快捷键 | `Ctrl+V` 或 `Ctrl+Shift+V`（终端用后者） | `Ctrl+V` |
| 粘贴后恢复原剪贴板 | 粘贴完成后把剪贴板恢复为原内容 | 开 |
| 自动去除静音（VAD） | 过滤静音，减少凭空生成文字 | 开 |
| 右 Alt 按住说话 | 关闭后只能用快捷键切换录音 | 开 |

提示词示例（结构设计相关）：

```text
结构计算书 施工图 剪力墙 框架柱 梁板 配筋 荷载 抗震 混凝土强度等级 桩基
```

---

## 在线 API 配置

在设置里选择「在线 API」，填写 Base URL、API Key、模型名。

| 服务 | Base URL | 模型名示例 |
|---|---|---|
| OpenAI | `https://api.openai.com/v1` | `whisper-1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` | `FunAudioLLM/SenseVoiceSmall` |
| Groq | `https://api.groq.com/openai/v1` | `whisper-large-v3-turbo` |
| 自建 faster-whisper-server / vLLM | `http://127.0.0.1:8000/v1` | 按服务要求填写 |

Base URL 规则：

- 填到 `/v1`：自动追加 `/audio/transcriptions`
- 填完整 `/audio/transcriptions`：原样使用
- 只填主机名：自动追加 `/v1/audio/transcriptions`

在线模式下音频会上传到所填服务，本地不保存录音。

---

## 配置文件

配置文件位于：

```text
~/.config/voice-type/config.json
```

完整示例：

```json
{
  "backend": "local",
  "local_model": "large-v3-turbo",
  "model_source": "modelscope",
  "setup_completed": true,
  "compute_type": "int8",
  "beam_size": 5,
  "vad_filter": true,
  "language": "zh",
  "api_base_url": "",
  "api_key": "",
  "api_model": "whisper-1",
  "source": "",
  "paste_shortcut": "ctrl+v",
  "restore_clipboard": true,
  "push_to_talk": true,
  "hotkey": "<Super><Alt>v",
  "initial_prompt": ""
}
```

字段说明：

- `backend`：`local` 或 `online`
- `local_model`：faster-whisper 模型名
- `model_source`：模型下载来源，`huggingface` / `hf-mirror` / `modelscope`
- `setup_completed`：首次启动向导是否已完成；改为 `false` 后重启程序会重跑向导
- `compute_type`：CTranslate2 量化类型，CPU 默认 `int8`
- `beam_size`：束搜索宽度，默认 5，增大可能略准但更慢
- `vad_filter`：是否启用 Silero VAD 静音过滤
- `language`：识别语言，`auto` 为自动检测
- `source`：录音设备名，留空用系统默认
- `paste_shortcut`：`ctrl+v` 或 `ctrl+shift+v`
- `restore_clipboard`：粘贴后是否恢复原剪贴板
- `push_to_talk`：是否启用右 Alt 按住说话
- `hotkey`：备用全局快捷键（GNOME gsettings 语法）
- `initial_prompt`：本地识别补充提示词；中文会自动在前面加上「以下是普通话的句子，请使用简体中文和标点。」

直接编辑配置文件后，需要退出并重新打开程序才生效。

---

## 系统架构

### 总览

```text
┌──────────────────────────────┐
│ 右 Alt 按下 / 松开            │
│ /dev/input/by-id/*-event-kbd │
└──────────────┬───────────────┘
               │ 只读监听（不 grab）
               ▼
┌──────────────────────────────┐        ┌───────────────────────────┐
│ PushToTalkListener           │        │ Super+Alt+V               │
│ • 识别 KEY_RIGHTALT          │        │ GNOME 自定义快捷键         │
│ • 短按 <350ms 取消           │        │ 执行 voice-type toggle    │
└──────────────┬───────────────┘        └─────────────┬─────────────┘
               │ start / stop / cancel                │ IPC
               ▼                                      ▼
        ┌─────────────────────────────────────────────────┐
        │              Controller（状态机）                 │
        │  IDLE → RECORDING → TRANSCRIBING → IDLE          │
        └──────┬───────────────┬───────────────┬──────────┘
               │               │               │
               ▼               ▼               ▼
        ┌────────────┐  ┌──────────────┐  ┌──────────────────┐
        │ pw-record  │  │ transcribe   │  │ inject_text      │
        │ 16k 单声道 │  │ local/online │  │ wl-copy + uinput │
        └────────────┘  └──────────────┘  └──────────────────┘
               │               │               │
               └───────────────┴───────────────┘
                               ▼
                    notify-send 桌面通知
```

### 进程模型

- 主进程是一个 GTK4 / libadwaita 应用，同时承担后台服务
- 通过 Unix 域套接字提供控制接口：

```text
$XDG_RUNTIME_DIR/voice-type.sock
（通常为 /run/user/1000/voice-type.sock）
```

- 协议：一行 JSON 请求 → 一行 JSON 响应
- 支持命令：`toggle`、`start`、`stop`、`status`、`show`、`quit`
- 单实例：使用 GApplication/DBus 的唯一实例模式，重复启动时由已有实例显示窗口，第二个进程直接退出
- 控制套接字带所有权标记：只有真正 `bind` 成功的实例才会在退出时删除套接字，避免误删其他实例的套接字
- `voice-type show` 在服务未运行时会拉起可见窗口；`toggle/start/stop` 不会自动拉起服务

### 录音 → 识别 → 输入 数据流

```text
右 Alt 按下
  → Controller.start()
  → 启动 pw-record 子进程，写 /tmp/voice-type-XXXX.raw（s16le, 16kHz, 单声道）

右 Alt 松开
  → Controller.stop()，状态切到 TRANSCRIBING
  → SIGINT 停止 pw-record
  → 裸 PCM 封装为 WAV
  → 本地：faster-whisper 读取 WAV → 转 float32 → 识别
    在线：httpx multipart 上传 WAV → 解析 JSON text
  → 中文标点规范化
  → wl-copy 写入剪贴板
  → uinput 虚拟键盘模拟 Ctrl+V（或 Ctrl+Shift+V；ydotool 备用）
  → 约 1.2 秒后恢复原剪贴板
  → 删除临时 WAV
  → 状态回到 IDLE，通知「已输入」
```

### 状态机

```text
        按下右 Alt / toggle
   ┌──────────────────────────┐
   ▼                          │
 IDLE ──start()──> RECORDING ──stop()──> TRANSCRIBING ──完成──> IDLE
                    │
                    └── 短按 <350ms / cancel() ──> IDLE
```

- 录音中再次触发 `start()` 会被忽略
- 识别中触发 `toggle()` 只提示「正在识别」，不会重复开始
- 录音短于 0.25 秒视为无效，直接丢弃

---

## 模块说明

```text
voice-type/
├── pyproject.toml              # 项目元数据与依赖
├── build-deb.sh                # deb 打包脚本
├── packaging/                  # deb 控制文件、维护脚本、udev 规则、桌面入口
├── install.sh                  # 源码安装脚本（开发者）
├── uninstall.sh                # 源码安装卸载脚本
├── README.md                   # 本文档
├── icons/voice-type.svg        # 应用图标
├── tests/test_units.py         # 单元测试
└── src/voice_type/
    ├── __init__.py
    ├── __main__.py             # python -m voice_type
    ├── cli.py                  # 命令行入口与子命令
    ├── config.py               # 配置读写、默认值、路径
    ├── audio.py                # pw-record 录音与 WAV 封装
    ├── models.py               # 模型目录、三来源下载、缓存检测、网络探测
    ├── transcribe.py           # 本地 / 在线识别、中文标点规范化
    ├── inject.py               # 剪贴板 + 按键注入（uinput 优先，ydotool 备用）
    ├── uinput.py               # 原生 uinput 虚拟键盘
    ├── controller.py           # 状态机、线程调度、通知
    ├── ptt.py                  # 右 Alt evdev 监听
    ├── ipc.py                  # Unix 套接字控制服务
    ├── hotkey.py               # GNOME 自定义快捷键安装
    ├── diagnostics.py          # 环境自检
    ├── wizard.py               # 首次启动四步向导
    ├── gui.py                  # GTK4 / libadwaita 窗口
    └── notify.py               # notify-send 封装
```

| 模块 | 职责 |
|---|---|
| `audio.py` | 启动 `pw-record` 录裸 PCM；SIGINT 停止；校验时长；用 Python `wave` 封装 WAV |
| `models.py` | 6 个模型的体积/说明目录；HF/hf-mirror/魔搭仓库映射；统一下载（进度回调、取消、断点续传）；两种缓存布局检测；来源连通性探测 |
| `transcribe.py` | 本地 faster-whisper 与在线 OpenAI 兼容 API；按来源解析模型路径；中文标点规范化 |
| `inject.py` | 校验粘贴快捷键；`wl-copy` 写剪贴板；uinput/ydotool 注入按键；延迟恢复剪贴板 |
| `uinput.py` | 直接写 `/dev/uinput` 创建临时虚拟键盘，按下 Ctrl(+Shift)+V 后销毁 |
| `controller.py` | 录音/识别/注入状态机；后台线程；通知；取消录音 |
| `ptt.py` | 发现键盘事件设备；解析 24 字节 `input_event`；识别 `KEY_RIGHTALT`；短按取消 |
| `ipc.py` | 单实例套接字、GLib IO watch、CLI 请求/响应 |
| `hotkey.py` | 用 GSettings 注册 GNOME 自定义快捷键 |
| `diagnostics.py` | 检查 GTK、系统命令、权限、服务、快捷键、模型缓存（按来源） |
| `wizard.py` | 四步首次启动向导：环境检查/权限修复、来源选择/探测、模型下载、快捷键注册 |
| `gui.py` | 控制窗口、设置页、向导集成、结果展示、状态同步、PTT 监听生命周期 |

---

## 关键实现细节

### 为什么右 Alt 要直接读 evdev

GNOME 的自定义快捷键：

- 只有“按下”事件，没有“松开”事件
- 不能把单独的 Alt 注册为快捷键
- 无法实现“按住说话、松开结束”

因此 `ptt.py` 直接以只读方式打开 `/dev/input/by-id/*-event-kbd` 和 `/dev/input/by-path/*-event-kbd`：

- 不 `EVIOCGRAB`，不拦截按键，AltGr 输入特殊字符不受影响
- 只关心 `EV_KEY` 类型、`KEY_RIGHTALT`（键码 100）
- 忽略按键重复（value=2）
- 读取用 `select` + 0.5 秒超时，支持热插拔
- 后台线程每 2 秒重新扫描设备，设备消失后自动重连
- 按住不足 350ms（例如用 AltGr 打 `@`）会自动取消本次录音

### 为什么用 pw-record

Ubuntu 24.04 上 `parecord` 经 PulseAudio 兼容层实测可能录到 0 字节。`pw-record` 是 PipeWire 原生工具，稳定可靠，也不需要 Python 音频库或 PortAudio。

录制格式为 16kHz、单声道、s16 裸 PCM，正好是 Whisper 需要的输入格式，避免额外重采样。

### 为什么绕过 PyAV

faster-whisper 默认用 PyAV 解码音频，但系统 PyAV 版本过旧会报：

```text
TypeError: open() got an unexpected keyword argument 'metadata_errors'
```

因此 `transcribe.py` 自己用标准库 `wave` 读取 WAV，转成 numpy `float32` 数组后直接传给 `model.transcribe()`，完全不经过 PyAV。

### 为什么用“剪贴板 + 模拟粘贴”

中文无法用键码逐字输入，逐字 keysym 映射也不可靠。因此：

1. `wl-copy` 把识别文字写入剪贴板
2. 模拟按下 Ctrl+V（或 Ctrl+Shift+V）
3. 约 1.2 秒后用后台线程恢复原剪贴板

注意点：

- `wl-copy` 会 fork 一个剪贴板守护进程；必须把 stdout/stderr 设为 `DEVNULL`，否则父进程会因管道未关闭而卡住
- 恢复剪贴板的线程必须是非 daemon 线程，否则主进程退出时恢复不会发生

### 为什么内置 uinput 注入而不是依赖 ydotool

Ubuntu 24.04 仓库的 `ydotool 0.1.8` **只打包了客户端，没有 ydotoold 守护进程**，全新系统上 `ydotool key` 无法工作。因此 `uinput.py` 直接写 `/dev/uinput`：

1. `ioctl` 声明 EV_KEY 与所需键码（LEFTCTRL / LEFTSHIFT / V）
2. 写入 `uinput_user_dev` 结构并 `UI_DEV_CREATE` 创建虚拟键盘
3. 等待约 0.3 秒让 libinput/合成器注册设备（太早发按键会被丢弃）
4. 发送按下/抬起事件 + SYN_REPORT，随后 `UI_DEV_DESTROY` 销毁设备

`ydotool` 保留为备用路径（uinput 不可用时尝试）。deb 包自带 udev 规则把 `/dev/uinput` 设为 `root:input 0660`。

### 为什么文字注入依赖 input 组

- 读取右 Alt：需要读 `/dev/input/event*`（`root:input rw`）
- 模拟粘贴：需要写 `/dev/uinput`（udev 规则设为 `root:input rw`）

二者都要求用户属于 `input` 组。向导第 1 步可一键修复（pkexec 提权），修改组后必须重新登录。

### 模型来源与缓存

- HF 两个源（官方/镜像）共用 `huggingface_hub.snapshot_download`，通过 `HF_ENDPOINT` 切换；禁用 Xet 传输以兼容镜像
- 魔搭源用 `modelscope` SDK 的 `snapshot_download`，仓库是现成的 CTranslate2 格式（与 HF 同源），下载后直接加载，无需转换
- 缓存位置：HF 在 `~/.cache/huggingface/hub/models--<owner>--<name>/snapshots/<rev>/`；魔搭新版在 `~/.cache/modelscope/hub/models/<owner>--<name>/snapshots/master/`（兼容旧布局）
- 识别前统一解析成本地目录：已缓存直接用，未缓存按所选来源下载；两个源都支持断点续传
- 下载进度：HF 通过 tqdm 回调桥接，魔搭通过 SDK 的 `progress_callbacks` 桥接，统一供向导进度条与 CLI 使用

### 为什么录音期间窗口保持可见

早期版本在开始录音时立即隐藏窗口，用户看不到录音状态。现在改为：

- 按下右 Alt：窗口保留，显示「正在录音…」
- 松开右 Alt：状态切换到「正在识别」，此时才隐藏窗口，把焦点还给目标应用

转录在后台线程完成，通常需要 1–5 秒，因此隐藏窗口后焦点有足够时间回到原应用，粘贴不会落到控制窗口里。

### 为什么没有托盘图标

GNOME 46 默认没有系统托盘（StatusNotifierItem 宿主）。普通 GTK 应用无法把图标放到顶部状态栏。因此本项目采用：

- 控制窗口
- 桌面通知
- 应用菜单入口

不依赖任何 GNOME 扩展。

---

## 命令行

```bash
voice-type show              # 打开控制窗口（服务未运行时会自动启动）
voice-type gui               # 直接启动 GUI
voice-type gui --hidden      # 以隐藏窗口方式启动
voice-type toggle            # 开始/停止（需要服务已运行）
voice-type start             # 开始录音
voice-type stop              # 停止并识别
voice-type status            # 查看状态（JSON）
voice-type quit              # 退出后台服务
voice-type doctor            # 环境自检
voice-type install-hotkey     # 安装/更新全局快捷键
voice-type install-hotkey --binding '<Super><Alt>v'
voice-type uninstall-hotkey  # 移除全局快捷键
voice-type download-model    # 预下载当前模型（按配置来源）
voice-type download-model --model small
voice-type download-model --source modelscope   # huggingface / hf-mirror / modelscope
voice-type config            # 显示配置路径与当前配置
```

`toggle/start/stop/status/quit` 要求程序已经打开；只有 `show` 会自动启动服务。

---

## 识别准确率与性能

### 模型选择

| 模型 | 下载大小 | 中文准确率 | CPU 速度 | 建议 |
|---|---|---|---|---|
| `tiny` | ~75MB | 低 | 极快 | 仅测试 |
| `base` | ~145MB | 较低 | 很快 | 低配机器 |
| `small` | ~480MB | 一般 | 快 | 应急 |
| `medium` | ~1.5GB | 中等 | 中等 | 旧默认 |
| `large-v3-turbo` | ~1.6GB | 高 | 中等偏快 | **默认推荐** |
| `large-v3` | ~3GB | 最高 | 慢 | 追求准确率 |

本机（AMD Ryzen AI 9 HX 370，12 核 24 线程，int8）实测：5.8 秒语音用 `large-v3-turbo` 识别约 2.7 秒。

### 提高准确率的方法

1. 使用 `large-v3-turbo` 或 `large-v3`
2. 在「识别提示词」中填写常用专业术语、人名、项目名
3. 保持 VAD 开启
4. 尽量靠近麦克风，减少环境噪声
5. 如果有耳麦或 USB 麦克风，在「麦克风设备」中指定设备
6. 长句一次说完，避免频繁短句
7. 仍不满意时改用在线 API（如 SiliconFlow、Groq）

### VAD 的作用

Whisper 在静音或纯噪声上可能“幻听”，甚至把提示词原样输出。开启 VAD 后，静音片段会被剔除，显著减少这类问题。VAD 模型随 faster-whisper 一起提供，不需要联网下载。

---

## 故障排查

先运行：

```bash
voice-type doctor
```

| 现象 | 原因与解决 |
|---|---|
| 按住右 Alt 没反应 | 用户不在 `input` 组，或键盘设备不可读；`sudo usermod -aG input $USER` 后重新登录 |
| 有通知但文字没出现 | 不在 `input` 组，或 `/dev/uinput` 不可写；同样重新登录 |
| AltGr 输入特殊字符时误触录音 | 右 Alt 与 AltGr 是同一个键；短按会自动取消，或在设置里关闭「右 Alt 按住说话」 |
| `Super+Alt+V` 没反应 | 程序未打开；先打开「语音输入」。已打开则检查 GNOME 设置 → 键盘 → 自定义快捷键 |
| 终端里粘贴没反应 | 设置里把「粘贴快捷键」改为 `Ctrl+Shift+V` |
| 识别不准 | 换 `large-v3-turbo`/`large-v3`；填写识别提示词；靠近麦克风；检查麦克风设备 |
| 静音时冒出奇怪文字 | 确认 VAD 已开启；仍出现可换更大的模型 |
| 在线 API 报 404 | Base URL 填错；确认服务端路径是 `/v1/audio/transcriptions` |
| 在线 API 报 401 | API Key 错误或未填 |
| 模型下载慢/失败 | 换个来源：设置里改「模型下载来源」（国内推荐魔搭），或 `voice-type download-model --source modelscope`；下载支持断点续传，重试会接着传 |
| 远程桌面（RDP）里右 Alt/注入无效 | RDP 会话的合成器不读 evdev 设备，属环境限制；请在本地登录的会话中使用 |
| 识别很慢 | 换 `small`/`base`；`large-v3` 比 `large-v3-turbo` 慢很多 |
| 窗口关掉后无法使用 | 这是设计行为：关闭即退出；重新从应用菜单打开 |
| 右上角状态栏看不到图标 | GNOME 46 没有系统托盘，运行中的应用只在任务栏和 Alt+Tab 中出现 |
| 想查看日志 | `~/.local/state/voice-type/gui.log` |

---

## 隐私与数据

- **本地模式**：音频不离开本机。录音临时文件位于 `/tmp/voice-type-XXXX.wav`，识别完成后立即删除
- **在线模式**：音频仅上传到你填写的 API 服务
- 程序不包含遥测、统计或自动更新
- 识别结果只保存在内存中用于窗口显示，不写入磁盘
- API Key 以明文保存在 `~/.config/voice-type/config.json`，请自行注意文件权限

---

## 已知限制

- GNOME 46 没有系统托盘，无法常驻右上角
- 右 Alt 与 AltGr 是同一个物理键，可能误触（短按自动取消）
- 程序不会开机自启动，关闭窗口即退出
- 文字注入依赖剪贴板；极少数禁用粘贴的应用无法输入
- 录音和粘贴之间没有专门的焦点锁定；转录期间不要切换窗口
- 没有基于静音的自动停止（必须松开右 Alt 或再按快捷键）
- 本地模型在 CPU 上运行，速度取决于 CPU；`large-v3` 明显较慢
- **远程桌面（gnome-remote-desktop/RDP）会话中不可用**：该会话的合成器不读取 evdev 设备，右 Alt 监听与按键注入都无法工作；需本地登录的会话
- deb 包目前仅 amd64；仅在 Ubuntu 24.04 + GNOME Wayland 上完整验证过

---

## 卸载

**deb 安装：**

```bash
sudo apt remove voice-type        # 卸载程序（保留用户配置与模型缓存）
```

**源码安装：**

```bash
bash uninstall.sh
```

会移除启动脚本、应用菜单入口与图标、全局快捷键。

两种方式都保留模型缓存和配置文件。彻底清理：

```bash
rm -rf ~/.config/voice-type
rm -rf ~/.cache/huggingface/hub/models--*faster-whisper*
rm -rf ~/.cache/modelscope/hub/models/*faster-whisper*
```

---

## 开发与测试

```bash
# 安装（开发模式，含魔搭可选依赖）
uv venv --system-site-packages .venv
uv pip install --python .venv/bin/python -e ".[modelscope]"

# 运行测试
.venv/bin/python -m unittest discover -s tests -v

# 直接运行 GUI
.venv/bin/python -m voice_type gui

# 语法检查
.venv/bin/python -m py_compile src/voice_type/*.py
```

### 构建 deb 包

```bash
bash build-deb.sh                                    # 默认 PyPI
bash build-deb.sh --pip-index https://pypi.tuna.tsinghua.edu.cn/simple   # 国内镜像
# 产物：build/deb/voice-type_<版本>_amd64.deb
```

构建流程：创建可迁移 venv（uv `--relocatable`，复用系统 PyGObject）→ 安装 faster-whisper/httpx/modelscope 与本项目 → 重写 shebang 到 `/opt/voice-type/venv` → 组装 packaging/ 控制文件 → `dpkg-deb --build`。构建机需要 Python 3.12 与 uv（或 python3.12-venv）。

测试覆盖：

- 在线 API URL 规范化
- 粘贴快捷键校验
- 配置读写、默认值、坏文件回退
- WAV 时长解析
- 中文标点规范化
- 快捷键显示名
- Controller 完整流程与注入失败处理
- 右 Alt 按键解析、短按取消、忙碌忽略、FIFO 事件读取
- 键盘设备发现
- 套接字所有权（重复实例不删主实例套接字）
- 模型来源映射、端点切换、HF/魔搭双缓存布局检测、非法模型/来源拒绝
- uinput 快捷键映射、事件结构、非法快捷键拒绝

当前共 31 个单元测试。

---

## 许可证

本项目使用 MIT License，详见 [LICENSE](LICENSE)。
