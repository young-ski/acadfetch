# acadfetch on Python 3.15 — Windows run

Protocol: <https://github.com/young-ski/acadfetch/issues/4> · tool version: `acadfetch 0.1.0`

> ## 结论先说
>
> **装得上（sdist 与 wheel 都 exit 0）、套件 192 passed、续传/校验/退出码都对。但有四个真问题：**
>
> 1. `-X dev` 下 23 条 `ResourceWarning`（已逐条定位到 `tests/test_client.py:121/149/177`，纯测试侧）
> 2. 已发布的 PyPI 0.1.0 在 `verify` 不存在的文件时吐完整 traceback（HEAD 已修，但没发版）
> 3. `os.replace` 覆盖清单时只要有并发读者就 `WinError 5`，CLI 未兜底 → 下载成功后崩、exit 1、留孤儿 `.tmp`
> 4. 默认并发（`-j 3`）下 Ctrl+C 停不住传输：整份下完才退 130，且不打 `.part`
>
> 另外，**已发布的 0.1.0 比 HEAD 落后一个版本周期**：同一套件打 0.1.0 是 `18 failed / 174 passed`（3.14 上逐条相同），且一轮要 14–15 分钟。
>
> **第 3、4 条与 3.15 无关**（3.14.9 上逐字复现），是既有缺陷；第 1、2 条也非 3.15 引起。所以按判定表落在**第二档「绿但有 warning」**，**不需要**把 `requires-python` 封到 `<3.15` —— 没有任何真破坏是 3.15 带来的。
>
> 另外：**PyPI 上的 0.1.0 与仓库 HEAD 不是同一份代码**（`cli.py`、`client.py`、`downloader.py`、`errors.py`、`manifest.py` 五个核心模块都不同），所以 CLI 与 3b/3c 的关键项**两条树各测一遍**，避免把已修的 bug 算到 3.15 头上、也避免用未发布的 HEAD 掩盖已发布版本的现状。

## 0 · 环境与偏差（先说清楚，否则结果会被误读）

| 项 | 实际 |
| --- | --- |
| 平台 | Windows 11 26200/26300 (`Windows-11-10.0.26300-SP0`)，AMD64 |
| Python | CPython **3.15.1** final，`MSC v.1951 64 bit`，GIL **enabled**（`Py_GIL_DISABLED=0`，`sys._is_gil_enabled()` → `True`） |
| free-threaded (`3.15t`) | **本机没有这个构建**，PyManager 里只有 `-V:3.15`。所以 free-threaded 那条轴本次**未覆盖** |
| 安装来源 | PyPI `acadfetch 0.1.0`（sdist 与 wheel 各一次），以及 GitHub `main` 的 zipball（HEAD，`2026-10-26T01:41:42Z`） |
| 依赖 | 项目零运行时依赖；只装了 `pytest` |
| 归因用基线 | 另装 CPython **3.14.9**（`py install 3.14`）做对照，只用于判定"某缺陷是不是 3.15 特有"，不参与 3.15 的结论 |
| 代码是否被改动 | 未改。仓库为 zipball 原样，`repo` 57/57 文件与 zip 逐字节一致；所有探针在 `af315\probes\` |
| 代理 / conda / pyenv / Rosetta | 均无。`HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` 全空，`ACADFETCH_ENV` 未设置 |

与 POSIX 协议的四点偏差，逐条交代：

1. **Windows 没有 `timeout`/`SIGTERM`**。3c 用硬杀（`TerminateProcess`）代替 `timeout 3`；§4 另用真正的 `CTRL_C_EVENT`（Windows 的 SIGINT 拼写）验证 130。
2. **沙箱 TEMP 不可写**，`python -m venv` 的 `ensurepip` 因此失败过（下文 §1 有原文）。把 `TEMP`/`TMP`/`TMPDIR` 指到工作区后恢复正常；后来沙箱放宽，又用回系统 TEMP。
3. **`api.figshare.com` 从 Python 侧返回 404**（不是 issue 说的 403 WAF 页），但 `ndownloader.figshare.com` **返回 200 真文件**，所以 3b/3c **没有跳过**，跑的是真下载。
4. **`zenodo.org` 顶点域名的 DNS 被污染成 `0.0.0.0` / `::`**（`www.zenodo.org` 正常）。Python 侧仍能连通，node/undici 直连失败。记录在案，避免把 DNS 问题算成工具问题。

## 1 · 环境（原文）

```text
$ python -VV
Python 3.15.1 (tags/v3.15.1:c37e74c, Oct 21 2026, 06:24:08) [MSC v.1951 64 bit (AMD64)]

$ python -c "import sys, platform; print(sys.version_info, platform.platform(), platform.machine())"
sys.version_info(major=3, minor=15, micro=1, releaselevel='final', serial=0) Windows-11-10.0.26300-SP0 AMD64

$ python -c "import sysconfig; print('gil', sysconfig.get_config_var('Py_GIL_DISABLED'))"
gil 0

$ python -c "import sys; print('is_gil_enabled', getattr(sys,'_is_gil_enabled',lambda:'n/a')())"
is_gil_enabled True

$ python -m pip --version
pip 26.2.2 from D:\python\Lib\site-packages\pip (python 3.15)

$ python -c "import os,platform; print(os.cpu_count(), platform.architecture())"
20 ('64bit', 'WindowsPE')

$ systeminfo | head -8
systeminfo : ERROR: Access denied

$ py -0
 -V:3.15 *        Python 3.15 (64-bit)

