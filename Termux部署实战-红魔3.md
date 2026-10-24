> 📄 本文是另一台真机（红魔 3）上的**独立部署实战记录**，由该机器的会话记录整理而来。
> 它走的是**不依赖 DSH、不需要 root** 的 Termux 路线，与本仓库主线的容器方案并行。
> 文中的群名、昵称、IP 均已脱敏；「未验证」标记保留原样，别当成结论。

---

# 红魔3（NX629J）微信机器人 · 从零部署「问题与解法」总表

生成时间：2026-10-24 20:20
事实来源：`redmagic/phone/交接记录-微信机器人.md`（813 行）、`redmagic/交接-给新对话.md`（363 行）、`redmagic/phone/loading/apply*.sh`、桌面三个 `.cmd`、`tx-boot.sh`，以及 10-24 全天会话记录。
本文只写**实测过**的事实。未验证项一律标注「未验证」。

**设备**：nubia Red Magic 3 / NX629J / SM8150 / Android 11 / `NX629J_CNCommon_V5.17`，无 root，BL 锁定
**机器人**：微信机器人 v2.2.2（第三方 wxauto 系）/ Termux 内跑 Python 3.14 / 虚拟屏 displayId=1 里跑微信 8.0.79
**目标**：一个独立微信号在群「示例群名」（备注 AAA，112 人）里按关键词与人设自动发言

---

## 一、路线层：三条被否的路（省掉最贵的弯路）

| 路线 | 卡在哪（实证） | 结论 |
|---|---|---|
| 刷 GSI / 找 root 系统 | `ro.boot.flash.locked=1`、`verifiedbootstate=green`；解 BL 必须清数据；**这台机电源键不可靠，fastboot 出事要靠拆壳** | 放弃。刷机收益（root）换不回「一按就砖」的风险 |
| 换 WebView 内核（为让 DSH App 不白屏） | 系统 `config_webview_packages` 只认 `com.android.webview` 83.0.4103.120；AOSP 包被签名拦（`INSTALL_FAILED_UPDATE_INCOMPATIBLE`）；Google 133 装得上、`pm enable` 成功，但 `dumpsys webviewupdate` 候选列表里没有它，`cmd webviewupdate set-webview-implementation` 仍失败 | 无 root 改不了框架白名单，死路 |
| 用 DSH App 容器跑机器人 | 容器界面全靠 WebView 渲染，WebView 是 83；DSH 产物尾部 `(globalThis.__DSH_BOOT_READY__ ??= Promise.withResolvers())` 需 Chrome 85，主 bundle 还有 25 处逻辑赋值 + 2 处 `static {}` 需 Chrome 94 | 白屏不是配置问题，是**解析阶段就崩**。要救只有两条独立工程：按 `target=chrome83` 重建 DSH 前端，或换内核 |
| **最终路线：Termux** | 机器人只要「Python + adb + /sdcard 可读」，Termux 全给，不动 BL、不清数据、不换分区 | 成立 |

> DSH App 白屏与本项目无关，最后用**自建面板**替代它做观测与操作。

---

## 二、手机系统侧：nubia 与 Android 11 的硬限制

### 2.1 nubia ApplicationFreeze 把 Termux 冻成 D 状态（最阴，排第一）

- **现象**：面板网页一直转圈、`/health` 30 秒超时，`/title`、`/where` 同样，但 `ps` 里进程还在；STAT 全是 **`D`**，`kill` 无效。
- **根因**：nubia 自研冻结把 Termux 子进程塞进 `freezer:/bg`（`/proc/<pid>/cgroup` 可见）。D 态不可杀，不是死锁。
- **判定对照**：`am start -n com.termux/.app.TermuxActivity` 把 Termux 拉回前台，**同批进程 4 秒内全变 `S`**，`sleep 30` 继续跑。
- **已用解法（三层加固，实测压住）**：
  ```sh
  dumpsys deviceidle whitelist +com.termux
  am set-inactive com.termux false
  termux-wake-lock
  ```
  验证方式：Termux 切后台、桌面在前台等 100 秒，`wxbridge`/`adb-keep` 仍 `S`、`/health` 正常。
