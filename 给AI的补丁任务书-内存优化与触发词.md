# 给 AI 的补丁任务书 —— 微信机器人：桥接内存优化 + 「看看腹肌」触发词扩容

> **这份文件是给「AI 助手」看的，不是给人看的。**
> 拿到的 AI：请把本文件从头读到尾，然后按 §2、§3 动手改 §1 里那台机器上的文件。
> 全程 **6 处编辑**，约 5 分钟。**路径不确定就先用 §1 的命令确认，不要猜。**

---

## 📋 人：把这段连同本文件一起发给对方

> 微信机器人有两处更新，我让 AI 写好了补丁任务书（就在这个 md 文件里）。
> **把文件发给我手机上的 AI（DSH）**，跟它说一句：
> 「按这个文件里的补丁任务书改一下，做完告诉我改了什么、怎么验证的」。
> 它会自己备份、改文件、重启桥接、跑验证。
> 注意：改的时候微信可能会有一两分钟读不到画面，那是重启桥接的正常现象，会自己恢复。

---

## 0. 这两处补丁解决什么

### 补丁 A：桥接进程 `wxbridge.py` 内存占用虚高（**推荐装**）

`wxbridge.py` 进程里跑 OCR（RapidOCR / onnxruntime）。每读一轮屏，它会临时分配并释放几百 MB
内存。问题在于 **glibc 的 malloc 默认不把 free 掉的内存还给内核**，而是留着"下次用"，
于是一路只涨不跌：

| 指标 | 打补丁前（实测 20 次采样） |
|---|---|
| `VmRSS` 基线 | 428 MB |
| `VmRSS` 峰值 | **958 MB** |
| `VmData` 基线 | 1188 MB |
| `VmData` 峰值 | **1770 MB** |
| `VmSwap` | **516 MB**（被挤进 swap） |
| `VmHWM`（历史最高水位） | **1230 MB** |

后果：低配手机内存吃紧、机器人被系统杀、整个系统变卡。

修法两步：① 在 `wxbridge.py` 里加一个每 20 秒调用 `malloc_trim(0)` 的后台线程，
把空闲堆**主动还给内核**；② 在启动脚本里限制 glibc 的 arena 数量并降低归还阈值。

**打完后的实测效果**（同一台机器、同样 20 次采样）：

| 指标 | 改前 | 改后 |
|---|---|---|
| `VmRSS` 基线 | 428 MB | **155 MB** |
| `VmRSS` 峰值 | 958 MB | 729 MB |
| `VmData` 基线 | 1188 MB | **365 MB** |
| `VmData` 峰值 | 1770 MB | 960 MB |
| `VmSwap` | 516 MB | **0** |
| `VmHWM` | 1230 MB | 833 MB |

基线（也就是平时闲着的占用）直接降到原来的 **1/3 以下**，swap 归零。
峰值仍会在每轮 OCR 时短暂出现，但 **约 5 秒内自己回到基线**。

**风险**：极低。`malloc_trim` 只对 glibc 有效，取不到 libc 就打印一行然后**安静跳过**，
绝不影响桥接启动；`MALLOC_ARENA_MAX=2` 对这种 2 线程 OCR 场景没有性能影响。

### 补丁 B：「看看腹肌」发图经常不触发（**看你要不要**）

规则里这条触发词原来只有字面的「看看腹肌」四个字。群里实际说话是「我要腹肌」「发张腹肌」
「看下腹肌」——都不含这四个字，于是**规则根本没命中**，机器人不回也不发图。

修法：把 `keywords` 从 1 个扩到 11 个常用变体。**不包含光秃秃的「腹肌」二字** ——
那会让「一米八腹肌男去哪里领」这种闲聊也误触发，是故意排除的。

> ⚠️ 如果你的机器人**没有**配「看看腹肌」这个相册/规则，补丁 B 直接跳过，只做补丁 A。

---

## 1. 开工前：前置检查（30 秒，先做，别跳）