$ echo "$ACADFETCH_ENV"
ACADFETCH_ENV=            (空)

$ echo "HTTP_PROXY=$HTTP_PROXY HTTPS_PROXY=$HTTPS_PROXY ALL_PROXY=$ALL_PROXY"
HTTP_PROXY= HTTPS_PROXY= ALL_PROXY=
```

构建是**带 GIL 的** `3.15t` 之外的普通构建，不是 free-threaded。

### 1a · `ensurepip` 在沙箱 TEMP 下的失败原文

第一次 `python -m venv` 直接失败，`venv-exit=1`，pip 没装上：

```text
File "D:\python\Lib\ensurepip\__init__.py", line 169, in _bootstrap
  copy2(bundled_wheel_path, tmp_wheel_path)
File "D:\python\Lib\shutil.py", line 529, in copy2
  copyfile(src, dst, follow_symlinks=follow_symlinks)
File "D:\python\Lib\shutil.py", line 315, in copyfile
  with open(dst, 'wb') as fdst:
       ~~~~^^^^^^^^^^^
PermissionError: [Errno 13] Permission denied: 'C:\\Users\\渡\\AppData\\Local\\Temp\\dsh-9urWTc\\tmpwrjo5ypv\\pip-26.2.2-py3-none-any.whl'
...
PermissionError: [WinError 5] 拒绝访问。: 'D:\\harness\\新建文件夹\\af315\\tmp\\tmpty31y7jd'
```

要点：这是 `venv`/`tempfile` 与沙箱 TEMP 的交互，**不是 3.15 的缺陷**，换任意可写 TEMP 即可。提一句是因为 `pip install` 也会用 TEMP，别人在受限环境里会撞到同一面墙。

## 2 · 两种安装方式（原文，全绿）

### 2a · 从 sdist 构建，无缓存

```text
$ python -m venv af315\venvA
venv-exit=0

$ pip install --no-cache-dir --no-binary :all: acadfetch
Collecting acadfetch
  Downloading acadfetch-0.1.0.tar.gz (211 kB)
  Installing build dependencies: started
  Installing build dependencies: finished with status 'done'
  Getting requirements to build wheel: started
  Getting requirements to build wheel: finished with status 'done'
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Building wheels for collected packages: acadfetch
  Building wheel for acadfetch (pyproject.toml): started
  Building wheel for acadfetch (pyproject.toml): finished with status 'done'
  Created wheel for acadfetch: filename=acadfetch-0.1.0-py3-none-any.whl size=33673 sha256=cd5e5182e7c565f8589472df09a70b789c7b4928626db431f150e7fd745c957c
  Stored in directory: C:\Users\渡\AppData\Local\Temp\pip-ephem-wheel-cache-6rvz4_9x\wheels\33\5b\57\2202dd9eea3a10a2f34c7ada96e0456c5f10d3d032781cc8b1
Successfully built acadfetch
Installing collected packages: acadfetch
Successfully installed acadfetch-0.1.0
exit=0
```

sdist 走完了 `setuptools.build_meta`，**无 `DeprecationWarning`、无 `SyntaxWarning`、无 setuptools 报错**，`requires-python >=3.10` 没有挡住 3.15。

### 2b · 从 wheel 装，第二个 venv

```text
$ python -m venv af315\venvB
venv-exit=0

$ pip install --no-cache-dir acadfetch
Collecting acadfetch
  Downloading acadfetch-0.1.0-py3-none-any.whl.metadata (8.1 kB)
Downloading acadfetch-0.1.0-py3-none-any.whl (33 kB)
Installing collected packages: acadfetch
Successfully installed acadfetch-0.1.0
exit=0
```

### 2c · `pip check` 与 `compileall`（两个 venv 都做了）

```text
--- [venvA] python -m pip check ---
No broken requirements found.
exit=0
--- [venvB] python -m pip check ---
No broken requirements found.
exit=0
--- [venvA] python -m compileall -q <pkgdir> ---
compileall exit=0
--- [venvB] python -m compileall -q <pkgdir> ---
compileall exit=0
```

`compileall` 静默通过 = 没有 `SyntaxWarning`（3.12+ 无效转义序列会报警，这里没有）。

### 2d · 额外一条：HEAD 也能装

为了让 §3 的对比有据，另建 `venvC` 装仓库 HEAD：

```text
$ pip install --no-cache-dir <repo>
Building wheel for acadfetch (pyproject.toml): finished with status 'done'
Successfully installed acadfetch-0.1.0
install exit=0
```

## 3 · 套件与 CLI

### 3a · 套件：192 passed，exit 0

`testpaths` 由 `pyproject.toml` 决定，cwd = 仓库根，导入的是**仓库源码（HEAD）**。

```text
$ python -W error::DeprecationWarning -m pytest -q
........................................................................ [ 37%]
----------------------------------------
........................................................................ [ 75%]
................................................                         [100%]
exit=0
```

`pyproject.toml` 里 `addopts = "-q"` 加上命令行的 `-q` 会变成 `-qq`，汇总行被吃掉，所以补一次带计数的：

```text
$ python -W error::DeprecationWarning -m pytest -q -rw -o addopts=''
........................................................................ [ 37%]
----------------------------------------
........................................................................ [ 75%]
................................................                         [100%]
192 passed in 44.14s
exit=0
```

**`-W error::DeprecationWarning` 下零命中**：没有 `email.utils`、没有 `datetime.utcnow`，也没有任何把 deprecation 变成 error 的用例。

`-X dev` 同样是 192 passed / exit 0，但**吐了 2 条 `ResourceWarning`**（默认过滤器把 23 条压成 2 条，见 §5）：

```text
$ python -X dev -m pytest -q 2>&1 | tail -30
........................................................................ [ 37%]
----------------------------------------
........................................................................ [ 75%]
................................................                         [100%]
============================== warnings summary ===============================
tests/test_downloader.py::test_plain_download
  D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 403: 'boom'>
    _warnings.warn(self.warn_message, ResourceWarning)