- **必须记住的两点**：
  - `termux-wake-lock` 拿到的锁**随会话结束释放**（实测 18:49:45 REL），不是一劳永逸。
  - **Android 11 没有 `am unfreeze`**（12+ 才有）。唯一可靠解冻手段是把 Termux 拉回前台。
- **未验证的根治路**：设置 → 电池 → 应用冻结，把 Termux 加白名单。
- **工程化后果**：所有桌面脚本第 2 步都必须「拉 Termux 回前台」，删掉这一步整套会失灵。

### 2.2 会话列表页按 BACK 会打开「最近使用的小程序」

- **铁证三处**：19:11 只发一次 `input -d 1 keyevent 4` → 出现「最近」页；19:47 存完备注连按两次 BACK → 又出现；老代码 `back_to_list()` 偶数轮发 BACK 收浮层 → **越收越开**，19:46 的死循环就是它。
- **正确做法**：点底部最左「微信」tab（1080 档实测 `tap(136, 2180)` 一次生效）。`back_to_list()` 两处已改。
- **连带坑**：那个面板是半屏浮层，底部 tab 栏照样露着（`tab_slots=[3131,0,0,0]`），所以「底部绿 tab 在」**不足以**证明列表可用，还得看 OCR 里有没有「搜索小程序」。判据函数 `_miniprogram_panel_open()`。

### 2.3 红魔「游戏空间」就是桌面

- `cn.nubia.gamelauncher` 声明的 intent 是 `MAIN` + `category.HOME`，**直接 `pm disable-user` 会没桌面**。
- 正确顺序：
  ```sh
  adb shell "cmd package set-home-activity cn.nubia.launcher/com.android.launcher3.Launcher"
  adb shell "pm disable-user --user 0 cn.nubia.gamelauncher"
  ```
- 恢复：`pm enable cn.nubia.gamelauncher`，**然后再设一次 home**，否则桌面仍是普通 launcher。

### 2.4 屏幕熄了，所有输入进不去

`mCurrentFocus=null` 时 `input text` 全部无效。前置四条：

```sh
adb shell svc power stayon true
adb shell settings put system screen_off_timeout 3600000
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
```

### 2.5 「微信团队」那条会话删不掉

长按 → 删除 → 弹「将清空聊天记录，同时不显示此聊天」+「我知道了」，那个按钮对 `input tap` **完全免疫**（试过 3 个坐标、motionevent、左滑，全无效）。要删只能手指点。留着不影响「目标群置顶第一行」。

### 2.6 Android 11 的 dumpsys 字段名不同

- `topResumedActivity=` 是 Android 12+ 写法，Android 11 只有 `mResumedActivity:`。
- 原代码按新字段 grep，拿到空串 → `wechat_front()` 恒假 → 一路报「微信不在虚拟屏前台」。
- 修法：grep 同时带 `Display #|topResumedActivity|mResumedActivity`，两种都认。

---

## 三、Termux 运行时：装得起来不等于跑得起来

### 3.1 libc++ 代差（一上来就撞墙）

- **现象**：`import numpy` 报 `cannot locate symbol "_ZNSt6__ndk113__hash_memoryEPKvm"`，三个原生模块同时报同一符号。
- **根因**：Termux 0.118.3 自带 `libc++ 27c`，仓库包用新 NDK 编。不是包坏，是运行库代差。
- **解法**：升级 Termux 到 `0.119.0-beta.3` + `pkg upgrade -y`，`libc++ 30`，全部恢复。

### 3.2 装依赖的正确写法（换源 + 一次性装齐）

```sh
cat > $PREFIX/etc/apt/sources.list <<EOF
deb https://mirrors.tuna.tsinghua.edu.cn/termux/apt/termux-main stable main
EOF
pkg update -y && pkg upgrade -y
pkg install -y python python-onnxruntime python-numpy python-pillow python-scipy nodejs tesseract imagemagick
pip install --no-cache-dir requests pyyaml
```

版本基线：python 3.14 / numpy 2.5.4 / onnxruntime 1.31.0 / Pillow 13.0.0 / scipy 2.5.4 / nodejs 26.12.1 / adb 1.0.41 / tesseract 5.5.3（chi_sim+eng）/ ImageMagick 7.1.2-34。

### 3.3 `/tmp` 在 Android 上不存在