```sh
cd /sdcard/dsh/wechat-bot/app || { echo "❌ 找不到 /sdcard/dsh/wechat-bot/app，停手，问人"; }

echo "--- 版本 ---"
cat /sdcard/dsh/wechat-bot/VERSION 2>/dev/null || echo "(没有 VERSION 文件)"

echo "--- 锚点检查（下面 4 个数字都应为 1，或按注释判断）---"
grep -c 'threading.Thread(target=reclaim_watch, daemon=True).start()' wxbridge.py
grep -c '^  PYTHONPATH="$LIB" OMP_NUM_THREADS=2 \\' wxbridge_ctl.sh
grep -c 'WX_SEARCH_STUCK_SECS=' wxbridge_ctl.sh
grep -c 'name: "看看腹肌"' /sdcard/dsh/wechat-bot/data/rules.yaml

echo "--- 是否已经打过补丁（应为 0）---"
grep -c '_mem_trim_watch' wxbridge.py
grep -c 'MALLOC_ARENA_MAX' wxbridge_ctl.sh
```

**判断：**

- `_mem_trim_watch` 或 `MALLOC_ARENA_MAX` 的计数 **≥1** → 已经打过了，**跳过补丁 A**。
- 前三个锚点里**任何一个为 0** → 你这台机器的版本跟本任务书假设的不一样，
  **停手**，把上面的输出贴给用户，说「锚点对不上，需要人确认版本」，**不要凭感觉乱改**。
- 最后一项「看看腹肌」为 0 → 跳过补丁 B。

### 先备份（必须）

```sh
cd /sdcard/dsh/wechat-bot/app
TS=$(date +%Y%m%d-%H%M%S)
mkdir -p 历史备份
cp wxbridge.py      "历史备份/wxbridge.py.bak-$TS-补丁前"
cp wxbridge_ctl.sh  "历史备份/wxbridge_ctl.sh.bak-$TS-补丁前"
cp /sdcard/dsh/wechat-bot/data/rules.yaml "历史备份/rules.yaml.bak-$TS-补丁前"
ls -la 历史备份/ | tail -4
echo "✅ 记下这个时间戳备用：$TS"
```

---

## 2. 补丁 A：桥接内存优化（4 处编辑）

文件都在 `/sdcard/dsh/wechat-bot/app/`。
**用编辑工具做「精确字符串替换」，不要按行号改**（行号会因版本不同而漂移）。
下面每处都给了「找到这段」→「换成这段」，逐字照抄。

### A1）`wxbridge.py` —— 新增 `_mem_trim_watch()` 函数

**找到**（全文件只有一处 `def main():`）：

```python
def main():
    did = display_id()
```

**换成**（在 `def main():` 前面插入整个新函数）：

```python
def _mem_trim_watch(interval: float = 20.0) -> None:
    """★ 2026-10-16：定期把 glibc 释放掉的空闲堆**还给内核**（RSS 高水位治理）。

    背景（实测）：桥接进程内跑 RapidOCR，**每轮读屏（约 15s）会临时分配并释放几百 MB**。
    而 glibc malloc 默认**不把 free 的内存交还系统**（留着下次用），于是
    ``VmData`` 从 1.19GB 一路爬到 1.77GB、``VmRSS`` 在 430MB↔960MB 之间跳、
    峰值（``VmHWM``）到过 **1.23GB** —— 全是"账面占用"，却没真正用着。
    每 20 秒调一次 ``malloc_trim(0)``，空闲页立刻交还内核，RSS 尖峰就压下去了。

    ⚠️ 只依赖 Linux/glibc；拿不到 libc（或非 glibc）就**安静退出**，绝不能因此起不来。
    """
    try:
        import ctypes
        libc = ctypes.CDLL("libc.so.6")
        _trim = libc.malloc_trim
        _trim.argtypes = [ctypes.c_size_t]
        _trim.restype = ctypes.c_int
    except Exception as exc:
        print("[wxbridge] malloc_trim 不可用（%r）→ 跳过内存归还" % (exc,), flush=True)
        return
    logged = False
    while True:
        time.sleep(interval)
        try:
            freed = _trim(0)
        except Exception:
            continue
        if freed and not logged:          # 只报第一次，免得每 20s 刷一行日志
            logged = True
            print("[wxbridge] malloc_trim 生效：空闲堆已交还内核（后续静默）", flush=True)


def main():
    did = display_id()
```