tests/test_downloader.py::test_plain_download
  D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 500: 'boom'>
    _warnings.warn(self.warn_message, ResourceWarning)

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
exit=0

$ python -X dev -m pytest -q -rw -o addopts=''
...
192 passed, 2 warnings in 43.96s
exit=0
```

### 3b · 真下载 + 篡改后 verify 必失败（HEAD）

`api.figshare.com` 从 Python 侧是 404，`ndownloader` 是 200 真 PDF（2.1 MiB）：

```text
$ acadfetch fetch "https://ndownloader.figshare.com/files/26000006" -o ./data
title   : 26000006
license : UNKNOWN - check before redistributing
files   : 1
landing : https://ndownloader.figshare.com/files/26000006
26000006  0 B
26000006  2.1 MiB  728.3 KiB/s
1 file(s)  2.1 MiB  728.3 KiB/s
ok   26000006 2.1 MiB

1/1 file(s) in data
exit=0

$ acadfetch verify ./data/manifest.json --root ./data
       ok 26000006

1/1 ok
exit=0

$ printf 'x' >> ./data/26000006

$ acadfetch verify ./data/manifest.json --root ./data     # expect 1, MISMATCH
 MISMATCH 26000006

0/1 ok
exit=1

$ ls -l ./data
   2192693 10:17:18  26000006
       761 10:16:36  manifest.json
exit=0
```

和预期一致：**篡改后必失败，exit 1，报文是 `MISMATCH`**。

### 3c · 中断与续传（HEAD）

`timeout 3` 在这台机器上落在解析阶段（figshare 解析要 ~3.5 s），什么都没有留下、目录都是空的。所以改成**一出现 `.part` 且超过 100 KB 就硬杀**：

```text
killed at .part=131072 bytes

--- ./r after kill (expect .part, no final file) ---
    131072 10:23:37  26000006.part
       170 10:23:37  26000006.part.meta.json
```

（早先 3 s 的版本，目录 `./r` 建立了但**空**，没有 `.part`。若要严格复现 `timeout 3` 语义得按机器速度调整。）

续传：

```text
$ acadfetch fetch "https://ndownloader.figshare.com/files/26000006" -o ./r
...
26000006  2.0 MiB  714.1 KiB/s
ok   26000006 2.1 MiB (resumed at 128.0 KiB)

1/1 file(s) in r
exit=0

--- ./r after resume ---
   2192692 10:23:56  26000006
       761 10:23:56  manifest.json

$ acadfetch verify ./r/manifest.json --root ./r
       ok 26000006
1/1 ok
exit=0
```

`resumed at 128.0 KiB`、只下了剩下的 2.0 MiB、最终 2192692 字节与首次完整下载一致 —— **真 Range 续传，不重下**。

### 3d · CLI 六连（两条树对照）

对 HEAD：

```text
$ acadfetch --version
acadfetch 0.1.0
exit=0

$ acadfetch sources
zenodo
figshare
arxiv
doi
direct
exit=0

$ acadfetch fetch nonsense
acadfetch: cannot tell what 'nonsense' is; try a DOI, a zenodo/figshare/arxiv id, or a URL.
known sources: zenodo, figshare, arxiv, doi, direct
exit=1

$ acadfetch verify /nonexistent/m.json
acadfetch: no such manifest: \nonexistent\m.json
exit=1
```

对已发布的 PyPI 0.1.0，前三条相同，`verify` 一条不同 —— **吐完整 traceback**：

```text
$ acadfetch verify /nonexistent/m.json
Traceback (most recent call last):
  File "<frozen runpy>", line 201, in _run_module_as_main
  File "<frozen runpy>", line 87, in _run_code
  File "...\venvB\Scripts\acadfetch.exe\__main__.py", line 5, in <module>
    sys.exit(main())
             ~~~~^^
  File "...\venvB\Lib\site-packages\acadfetch\cli.py", line 267, in main
    return args.func(args)
           ~~~~~~~~~^^^^^^
  File "...\venvB\Lib\site-packages\acadfetch\cli.py", line 174, in cmd_verify
    rows = manifest.verify_manifest(Path(args.manifest), root)
  File "...\venvB\Lib\site-packages\acadfetch\manifest.py", line 79, in verify_manifest
    data = load(path)
  File "...\venvB\Lib\site-packages\acadfetch\manifest.py", line 71, in load
    data = json.loads(path.read_text("utf-8"))
                      ~~~~~~~~~~~~~~^^^^^^^^^
  File "D:\python\Lib\pathlib\__init__.py", line 986, in read_text
    with self.open(mode='r', encoding=encoding, errors=errors, newline=newline) as f:
         ~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "D:\python\Lib\pathlib\__init__.py", line 970, in open
    return io.open(self, mode, buffering, encoding, errors, newline)
           ~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