- **隐蔽点**：`shot()` 返回 False，日志只有「截图失败」；但直接 `w.shot("/sdcard/dsh/tmp/t2.jpg")` 是 True —— **故障在调用方传的临时路径**，不在截图函数。
- 排查手法：`import wxbridge` 后逐个调 `did()` / `_require_did()` / `_frame_direct()` / `shot()`，把故障面从「整条链」收敛到「一个参数」。
- 修法：全量 `/tmp/` → `$PREFIX/tmp/`。数量：`wxbridge.py` 56 处、`wxreader.py` 5 处、`app/` 下 22 个脚本的临时路径。

### 3.4 `/sdcard` 是 FUSE，noexec

`run-as ... sh /sdcard/xxx.sh` 报 Permission denied。脚本必须先 copy 进 `$PREFIX` 或 `$HOME` 再执行。

### 3.5 `run-as com.termux` 看不见别人的进程、也读不了 /sdcard

- SELinux `runas_app` 挡住 `/sdcard` 读，但 Termux 私有目录可读 → 读日志、改文件走它。
- `run-as` 下 `pgrep` 只看得见自己 uid 的进程：**VsDaemon 是 shell(uid 2000) 起的，会被误判「缺失」而重复拉起**。判据必须改成探端口 `http://127.0.0.1:18250/list`。
- `run-as` 起的桥接会报 `adb 直连 不可用`，必须用 Termux 原生环境起（`tx-start.sh`）。

### 3.6 Termux 的 adb 需要单独授权

`adb connect 127.0.0.1:5555` 报 `failed to authenticate`，手机会弹「允许 USB 调试吗？」。点「一律允许」后 Termux 自己的 adbkey 才进白名单。**这一步必须有人在手机边上点。**

### 3.7 拔掉 USB 之后仍然能连

adbd 的 TCP 监听独立于 USB：

```sh
getprop service.adb.tcp.port      # 5555
settings get global adb_enabled   # 1
```

手机内走 `127.0.0.1:5555`，外部走 WiFi `adb connect 192.168.1.100:5555`（实测可用）。别关手机 WiFi，否则外部失联（机器人自己不受影响）。

### 3.8 PowerShell 会把二进制搞坏

`adb exec-out screencap -p > file` 在 PowerShell 里得到的是损坏的 PNG。改 `screencap -p /sdcard/x.png` + `adb pull`。虚拟屏截图要 `adb shell screencap -p -d 1 /sdcard/x.png`（display 1 才是微信）。

### 3.9 用 `input text` 往 Termux 里下命令的规矩

- `%s` 代表空格：`input text 'sh%s/sdcard/dsh/x.sh'`，回车 `input keyevent 66`。
- **不要**先 tap 终端（弹软键盘挡输出）；**不要**用 BACK 收键盘（会把 Termux 退到桌面）。
- 输出重定向到 `/sdcard/` 下的日志，用 `adb shell cat` 读，别看屏幕。

---

## 四、桥接与微信自动化：代码级 bug 清单（共 20 处 + 第三轮 12 处）

### 4.1 发送链路（图片 + 文本）