> 说明：`time` 模块在文件顶部已经 import 过了（`reclaim_watch` 在用），不用再加。
> 插入位置必须在 `main()` **之前**（模块级函数），不要塞进别的函数体里。

### A2）`wxbridge.py` —— 在 `main()` 里启动这个线程

**找到**：

```python
    threading.Thread(target=reclaim_watch, daemon=True).start()
    print("[wxbridge] 自动归还已开启：
```

**换成**（只多两行，插在中间）：

```python
    threading.Thread(target=reclaim_watch, daemon=True).start()
    # ★ 2026-10-16 内存优化：每 20 秒把空闲堆还给内核（见 _mem_trim_watch）
    threading.Thread(target=_mem_trim_watch, daemon=True).start()
    print("[wxbridge] 自动归还已开启：
```

> 上面「换成」里最后那行 `print(...` 是**半截**，只用来定位，**不要照抄整行** ——
> 你只应该在 `reclaim_watch` 那行后面**插入那两行**，原有的 `print` 整行保持原样不动。

### A3）`wxbridge_ctl.sh` —— 加一段注释（可选，但强烈建议）

**⚠️ 先读这条坑，它已经害过人一次：**

> `wxbridge_ctl.sh` 里启动桥接是一整条 **用 `\` 续行的长命令**。
> **注释绝对不能插进这条续行链的中间** —— shell 会在注释那一行截断命令，
> 后面所有行都成了新命令，`PYTHONPATH` 直接丢掉，结果 OCR 起不来
> （现象：`/health` 里 `"ocr": false`，报 `No module named 'rapidocr_onnxruntime'`，
> 机器人变成哑巴）。
> **注释只能放在这条链的「头一行之前」。**

**找到**（全文件只有一处）：

```sh
  PYTHONPATH="$LIB" OMP_NUM_THREADS=2 \
```

**换成**（注释加在这一行**上面**）：

```sh
  # ★ 2026-10-16 内存优化：限制 glibc arena 数、压低空闲堆归还阈值
  #   （配合 wxbridge.py 的 _mem_trim_watch → 治 RSS 高水位）
  #   ⚠️ 这几行注释必须在这条续行链**之外** —— 插进链中间会让 shell 在注释处断链、
  #      PYTHONPATH 丢掉 → OCR 起不来（踩过一次）
  PYTHONPATH="$LIB" OMP_NUM_THREADS=2 \
```

### A4）`wxbridge_ctl.sh` —— 真正加那两个环境变量

**找到**：

```sh
    WX_SEARCH_STUCK_SECS="${WX_SEARCH_STUCK_SECS:-45}" \
    nohup python3 -u wxbridge.py
```

**换成**（在两者之间插两行，注意**每行结尾都要有 `\`**，这是续行符）：

```sh
    WX_SEARCH_STUCK_SECS="${WX_SEARCH_STUCK_SECS:-45}" \
    MALLOC_ARENA_MAX="${MALLOC_ARENA_MAX:-2}" \
    MALLOC_TRIM_THRESHOLD_="${MALLOC_TRIM_THRESHOLD_:-131072}" \
    nohup python3 -u wxbridge.py
```

> 同样：`nohup python3 -u wxbridge.py` 那行是**半截**，只用于定位，别照抄整行。
> 特别留意 `MALLOC_TRIM_THRESHOLD_` 结尾是**一个下划线**，不是两个。

---

## 3. 补丁 B：「看看腹肌」触发词扩容（2 处编辑）

### B1）`/sdcard/dsh/wechat-bot/data/rules.yaml`（正在生效的那份）

先看一眼现状：

```sh
grep -n -A3 'name: "看看腹肌"' /sdcard/dsh/wechat-bot/data/rules.yaml | head -8
```

**找到**：

```yaml
    keywords: ["看看腹肌"]