FileNotFoundError: [Errno 2] No such file or directory: '\\nonexistent\\m.json'
exit=1
```

逐条对预期：

| 命令 | 预期 | 实测（HEAD） | 实测（PyPI 0.1.0） |
| --- | --- | --- | --- |
| `--version` | 版本一行 | 一行，exit 0 | 同 |
| `sources` | 列出来源 | 五行，exit 0 | 同 |
| `fetch nonsense` | 一行，exit 1 | **两行**，exit 1 | 同 |
| `verify /nonexistent/m.json` | 一行、无 traceback | 一行，exit 1 | **整段 traceback**，exit 1 |

两点说明，别误读：

- `fetch nonsense` 的“两行”其实**是一条消息**，`registry.py:59` 里就写着 `\n`，把 `known sources:` 接在后面。exit 码对，只是和 issue 的“one-line”不一致。
- `/nonexistent/m.json` 在 Windows 被规整成 `\nonexistent\m.json`（POSIX 绝对路径的 Windows 解），属正常。

### 3e · `-n` 试运行（zenodo）

```text
$ acadfetch fetch zenodo:23612969 -n
title   : Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler
doi     : 10.5281/zenodo.23612969
license : cc-by-4.0
files   : 1, 1.6 MiB
landing : https://zenodo.org/records/23612969
  would fetch Dinler Tarihi Alanında Güncel Konular ve Akademik Değerlendirmeler.pdf (1.6 MiB)