| # | 症状 | 根因（实证） | 解法 | 状态 |
|---|---|---|---|---|
| A | `/send_image` 报「屏幕上有 3 个微信会话窗口（悬浮小窗）→ 这张不发」，但截图干净 | `wx_main_windows()` 用宽松正则扫 `dumpsys window windows` 整块，在两个 `KeyboardWindow` 块的 `mParentWindow=…LauncherUI` 上命中，计数 1→3 | 只认「窗口名行」（块前两行）的身份；改后同一 dumpsys 计数 = 1 | ✅ 17:04 修 |
| B | 点「发送」点到图片空白处，停在半屏分享页 | `_green_btn(img, y0=900, y1=1320)` 写死区间在本机**零绿像素**；真实绿块 bbox `x[561..884] y[2037..2166]`，质心 (722,2101) | ①`y1` 默认扫到底 ②改成找「横向绿色条带」，取最下面跨度≥150 的块 ③主流程反过来：先按颜色定位、OCR 只作后备 | ✅ 17:33 打通 |
| C | `/send` 恒报「输入法没能切到 DSH 输入」 | `ime_set()` 把「借用登记」写在验证之后；看护线程 `_ime_unstick()` 判据是「默认值==DshIme 且无登记」→ 在 `ime set` 与 `_ime_wait_bound()` 的窗口里把输入法抢回搜狗（逐秒采样 t1/t2=DshIme，t3=搜狗） | 登记提前到 `ime set` 之前 + 新增 `_IME_SWITCHING` 互斥（30 秒兜底防卡死） | ✅ |
| D | 判「没绑上」但其实绑上了 | `_ime_bound()` 用 `grep -m1 'mCurId='`；本机 `mCurId=` 至少出现两次（第 179、464 行，不同 section） | `_ime_bounds()` 收集全部 mCurId，任一命中即算绑上；首轮等待 2.5→4.0 秒 | ✅ |
| E | 聚焦判据永远失败 | `dumpsys input_method \| grep 'mServedView=' \| head -1` 第一行是输入法服务自己的 `mServedView=null` | 去掉 `head -1`，出现 `MMEditText` 即算聚焦 | ✅ |
| F | 四次点击全落在屏幕外 | 坐标表只有 636（half）与 1272（full，按 1272×2800 标定）两档，1080 宽被归到 1272 档 → 拿到 `tap 400 2715`（屏只有 2340） | 新增 **1080×2340 档**（输入框 `(400,2210)/(380,2210)/(430,2210)/(400,2235)`，实测都能让 `mServedView` 变 `MMEditText`） | ✅ |
| G | `stddev=None`，判输入框为空 | 手机没装 ImageMagick，`convert` 不存在 | `pkg install -y imagemagick`（IM 7 里 `convert` 是 `magick` 软链） | ✅ |
| H | `OCR 复核异常：TypeError('Cannot handle this data type: (1, 1), <U59')` | `_TessEngine` 只吃 PIL/ndarray，上层传的是**文件路径**（RapidOCR 吃路径） | `__call__` 加 `isinstance(img, str)` 分支 | ✅ |
| I | 裁剪窗读到 `''` | 1080 档裁剪窗是按 1272 档**折算的推算值**，不是实测；字号小 tesseract 不放大读不出 | 实测标定 `INPUT_CROP=(596,100,144,2200)`、`INPUT_OCR_CROP=(596,116,144,2192)`，OCR 前 `-resize 200%`（同窗口不放大命中 0，放大命中 3/3） | ✅ |
| J | 点了发送但输入框里还有字 | 兜底坐标 (975,2212) 点不中 | 手测 (975,2223)，`SEND_BTN_POS` 改 2223 | ✅ |
| K | `bot.sh` 卡在 `tar: /root: Cannot open` | `setup_ocr.sh` 是给「容器里有 /root」写的，Termux 没有；`bot.sh` 是 `bash setup_ocr.sh \|\| exit 1`，整链断在这 | 开头加 Termux 判定：有 tesseract 且无 /root 就放行 | ✅ |
| L | `/open` 失败 `RemoteDisconnected`，机器人永远起不来 | `_ABORT` 只有 send_text/send_image 会 `clear()`，`/open` 漏了；`bot.sh` 启动第一步就 `POST /abort` → `_abort_check` 抛 `_Aborted`（BaseException，无人接）→ 线程死、连接断 | `open_chat()` 入口 `_ABORT.clear()` | ✅ |

> **接口形状纠错**：`/send` 的 body **只有 `text` 与 `force`**，没有 `chat`；它发给**当前会话**，要发别的群得先 `/open`。这一条曾按 `{"text":…,"chat":…}` 写过，是错的。

### 4.2 切群链路（真跑的最后一关，也是今天耗时最多的一关）

时间线（日志实证）：18:45:33 列表里找不到「示例群」→ 18:46:13 改走搜索框 → 界面变成 `MMFTSSOSHomeWebViewUI`（标题='示例群' 但 `匹配=False`）→ 切群失败 53.4 秒，机器人拒绝启动。诡异点：那一刻微信**就在目标群聊天页**，`/where` 也 `in_target: true`。