```

**换成**（缩进别改，第二行对齐到 `[` 之后）：

```yaml
    keywords: ["看看腹肌", "看腹肌", "看个腹肌", "看下腹肌", "看你的腹肌",
               "我要腹肌", "发腹肌", "发张腹肌", "来张腹肌", "腹肌来一张", "腹肌图"]
```

> **只改 `keywords:` 这一行**，`mode: contains` / `action: meitu` /
> `meitu_source: "看看腹肌"` / `replies:` 全部原样别动。
> 如果这行已经不是 `["看看腹肌"]` 了（你自己加过变体）→ **跳过 B1**。

### B2）`/sdcard/dsh/wechat-bot/app/defaults/rules.yaml`（模板，为了以后重装也对）

同样的替换，把 `keywords: ["看看腹肌"]` 换成上面那两行。
这份是**全新安装时用的模板**，改它不影响现在运行，但能让以后重装/换机也带上。

> ⚠️ 别忘了：`app/` 目录在**升级安装时会被包内文件覆盖**，
> 所以 B2 这份改动只对「之后再从本包全新安装」有意义。

**改完不用重启** —— 机器人会自动检测 `rules.yaml` 变化并热重载，
日志里会出现「检测到 rules.yaml 改动，已重载 N 条关键词规则」。

---

## 4. 改完先自检，再重启

```sh
cd /sdcard/dsh/wechat-bot/app

echo "--- 补丁是否都在（前 2 个应 ≥1，后 2 个应为 1）---"
grep -c '_mem_trim_watch' wxbridge.py
grep -c 'MALLOC_ARENA_MAX' wxbridge_ctl.sh
grep -c 'MALLOC_TRIM_THRESHOLD_' wxbridge_ctl.sh
grep -c 'PYTHONPATH="\$LIB"' wxbridge_ctl.sh

echo "--- 语法检查（两个都必须出 OK）---"
python3 -c "import ast,io;ast.parse(io.open('wxbridge.py',encoding='utf-8').read());print('OK python 语法')"
bash -n wxbridge_ctl.sh && echo "OK shell 语法"

echo "--- 续行链是否完整（应看到最后一行结尾是 \\ 且紧跟 nohup）---"
grep -n -B1 'nohup python3 -u wxbridge.py' wxbridge_ctl.sh
```

**只要 shell 语法检查没过 → 停手，别重启**，把输出贴给用户。
`bash -n` 是纯语法检查，不会真的启动任何东西，放心跑。

---

## 5. 重启桥接并验证（关键一步）

```sh
cd /sdcard/dsh/wechat-bot/app
bash wxbridge_ctl.sh restart
```

> **正常现象，别慌**：重启桥接会触发「自动归还」逻辑，暂时收回虚拟屏；
> 机器人随后会自己抢回来，这中间大约**有 1 分钟读不到画面**。这不是故障。

等大约 15 秒，然后：

```sh
echo "=== 1) 桥接健康（必须 ok:true 且 ocr:true）==="
curl -s http://127.0.0.1:8765/health; echo

echo "=== 2) 环境变量是否真的进了进程（应看到 MALLOC_ARENA_MAX=2 和 MALLOC_TRIM_THRESHOLD_=131072）==="
PID=$(cat /tmp/wxbridge.pid)
tr '\0' '\n' < /proc/$PID/environ | grep -E '^MALLOC_|^PYTHONPATH='

echo "=== 3) 内存归还线程是否生效（日志里应有一行「malloc_trim 生效」）==="
grep -m1 'malloc_trim' /sdcard/dsh/wechat-bot/logs/wxbridge.log

echo "=== 4) 当前内存 ==="
grep -E 'VmRSS|VmData|VmSwap|VmHWM' /proc/$PID/status
```

✅ **全部通过的判据**：

| 检查 | 期望 |
|---|---|
| `/health` | `"ok": true` **且 `"ocr": true`** |
| environ | 有 `MALLOC_ARENA_MAX=2`、`MALLOC_TRIM_THRESHOLD_=131072`、`PYTHONPATH=...` |
| 日志 | 出现 `malloc_trim 生效：空闲堆已交还内核` |
| 内存 | `VmRSS` 相比改前明显低（百来 MB 量级），`VmSwap` 应接近 0 |

❌ **如果 `/health` 里 `"ocr": false`** → 99% 是 A3 的注释插进了续行链中间，
把 `PYTHONPATH` 弄丢了。按 §6 回滚，重做 A3/A4，确保注释在链**外面**。

再顺手确认机器人本体也活着：

```sh
bash bot.sh status
```

### 顺便验证补丁 B（可选）

```sh
cd /sdcard/dsh/wechat-bot/app
python3 rule_tool.py --test "我要腹肌"
```

期望输出显示**命中**「看看腹肌」这条规则。
（改前测「我要腹肌」是「命中：无」，这正是当初不触发的原因。）
**别拿真群试** —— 要发实测消息就用自己的「文件传输助手」。

---

## 6. 回滚（出任何问题就整份还原）

```sh
cd /sdcard/dsh/wechat-bot/app
# 把 $TS 换成 §1 备份时记下的时间戳
ls 历史备份/ | grep '补丁前'
cp "历史备份/wxbridge.py.bak-$TS-补丁前"     wxbridge.py
cp "历史备份/wxbridge_ctl.sh.bak-$TS-补丁前" wxbridge_ctl.sh
cp "历史备份/rules.yaml.bak-$TS-补丁前"      /sdcard/dsh/wechat-bot/data/rules.yaml
bash wxbridge_ctl.sh restart
curl -s http://127.0.0.1:8765/health; echo
```

回滚是**完全可逆**的：本补丁没有改动任何数据文件格式，只加了一个后台线程和两个环境变量。

---

## 7. 常见问题

**Q：`malloc_trim 不可用` 怎么办？**
A：说明这台机器的 libc 不是 glibc（比如 musl）。桥接会**照常启动**，只是补丁 A 的内存
归还不起作用；`MALLOC_ARENA_MAX` / `MALLOC_TRIM_THRESHOLD_` 也会被忽略。
不影响功能，不用管。

**Q：日志里 `malloc_trim 生效` 只出现一次，是不是挂了？**
A：设计如此。只在**第一次**真的归还了内存时打印一行，之后静默，免得每 20 秒刷屏。

**Q：改了 `app/` 里的文件，下次升级安装会不会丢？**
A：**会。** 升级安装会按包内内容覆盖整个 `app/` 目录（`data/` 不动）。
所以这个补丁要**等官方包内置**才算永久；在此之前，每次升级完请重跑本任务书。
（补丁 B 改在 `data/rules.yaml` 的那份**不会**被覆盖。）

**Q：内存峰值还是偶尔冲到 700 多 MB，正常吗？**
A：正常。每轮 OCR 确实会临时用掉几百 MB，区别是**改完之后它会掉回来**
（约 5 秒内），而改之前是**只涨不跌**、一路把机器拖进 swap。

**Q：能不能把 `interval` 改小一点，比如 5 秒？**
A：不建议。20 秒已经足够压住峰值，改小只会白白增加 `malloc_trim` 的调用开销。
真想调，改 `_mem_trim_watch(interval=20.0)` 那个默认值就行。

---

## 8. 干完之后请向人汇报这三件事

1. **改了什么**：哪几个文件、各加了什么（照 §2/§3 说）；
2. **怎么验证的**：把 §5 的 `/health` 输出和 `VmRSS/VmSwap` 数字原样贴出来；
3. **怎么回滚**：告诉人备份放在 `历史备份/` 下、时间戳是多少。

**不要只回一句「改好了」** —— 人看不到你做了什么，只信这三个证据。