exit=0
```

两条树输出完全一致。非 ASCII 标题在 cp936 控制台下没有乱码（`cli.py:270` 会 `reconfigure(encoding="utf-8")`），这点在 Windows 上是有效的。

### 3f · 弃用陷阱专项（issue 第一条预判）：没中，且预判已过时

issue 让留意 `email.utils` / `datetime.utcnow` 一类。全仓只有三处相关调用，**没有一处已弃用**：

- `client.py:65` `email.utils.parsedate_to_datetime`
- `client.py:72`、`manifest.py:27` `datetime.now(timezone.utc)`（已经带 tz）

把这些路径打真跑一遍，零警告：

```text
$ python -W error::DeprecationWarning <probe: parse_retry_after + manifest.build/write>
parse_retry_after('120') = 120.0  [no DeprecationWarning]
parse_retry_after('Sun, 06 Nov 1994 08:49:37 GMT') = 0.0  [no DeprecationWarning]
manifest.build generated_at = 2026-10-26T02:33:05+00:00  [no DeprecationWarning]
manifest.write OK -> throwaway\manifest_probe.json size 544  [no DeprecationWarning]
---EXIT 0
```

两条树都一样；整套 192 条在 `-W error::DeprecationWarning` 下也是全绿。

`utcnow`/`utcfromtimestamp` 在 3.15.1 上**确实**会 warn，只是这个项目没用；issue 名单里 `cgi`/`imghdr`/`pipes`/`distutils`/`imp` 这些更是**已经被硬删**了：

```text
$ python -c "import cgi"
ModuleNotFoundError: No module named 'cgi'
$ python -c "import imghdr"
ModuleNotFoundError: No module named 'imghdr'
$ python -c "import pipes"
ModuleNotFoundError: No module named 'pipes'. Did you mean: 'types'?
$ python -c "import distutils"
ModuleNotFoundError: No module named 'distutils'
$ python -c "import imp"
ModuleNotFoundError: No module named 'imp'
```

22 个 3.11–3.15 删除的模块逐个 import，全 `ModuleNotFoundError`，而 acadfetch 一个都没用。**这条对 3.15 完全免疫。**

### 3g · 同一套件打已发布的 PyPI 0.1.0：18 处失败（全是版本差，不是 3.15）

把 `tests/` 与 `pyproject.toml` 复制到空目录、让 `acadfetch` 从 site-packages 解析（没有本地包目录，确认过导入路径是 `venvB\Lib\site-packages\acadfetch\__init__.py`、`__version__` 0.1.0），再跑同一套件。**八个组合全跑**（3.15.1 与 3.14.9 × `-W error::DeprecationWarning` / `-X dev` × 带或不带 `-o addopts=`）：

| 运行 | 解释器 | 退出码 | 失败/通过 |
| --- | --- | --- | --- |
| r1–r2 | 3.15.1 | 1 / 1 | 18 / 174 |
| r3–r4 | 3.15.1 | 1 / 1 | 18 / 174 |
| b1–b2 | 3.14.9 | 1 / 1 | 18 / 174 |
| b3–b4 | 3.14.9 | 1 / 1 | 18 / 174 |

```text
18 failed, 174 passed in 900.44s (0:15:00)
exit=1
```

（逐条 `PASSED`/`FAILED` 在 `_log_suite_010_verbose.txt`；八个组合的完整原文在 `af315\_log_suite_010.txt`，5,979 行。）

失败点**精确聚在五个有差异的模块**上：`test_cli.py` 10 条（9 条 `verify` 的“一行/no-traceback” + `test_retries_flag_reaches_the_transfer_layer`）、`test_client.py` 2 条（500 的 HEAD 重试语义）、`test_downloader.py` 6 条（长度不符 / 206 偏移 / 重试预算 / `--max-size` 丢 part）。这正是 HEAD 那批修复的对应测试，**属版本差，不是 3.15 引起**。

同一份 0.1.0 在 3.14.9 上跑同样的套件：**18 failed / 174 passed，退出码与失败集合逐条相同**（八份 FAILED 列表两两比对，顺序也一致）。两条树的失败面完全一致，再次说明和解释器版本无关。

还有一条**速度陷阱**，容易被误判成卡死：每轮要 14–15 分钟。原因是 0.1.0 的 `downloader._transfer` 调 `client.open(...)` 时**不传 `attempts`**（该参数是 HEAD 才加的，`downloader.py:113` vs 0.1.0 的 `:88`），于是重试预算落在 client 的 `min(0.8*2**attempt, 30)` 真实 sleep 上。`opts(retries=0)` 配 `Client(retries=9)` 会打 10 次、耗约 140 s；`opts(retries=4)` 重复 5 轮约 600 s。卡住时的 faulthandler 栈：

```text
File "...\acadfetch\client.py", line 104 in _sleep
File "...\acadfetch\client.py", line 158 in _with_retries
File "...\acadfetch\client.py", line 167 in open
File "...\acadfetch\downloader.py", line 88 in _transfer
```

所以：**"套件在 37% 卡了十分钟"不是卡死，是 0.1.0 在真睡。** 判 3.15 时以 HEAD 那轮（44 s、192 passed）为准，0.1.0 这轮只用来确认"发布版落后于测试"。

## 4 · 进程级退出码

### 4a · 先说 Windows 上的"shell 假象"长什么样

Windows 没有 `SIGINT` 可发，只能发控制台事件。第一版探针直接 `GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)`：**返回 1（成功），子进程却毫无反应，一路把 16 MiB 下完、exit 0**。`1` 只代表"调用成功"，不代表"有人收到" —— 这正是 issue 要排除的那类假象，在 Windows 上的形态。

可用的机制是：父进程先 `FreeConsole()`，子进程用 `CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP` 起，然后 `AttachConsole(子 pid)` → `GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0)` → `FreeConsole()`。

### 4b · 对照组：不捕获的 `KeyboardInterrupt` 是什么码

```text
$ driver_ctrl.py ... -c "import time; time.sleep(30)"      # 1.2 s 后发 Ctrl+C
raw returncode = 3221225786
unsigned       = 0xc000013a (3221225786)
--- child stderr ---
Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import time; time.sleep(30)
KeyboardInterrupt
```

裸进程没人捕获 `KeyboardInterrupt`，走的是系统通用码 `0xC000013A`（`STATUS_CONTROL_C_EXIT`）。**acadfetch 会捕获并自己返回 130**，所以下文的 130 是它给的进程状态，不是系统的、也不是 shell 编的。

### 4c · 实测：130 是真的

HEAD，默认并发：

```text
t+ 0.360s GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) -> 1
raw returncode = 130
unsigned       = 0x00000082 (130)
```

PyPI 0.1.0，`-j 1`：

```text
t+ 0.299s parts before event: {'blob.part': 0}
t+ 0.362s parts after exit: {'blob.part': 131072}
raw returncode = 130
unsigned       = 0x00000082 (130)
```

真实 PowerShell 里的 `$?` 视图与进程状态一致，不存在外壳篡改：

```text
LASTEXITCODE=130
dollar-question=True
global:LASTEXITCODE=130
```

（外层 powershell 自己的 `$LASTEXITCODE` 是 0，因为它跑的是脚本不是工具；脚本内捕获到的子进程码就是 130。）

### 4d · 但这里有个发现：默认并发下 Ctrl+C 停不住传输

同一个场景，**默认 `-j 3`**：

```text
t+ 0.360s GenerateConsoleCtrlEvent(CTRL_C_EVENT, 0) -> 1
t+16.345s child exited
t+16.345s parts after exit: {}
raw returncode = 130
--- child stdout ---
blob  0 B/16.0 MiB
blob  5.1 MiB/16.0 MiB  ...
blob  16.0 MiB/16.0 MiB ...
1 file(s)  16.0 MiB/16.0 MiB  1016.8 KiB/s  ETA 0:00
--- child stderr ---
interrupted - partial files kept, rerun to resume
--- ./k4 ---
blob 16777216
```

`KeyboardInterrupt` 只打断主线程，工作线程把 16 MiB 下完了进程才退。结果是一个自相矛盾的状态：**文件已完整、没有 `manifest.json`、exit 130**，还会打印"partial files kept"但其实没有 partial。脚本按 exit 码判断就会误以为失败。

换成 `-j 1`（传输回到主线程）就立刻停，`.part` 保留：

```text
t+ 0.428s parts after exit: {'blob.part': 131072}
raw returncode = 130
--- ./k4 ---
blob.part           131072
blob.part.meta.json     98
```

**这条不是 3.15 的问题。** 同样代码同样场景在 CPython 3.14.9 上逐字复现（默认并发 → 下完再退 130；`-j 1` → 立刻停、`.part` 327680 保留），见 `_log_4_cross314.txt`。所以它是既有缺陷，3.15 只是把它一起继承了；判定表里不该算到 3.15 头上。

### 4e · 保留的 `.part` 能续上

对 `.part` 目录续传后补完，最终文件长度 16777216、sha256 与完整副本一致（两条树都是）：

```text
ok blob 16.0 MiB (resumed at 128.0 KiB)
16777216 blob
sha256(blob) = 341AACAC661CCB210720BEDAA9EAD5D668FE5EA41A73532FC147C71E34040DF1
```

这个摘要值是三条独立路径分别推出来的（`_log_4_final.txt` §15），不是抄某一处。

### 4f · 另外两个码：1 与 2

`1`（失败）与 `2`（部分成功）在进程级也都成立，且外壳视图与进程状态一致：

```text
$ verify /nonexistent/m.json
raw returncode = 1        shell: 1