| # | 问题 | 根因 | 解法 | 状态 |
|---|---|---|---|---|
| M | 搜索框这条路必跳「搜一搜」 | 红魔3 上微信搜索后跳的是 `MMFTSSOSHomeWebViewUI`，代码只认 `FTSMainUI` | 补丁认搜一搜页（`patch-fts`）；同时结论是**搜索框这条路是死路**，改走会话列表点击 | 部分（路线已放弃） |
| N | 明明在群里却判「不在」 | `_already_in_chat` 是单帧判据，OCR 一抖就 False，接着走「列表里找→找不到→搜索框」这条必然失败的路 | 改成多帧投票 `_title_vote`（`/send` 早就在用），有结论就用，全帧读不出才退回单帧 | 已打补丁，**验证未完成**（打完最后一次启动仍失败 56.4s，日志里没看到「已经在目标会话，直接放行」） |
| O | 群名 OCR 命中率低 | tesseract 读「示例群名」实测读成过「示例群名」「示例群名」「1 示例群名」「1 [示例群名」`''`（空） | **给群加备注 AAA**（微信原话「群聊的备注仅自己可见」，群里任何人看不到）。之后标题命中率近 100%，`where` 返回 `{"in_target":true,"chat_title":"AAA"}` | ✅ **最值钱的一条**：切群从 100 秒失败 → **1.2 秒成功** |
| P | 改成 AAA 后连报三次「会话列表里没找到「AAA」」 | `_chat_title_now` / `_find_chat_row` / `_first_row` 都要求文本含 `[\u4e00-\u9fff]`，纯字母被整门挡住 | 新增 `_text_ok()`：含中文，**或**≥2 个字母/数字 | ✅ |
| Q | 微信跑偏后「猜 6 轮返回箭头」耗时 40~56 秒还常失败 | 没有确定起点 | `hard_reset_wechat()`：`am force-stop` + `am start --display 1`，接在「列表点击失败」之后，一次 `/open` 最多重启一次。冷启动 8~12 秒，**必然停在会话列表**，起点确定 | ✅ |
| R | `/reclaim` 搬微信不可靠 | 实测 20:05：`POST /reclaim` 后微信变成「两屏都不在」的悬空状态，**桥接进程还死了** | 直接 `adb shell am start --display 1 -n com.tencent.mm/.ui.LauncherUI` | ✅ 改用直连 |
| S | `/takeover` 之后看到的还是桌面 | takeover 只把 task 移出虚拟屏，**不会把前台让出来** | 补一条不带 `--display` 的 `am start`（配合 `force-stop` 先清） | ✅ |
| T | 标题被未读角标污染 | 标题左侧未读角标「8」被一起读进标题，偶尔只剩「8」→ 机器人误判不在目标群，待命中 | 待办：`read_title` 先裁掉左侧 120px 角标区；或把 `match_title` 放宽成「包含目标名」 | ⏳ 未做 |

### 4.3 打补丁本身的坑（比 bug 更贵）

**`wxbridge_ctl.sh start` 有时不会杀掉旧进程**（pidfile 与实际进程对不上）。于是「补丁在文件里、跑的还是旧进程」，症状与没打补丁完全一样 —— 为此白试三轮，三次都是同一个 `RemoteDisconnected`。

解法：`tx-force-restart.sh`（`pkill -9` + 删 pidfile + 重启），打补丁后**必须确认 PID 变了**。

### 4.4 补丁与备份

全部补丁在手机 `/sdcard/dsh/`，各自带 `.bak-*` 备份，可回滚：

`patch-win.py`、`patch-green.py`、`patch-ime.py`、`patch-ime2.py`、`patch-ime3.py`、`patch-focus.py`、`patch-tier1080.py`、`patch-ocrpath.py`、`patch-crop1080.py`、`patch-ocr2x.py`、`patch-sendbtn.py`、`patch-setupocr.py`、`patch-abortclear.py`、`patch-shorttext.py`、`patch-fts.py`、`patch-alreadyvote.py`、`patch-adbdiag.py`、`patch-focus2.py`、`patch-tap.py`（后两个是调试日志，可删）。

原包备份：`/sdcard/dsh/微信机器人-v2.2.2.zip`（323 MB）。原包 `payload/wechat-bot/data/` 里有作者真实聊天记录与 `.deepseek_key`，手机上跑的是**干净模板**，`data-author-backup/` 保留但未启用。

---

## 五、交互通道：为什么最后一定要有面板

1. **Termux 终端里下命令会丢字/串行**：`input text` 打到 Termux，首字符常被吞（命令变 `h /sdcard/...`）；上一行没跑完时新输入会接到旧命令后面（`bash bot.sh stop bash /sdcard/...`）。缓解：加前导空格 + 每次先 `input keyevent 66`。根治：**走面板 HTTP 接口**。
2. **`/health` 里 `adb` 字段时真时假**，但手动 `adb -s 127.0.0.1:5555 shell echo ok` 一直正常。假的时候走「命令守护」，慢但能用。已埋诊断日志（`adb 探活失败：rc=… err=…`），**根因未定位**。
3. **面板把「看不见」变成「能看见、能点」**：手机浏览器 `http://127.0.0.1:8790`，PC 走 `adb forward tcp:18790 tcp:8790`。
   控制接口是 `/start` `/stop` `/dry` `/reclaim`（**不是** `/api/start`，`/api` 只吃 GET 状态）。

端口总账：面板 8790→18790、桥接 8765→18765、虚拟屏守护 18250、adb 5555。

---

## 六、身份口径与模型

| 项 | 旧 | 新（10-24 19:00 起） |
|---|---|---|
| 谁说话算主人 | `msg.is_self`（共用号，自己发=主人发） | **群里「主人昵称」发的**（`config.yaml → me.owner`） |
| 机器人身份 | 与主人同一个号 | **自己接管一个号**，主人的话是「别人说的」 |

代码：`wxauto/bot.py` 新增 `_is_owner()` / `_owner_name()`，改三处（`own` 列表、`pending` 列表剔除主人、消息分派判据）；人设 `personas/猫娘.md` 改三段。

> **必须记住**：`pending` 必须剔除主人。主人变成「别人」之后他的消息会同时进 `own` 和 `pending`，不剔就是**一句回两条**。

模型：`config.yaml → llm.api_key` 已填，`main_android.py --check-llm` 通过（`deepseek-chat`，生成 2.9 秒）。**key 是明文躺在 `data/config.yaml` 里**，别往公开地方带。

会话列表现状：`AAA`（置顶，唯一）+ `微信团队`（系统号，删不掉）。

---

## 七、状态总账（2026-10-24 20:10 实测）

| 能力 | 状态 | 证据 |
|---|---|---|
| 无线 adb | ✅ | `adb connect 192.168.1.100:5555` → device |
| 虚拟屏（displayId=1） | ✅ | `curl :18250/list` |
| 读群消息 | ✅ | `/title`、`/snapshot`、`--probe` 8 条消息解析正确 |
| 发图片 | ✅ | 17:33 群里有图，`/send_image` 回 `ok:true` |
| 发文本 | ✅ | 18:07 群里有「再测一句，看自检通不通」 |
| 面板 | ✅ | 启停/抢回/状态/实战台/打一顿/识图 |
| 开机自启三层 | ✅（一层未验证） | `~/tx-boot.sh` 幂等（第二次 1 秒）、`~/.bashrc` 钩子、`~/.termux/boot/00-wxbot.sh`（**需装 Termux:Boot 才在真开机生效，未验证**） |
| 干跑 | ✅ | `main_android 2330`，`[演练] 本该发这张表情包：抱着.png` |
| 切群 | ✅ | 备注 AAA 后 1.2 秒成功 |
| 真跑 | ⏳ | 方向已授权（「它想说就说」），尚未正式启动 |
| DSH App 界面 | ❌ | WebView 83，与本项目无关 |

进程（仅 Termux 在前台时才是 `S`）：

```
wxbridge.py   32329  桥接（displayId=1 / adb true / ocr true）
botctl.py      6283  面板
VsDaemon      13856  虚拟屏守护 :18250
adb-keep.sh   27163  adb 自愈
main_android   2330  干跑中
```

---

## 八、未解 / 未验证清单（下一步的靶子）

| # | 问题 | 可选解法 |
|---|---|---|
| 1 | `_already_in_chat` 多帧投票是否生效**未验证**（18:47:57 那次仍失败 56.4s） | 先看日志有没有 `/open … 已经在目标会话，直接放行`；没有就是补丁没跑，用 `tx-force-restart.sh` 硬重起再验 |
| 2 | 单字 OCR（群规则里「喵」「咪」是单字回复，tesseract 把「喵」读成「十」） | 已放宽成「≤2 字且读到 ≤3 字就放行」（未验证）；根治用像素比对（stddev）兜底，或换 RapidOCR（缺 opencv/shapely/pyclipper，装不上） |
| 3 | 标题 OCR 被未读角标污染 | 裁左侧 120px 再识别，或 `match_title` 改「包含即算」 |
| 4 | `adb` 直连抖动 | 已埋诊断日志，等复现；兜底是命令守护（慢但能用） |
| 5 | Termux:Boot 那一层未验证 | 装 Termux:Boot 后冷启动验一次 `tx-boot.log` |
| 6 | nubia「应用冻结」白名单能不能一劳永逸 | 设置 → 电池 → 应用冻结 把 Termux 加白，比每次 `am start` 省事（未验证） |
| 7 | 「微信团队」删不掉 | 只能手指点，不影响置顶第一行 |
| 8 | 面板/脚本还在电脑桌面 | 20:11 你的最新要求：**搞手机里**。做法：`/sdcard/dsh/` 放 `.sh` 版入口 + 手机浏览器书签 `http://127.0.0.1:8790`，桌面 `.cmd` 只留 PC 侧备用 |
| 9 | 真跑 | 面板 `/start` 或 `还给机器人.cmd` 选 1；急停 `bash bot.sh stop` 或目录里建 `STOP.txt` |

### 桌面脚本的两个硬要求

- **必须 CRLF 行尾**。LF 会被 cmd 当成一行，逐行报「'机' 不是内部命令」。
- **第 2 步必须是「拉 Termux 回前台」**，删了整套会因冻结失灵。

三个脚本：`打开机器人面板.cmd`（连机→解冻→转发→开浏览器）、`我要用微信.cmd`（停机器人→`/takeover`→补 `am start` 到物理屏）、`还给机器人.cmd`（解冻→转发→`am start --display 1`→桥接探活 3 次→切群 AAA→可选真跑/干跑）。

---

## 九、方法层：这次真正省时间的六个习惯

1. **先分清「发不出去」和「自检读不到」**。文本那条链早就通了，卡的是自检判据；把这两件事混在一起会去修根本没坏的通道。
2. **对照组先做**。判定 BACK 键行为、判定 Termux 冻结、判定 401/回退页，全靠「同一操作、看两次不同结果」。没有对照的观测不算证据。
3. **把坐标类断言换成像素实测**。绿按钮、裁剪窗、点击点，全部先用脚本量出来再写进代码，写死的推算值 100% 会在别的分辨率上失效。
4. **打补丁先确认进程换了**。文件变了 ≠ 进程变了，这是本次最贵的一课（白试三轮）。
5. **找「一个字的稳定锚点」，而不是硬认全名**。群备注 AAA 把 OCR 难题从「识别长中文串」降级成「识别三个字母」，一次性解决切群、`where`、标题匹配三处。
6. **能用系统命令复位就别猜 UI**。`force-stop` + 冷启动 8~12 秒，胜过「猜 6 轮返回箭头」40~56 秒且常常失败。不确定性要用确定性手段消灭，不是用更多重试。

---

## 十、速查命令

```sh
# 通道与进程
adb connect 192.168.1.100:5555 && adb devices
adb shell "ps -A -o PID,STAT,ARGS | grep -E '[w]xbridge|[a]db-keep|[V]sDaemon|[b]otctl'"

# 解冻（nubia 冻结唯一可靠手段）
adb shell am start -n com.termux/.app.TermuxActivity

# 健康与状态
adb forward tcp:18765 tcp:8765 && adb forward tcp:18790 tcp:8790
curl -s http://127.0.0.1:18765/health     # {"ok":true,"displayId":1,"adb":true,"ocr":true}
curl -s http://127.0.0.1:18765/where
curl -s http://127.0.0.1:18790/api

# 搬微信
adb shell am start --display 1 -n com.tencent.mm/.ui.LauncherUI   # 回虚拟屏
adb shell am force-stop com.tencent.mm
adb shell am start -n com.tencent.mm/.ui.LauncherUI               # 物理屏，你自己用

# 硬重启桥接（打完补丁必用）
sh /sdcard/dsh/tx-force-restart.sh

# 全量拉起（幂等）
~/.termux/boot/00-wxbot.sh    # 或 bash ~/tx-boot.sh
```

**急停**：面板点停止 / `bash bot.sh stop` / 在 `~/wechat-bot/app` 建 `STOP.txt`。