$ fetch <两文件记录，第二个 404>
raw returncode = 2        shell: 2
good.bin 保留，gone.bin 报 "FAIL ... HTTP 404"
```

`2` 需要一点外援：`fetch` 只收一个 target、每个适配器只产一个文件、zenodo/figshare 的 API 域名又是写死的，本地起服务器造不出双文件记录。用一个小 shim（`af315\_codes\partial2.py`）只替换 `registry.resolve`，`cli.main` 原样调用。另外 `verify` 只能返 0/1，出不了 2。

### 4g · `CTRL_BREAK_EVENT` 不等价，别拿来顶

```text
5 ms 内进程死亡，status 0xC000013A
但没有 "partial files kept" 那行、没有汇总
PowerShell 掉进调试器，报 -1073741510
```

因为 `CTRL_BREAK` 不触发 `KeyboardInterrupt` 分支，走的是系统通用码。所以"Windows 上随便杀一下看退出码"会得到完全不同的结论。

### 4h · Ctrl+C 也停不掉正在阻塞的 socket 读

服务器发 256 KiB 后静默，进程满 consts.DEFAULT_TIMEOUT（60 s）才退出 130（`.part` 保留）。**Ctrl+C 停的是后续工作，不是当前那次 read。** 也就是说第 4d 条在真实慢站上会放大成"按了 Ctrl+C 还得等一分钟"。

### 4i · 小结

| 期望 | 实测 |
| --- | --- |
| 130 是进程真状态，不是外壳假象 | ✓ 原始状态 130（0x82），`$LASTEXITCODE` 也 130 |
| 中断后 `.part` 保留 | ✗ 只在 `-j 1` 成立；默认并发下整份下完、无 `.part`、无 manifest |
| `.part` 可续传且结果正确 | ✓ 16777216 B、sha256 一致 |
| 退出 1 / 2 | ✓ 且外壳一致 |
| 中断及时 | ✗ 阻塞中的 read 不会被中断，最长等满 60 s |

后两条（4d、4h）都不是 3.15 带来的，3.14.9 上逐字复现；判定时别记到 3.15 账上。


## 5 · 那两条 `ResourceWarning` 到底是谁的锅（重要）

issue 预判的触发点是 `acadfetch/sources/arxiv.py:39`。**实际不是它。** 顺着 23 条 warning 查下去，来源在测试侧。

### 5a · 先把条数摊开

默认过滤器把重复的 warning 按「消息+位置」只留一条，所以只看见 403、500 各一条。换成 `always` 再数：

```text
$ python -X dev -m pytest -q -o addopts='' tests/test_client.py -W "always::ResourceWarning"
... (exit=1，因为 pytest 把它当 unraisable 报告)
15x D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 403: 'boom'>
 8x D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 500: 'boom'>
```

`boom` 这个串只出现在 `tests/test_client.py:89`：`http_error()` 构造 `urllib.error.HTTPError`。而这些对象是**在列表推导里提前造好、大部分从未被 raise**：

| 位置 | 造了几个 | 实际抛出的 | 泄漏 |
| --- | --- | --- | --- |
| `test_client.py:121` `[... for _ in range(10)]`（block 重试上限 2+1 次） | 10 | 3 | **7** |
| `test_client.py:149` `[... for _ in range(9)]`（HEAD 只试 1 次） | 9 | 1 | **8** |
| `test_client.py:177` `[... for _ in range(9)]`（HEAD 只试 1 次） | 9 | 1 | **8** |
| `:114` `:131` `:138` `:155` `:187` `:188` | 各 1–2 | 全部抛出 | 0 |

7 + 8 = **15** 条 403，8 条 500 —— 与实测计数逐条对上，不多不少。

同一个探针打在**已发布的 0.1.0** 上只有 8 条（7×403 + 1×500），默认过滤器后就剩 1 行：

```text
$ (0.1.0) python -X dev -m pytest -q tests/test_client.py -W "always::ResourceWarning"
7x D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 403: 'boom'>
1x D:\python\Lib\tempfile.py:538: ResourceWarning: Implicitly cleaning up <HTTPError 500: 'boom'>
```

两类都少了（403 从 15 降到 7，500 从 8 降到 1）：0.1.0 的 `client.py:142` 与各 403 分支上会 close 掉**被抛出的**那些，抛得越多泄漏越少；`head()` 又会拿重试去试 500（那两条测试因此失败，见 §3g），9 个里抛掉了 8 个。

**注意别据此以为 0.1.0 更干净** —— 泄漏机制完全相同，只是"被抛出并关闭"的比例不同。而且 `-X dev` 默认过滤器只会打出 500 那一条（403 那一批被拦在捕获路径里），所以在 0.1.0 上"只有 1 条警告"是个过滤器假象，实际是 8 条。

### 5b · 为什么"没抛出的那个"会警告

3.12 起 `urllib.error.HTTPError` 继承 `urllib.response.addinfourl`，而 `addinfourl` 又继承 `tempfile._TemporaryFileWrapper`（`D:\python\Lib\urllib\response.py:14`）。构造时会在 `tempfile.py:557` 建一个 `_TemporaryFileCloser`，其 `__del__`（`tempfile.py:538`）在"从没被 close 过"时发 `ResourceWarning`。

被 `raise` 的那些走 `client._with_retries`，`client.py:154` 有 `exc.close()`（在分支之前），所以是干净的；**没被 raise 的留在脚本 list 里，没人 close**，GC 时才报警。

隔离复现（0 条 warning）反过来证明库侧没问题：

```text
$ python -X dev _repro3.py
403 cause closed: True closer closed: True
403 warnings: []
500 cause closed: True closer closed: True
500 warnings: []
done
```

而 `tests/test_downloader.py::test_plain_download` 只是 GC 恰好落在它身上，不是它造成的。

### 5c · `arxiv.py:39` 实测：不漏

因为它压根没被套件跑到 —— `tests/` 里只有 `arxiv.match()` 的匹配表，**没有任何一处调用 `arxiv.resolve()`**（`test_cli.py` 用 `_stub_resolve` 把 resolve 整个打桩）。所以绿套件对这条路径零覆盖。

直接打真 arxiv 也没报警：

```text
$ python -X dev -W always::ResourceWarning -m acadfetch fetch arxiv:2401.12345 -n
title   : Distributionally Robust Receive Combining
doi     : 10.48550/arXiv.2401.12345
license : arxiv-nonexclusive-distrib
files   : 1
landing : https://arxiv.org/abs/2401.12345v3
  would fetch 2401.12345.pdf (?)
exit=0
```

原因：

```text
$ python -c "...# headers + will_close"
{'Connection': 'close', 'Content-Length': '3112'}
will_close= True
```

arxiv 发 `Connection: close`，`read()` 读到 EOF 时 `HTTPResponse` 自己把连接关了，所以 `client.open(...).read()` 这种缺 `with` 的写法在这里不流血。给一个 keep-alive 的响应才会漏（本地 HTTP/1.1 + `Content-Length` 的复现里就出现了 `ResourceWarning: unclosed <socket.socket ...>`）。也就是说：**`arxiv.py:39` 是"侥幸不漏"，不是"安全"**——arxiv 或中间代理哪天改 keep-alive，它就会开始漏，而且套件看不出来。

### 5d · 一个容易漏掉的结构问题

`-W error::DeprecationWarning`（issue 的第一条套件命令）**抓不到这些**，因为它们是 `ResourceWarning`。所以"第一条命令 exit 0"和"有资源泄漏"同时成立 —— 只看那条命令会得出错误结论。修复方向在测试侧：`fast()` 接收可调用对象按需构造、或把未消费的脚本项在 fixture 收尾时 close 掉。

### 5e · 顺手确认：真实用户看不到这些

真打一个 404（真 `HTTPError`，不是 fake），`-X dev -W always::ResourceWarning` 下依然干净：

```text
$ python -X dev -W always::ResourceWarning -m acadfetch fetch http://127.0.0.1:8798/missing.bin -o ./w404
acadfetch: HTTP 404 for http://127.0.0.1:8798/missing.bin
exit=1
```

原因是 `client.py:154` 在分支之前就 `exc.close()` 了。所以这是**纯测试卫生问题**，不是用户可感知的缺陷 —— 但会因为 `-X dev` 把 CI 变噪音而咬人。

## 6 · `os.replace`：issue 三条预判里唯一真中的一条

issue 让留意"`os.replace` 在 APFS/网络盘的行为"。**它确实是问题，但机制和预判不同，而且跟 3.15 无关。**

### 6a · 真正会炸的是同目录下的并发读者，不是网络盘

`manifest.write()` 的写法是"写 `manifest.json.tmp` 再 `os.replace` 覆盖目标"。Windows 上只要**有另一个进程或句柄把目标开着**，`os.replace` 就 `PermissionError [WinError 5]`。采样 150 轮死 138 轮：

```text
{"case": "a-atomic-plain", "rounds": 150, "replace_ok": 12, "replace_failures": 138,
 "replace_failure_kinds": ["PermissionError"],
 "replace_failure_sample": ["PermissionError: [WinError 5] 拒绝访问。:
   'C:\\Users\\渡\\AppData\\Local\\Temp\\atomic-0.tmp' -> '...\\atomic.bin'"],
 "torn_observations": 0, "dest_final_size_ok": true, "dest_final_uniform": true}
```

换成真实调用的 `manifest.write` vs `manifest.load` 对打：**写 98/120 失败，读也有 19 次失败**（`ManifestError: cannot read ...: Permission denied`）。

**内容原子性本身是成立的**（150 轮 0 次半写）。坏的是并发容错。

### 6b · 用户看到的是崩溃，不是一行错误

`cmd_fetch` 调 `manifest.write` 没有 `OSError` 兜底（`cli.py:163,166`），`main()` 只捕 `TargetError`/`AcFError`/`KeyboardInterrupt`。于是：**下载成功之后、写清单时崩**。

```text
$ (另一进程把 j3\manifest.json 开着 45 s) ; acadfetch fetch http://127.0.0.1:8792/blob -o ./j3 -j 3
title   : blob
...
skip blob 16.0 MiB
---EXIT 1
Traceback (most recent call last):
  File "D:\python\Lib\runpy.py", ...
  File "...\acadfetch\cli.py", line 163, in cmd_fetch
    manifest.write(out_dir / "manifest.json", manifest.build(record, results, token))
  File "...\acadfetch\manifest.py", line 68, in write
    os.replace(tmp, path)
PermissionError: [WinError 5] 拒绝访问。: 'j3\\manifest.json.tmp' -> 'j3\\manifest.json'
stray tmp left in j3: blob, manifest.json, manifest.json.tmp   <-- 孤儿
```

HEAD 与 PyPI 0.1.0 同样炸（行号 163/68 与 162/67 之别），**exit 1 + 孤儿 `.tmp`**。对 3b 的"篡改 → MISMATCH"没影响，但对"边下载边 verify"这种用法直接崩。

### 6c · 预判的三条里，只有一条半成立

| issue 的说法 | 实测 |
| --- | --- |
| 网络/APFS 盘 | 网络盘这条**测不到**（本机无 APFS）；UNC `\\localhost\C$` 与 `\\?\` 扩展前缀确实报 `WinError 17`，但原因是跨卷，不是 UNC/前缀本身 |
| 非 ASCII 路径 | **无罪**。同卷通过，235 字符长路径也通过；之前"失败"只是因为它把源放在 C: 而目标在 D: |
| 跨卷 | **150/150 失败**，`WinError 17`（`os.replace` 不负责复制）。正常用法够不着，因为 `.tmp`/`.part` 都是同目录兄弟 |

### 6d · 归属：不是 3.15

同一条探针在 3.14.9 与 3.15.1 上完全相同：

```text
3.14: failures 100/100
3.15: failures 100/100
```

所以这是 Win32/NTFS 的既有性质（Python 的 `open()` 不带 `FILE_SHARE_DELETE`），**不能拿它去封 `requires-python <3.15`**。修法是 `os.replace` 加重试（Windows 上 3–5 次、几十毫秒退避即可），顺便让 `cmd_fetch` 兜住 `OSError`。

## 7 · 未覆盖项（别把这份报告当全绿）

1. **free-threaded（`3.15t`）完全没测。** 本机没有该构建，PyManager 只有常规 3.15。issue 关心的 `-j 3` 竞争问题**没有任何证据**，需要另找一台 `3.15t`。
2. **只测了 Windows。** macOS/APFS 与 Linux 都没跑；issue 里 `os.replace` 在 APFS/网络盘的担忧只在 Windows/NTFS 上得到回答。
3. **figshare API 路径没走通。** `api.figshare.com` 从本机 Python 返回 404（不是 issue 预期的 403 WAF 页），所以 `figshare.resolve()` 的真实端到端没验；被验证的是 `direct`/`ndownloader` 这条。
4. **套件对 `arxiv.resolve`、`doi.resolve`、`figshare.resolve` 的真实实现零覆盖**（全走 fake client 或打桩），绿套件在这几条路径上不能当证据。
5. **单机单次。** 没有重复跑，抖动/并发相关的偶发问题可能没抓到。
6. 沙箱 TEMP 与 DNS 异常是本机特性，见 §0 偏差说明。

## 8 · 对判定表的落点

| issue 的三档 | 本次落点 |
| --- | --- |
| 全绿无警告 → 进矩阵、宣称支持 | ✗ 有 23 条 `ResourceWarning`（§5） |
| 绿但有 deprecation/resource 警告 → 先修或钉住 | ✓ **落在这里** |
| 装不上 / 套件红 / `os.replace`·`pathlib`·`urllib` 行为变 → 封 `<3.15` | ✗ 都不成立；`os.replace` 是真缺陷，但 3.14 上一模一样（§6d），不是 3.15 引起的行为变化 |

具体到"先修什么"，按用户可感知程度排：

1. **P1 — `os.replace` 并发崩溃**（§6）。下载成功却因为清单覆盖失败而崩、exit 1、留孤儿 `.tmp`；任何"边下边 verify"的用法立刻中招。`manifest.write` 加 Windows 重试 + `cmd_fetch` 兜 `OSError`。**与 3.15 无关，但比下面三条都值钱。**
2. **P2 — 默认并发下 Ctrl+C 停不住**（§4d、§4h）。`KeyboardInterrupt` 只打主线程，工作线程把整份下完并改名，最终"文件完整 + 无清单 + exit 130 + 却说 partials kept"；阻塞中的 `read` 还要等满 60 s。同样与 3.15 无关。
3. **P3 — 测试侧 23 条 `ResourceWarning`**（§5）。纯测试卫生，会让任何 `-X dev` 的 CI 变噪音；`tests/test_client.py:121/149/177` 改成按需构造即可。
4. **P4 — 0.1.0 落后 HEAD 一个版本周期**（§3d、§3g）。`verify` 的 traceback、10 条 `verify` 一行性测试、6 条 downloader 长度/重试测试、`attempts` 参数全都在 HEAD 修了但没发版；`pip install acadfetch` 的用户拿到的是旧行为，且一轮套件要 14–15 分钟。先发版。
5. **P5 — `arxiv.py:39` 补 `with`**（§5c）。当前侥幸不漏；顺带补一条真走 `resolve()` 的测试（套件现在对它是零覆盖）。
6. **P6 — `fetch nonsense` 的第二行**（§3d）。`registry.py:59` 用 `\n` 把 `known sources:` 接在同一条消息里，和"one-line"预期不符。

P1/P2 与 3.15 无关，但值得在宣称支持之前一起修掉；**不需要**把 `requires-python` 封到 `<3.15`，因为没有任何真破坏是由 3.15 引起的。

## 9 · 一句话回答 issue 第 4 项

**装得上、192 个用例全绿、续传/校验/中断的退出码都对，但 `-X dev` 下 23 条资源警告（全在测试侧、已定位到行）、已发布 0.1.0 的 `verify` 会吐 traceback、`os.replace` 在有并发读者时崩溃、默认并发下 Ctrl+C 停不住下载 —— 后三条与 3.15 无关（3.14 逐字复现），但"一行错误、无 traceback、partials kept"这些预期确实被打破了。**


