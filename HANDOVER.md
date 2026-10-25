# 交接文档

给下一个在这个目录里开新会话的人（大概也是我）。看完这份再动手，能省掉一整轮踩坑。

**这个目录本身就是一个 git 工作区**，`git rev-parse --show-toplevel` 返回它自己。没有需要 `cd` 进去的子目录。

---

## 一句话状态

`acadfetch` 0.1.0：学术数据仓库的批量下载器，断点续传 / 全局限速 / 校验和验证 / 落可复验的 manifest。**已公开在 <https://github.com/young-ski/acadfetch>，MIT，main 分支 HEAD `7de4768`，工作区干净，远程 `origin` 走 ssh，CI 9 个 job 全绿。**

已经能日常用：真实 Zenodo 记录下载、DOI 解析、512 KiB 处 kill 后续传成功、篡改文件后 `verify` 退出 1、figshare 下载端（`ndownloader`）2.1 MiB 实测跑通。**还没有** PyPI 发布、没有 release tag、没有 OAuth。

## 先跑起来

```bash
python -m pip install -e . pytest ruff     # 已装过可跳过
python -m ruff check . && python -m ruff format --check .
python -m pytest -q                          # 153 个测试，全离线
```

测试必须离线可跑：`tests/test_downloader.py` 起了本地 HTTP 服务器，能被告知「忽略 Range」「截断响应」「喂坏校验和」「回 WAF 块页」。真实响应体在 `tests/fixtures/`，来源见那里的 README。改传输逻辑前后都跑它。

需要真实网络的验证用这两个脚本（会连 Zenodo，网络慢时可能几分钟）：

```bash
python scripts/probe_resume.py 23612969 400000   # 在 400 KB 处中断，然后续传，断言最终大小一致
python scripts/slow_server.py                    # 本地 1 MiB/s + Range + HEAD，看进度条用
```

## 代码地图

| 文件 | 职责 |
| --- | --- |
| `acadfetch/downloader.py` | 传输引擎：`.part` + `Range`/`If-Range`、重试、校验、`os.replace` |
| `acadfetch/client.py` | **所有**出站请求的唯一入口：令牌桶、重试、`Retry-After`、按源附带凭据 |
| `acadfetch/naming.py` | 把仓库元数据（敌意输入）变成安全文件名 |
| `acadfetch/verify.py` | 校验和解析与流式比对 |
| `acadfetch/manifest.py` | manifest 生成、写入、复验 |
| `acadfetch/progress.py` | 聚合进度行，TTY 原地重绘，管道退化为整行 |
| `acadfetch/auth.py` | 环境变量 token，按源绑定，只打印尾四位 |
| `acadfetch/sources/*` | 适配器：`zenodo` / `figshare` / `arxiv` / `direct` / `doi` + `registry` 分发 |
| `acadfetch/cli.py` | 参数、退出码（0 成功 / 1 失败 / 2 部分 / 130 中断） |

## 不许破坏的不变量

这些是设计契约，改之前先想清楚，改完补测试：

1. **零运行时依赖**。`urllib` 就够，加依赖要论证。
2. **所有出站请求走 `Client`**。绕过去就没有限速和重试，算 bug。
3. **字节只落 `.part`，校验通过才 `os.replace`**。中断不能留下看着完整的文件。
4. **仓库元数据全是敌意输入**。变文件名的过 `naming.sanitize`，变路径的过 `naming.unique`。
5. **token 按源绑定**，只会发往自己那个仓库；进 manifest 的只有变量名和尾四位。
6. **进度永远不能失败下载**。它自己吞掉流错误，禁用时所有钩子是 no-op。
7. **没有校验和的源必须报 `verified=null`**，不能假装安全。

## 已经修过的坑（别退回）

- doi.org 把 Zenodo DOI 解析成 `https://zenodo.org/doi/10.5281/zenodo.N` 落地页，早期会被判成 `direct`，导致标题/许可为空、大小取 HEAD 的错值。现在 zenodo 有专门 pattern，registry 也会把 doi.org URL 先还原成 DOI。
- 进度在最后一个文件 `stop` 之后会渲出 `0 file(s)`。现在 `stop` 先发一帧（文件仍在计数）再移除，`finish` 回退到 `_last_count`。
- 输出里的 em dash 在 cp936 控制台变成 `?`。现在 `main()` 把 stdout/stderr 强制 UTF-8 + `errors=replace`。
- `_render()` 在 `_maybe_emit()` 持锁时被调用，普通 `Lock` 会自死锁。现在用 `RLock`。
- 403 曾被当成和 401/404/410 一样的终态，figshare 的 WAF 一抽风整个记录就打不开。现在先 `client.is_block_page` 分类：HTML/空体算块页，退避重试 2 次并保留 `.part`；JSON 权限错误仍然首败。别把分类挪回 `downloader`，`head()` 和元数据请求也要用。

## 未完成的活

**#1 figshare 403 —— 已修（2026-10-25）**
`api.figshare.com` 在 WAF 后面，对非浏览器 UA 间歇 403。现在 403 分两类：块页（HTML / 空体，AWS ELB 默认页）退避重试 2 次，然后换 `download_url` 的文件级 fallback；API 自己的权限 403（JSON `{"message": "Forbidden"}`）仍然立即失败。分类在 `client.is_block_page`，`HttpError.transient` 负责往上传递。真实 403 体存在 `tests/fixtures/figshare_403_block.html`，别再退回「403 全是终态」。注意 `head()` 不花这 2 次重试（HEAD 只是探大小，403 本来就吞）。

下一件要做的话：**figshare 适配器仍然没法在这台机器上端到端验证** —— 见下面的环境脾气。等换到不挡 API 的网络，用 `python -m acadfetch fetch figshare:13054873` 跑一遍并把 `tests/fixtures/figshare_article.json` 换成真实抓取（现在那个是按 v2 schema 重建的，README 里写了）。

**其它方向**（都不急）

- PyPI 发布 + `0.1.0` release tag；`python -m build` 已经能过（CI 的 `build` job 每天在跑）
- 认证只有 `--header` / 环境变量，没有交互式 `login`，也没有取 token 后刷新
- 没有搜索和分页，用户自带 target；OSF 适配器可以考虑，`auth.TOKEN_ENV` 里已经预留了命名习惯
- 并发多文件时进度只给计数不给文件名，可以按需加

## 环境脾气

- **网络对 Zenodo 时好时坏**：好时 8 MB/s，坏时 200 KiB/s 并且会长时间停住。写验证脚本要给足够 timeout，别把慢当成 bug。
- **figshare 的状况是分主机的**：`api.figshare.com` 从这台机器上**恒定** 403（AWS ELB 块页，换浏览器 UA 也没用），`ndownloader.figshare.com` 完全正常且支持 `Range`（实测 2.1 MiB 文件 920 KiB/s）。所以 figshare 的**下载**能端到端跑通、**解析**不能。验证下载用：
  `python -m acadfetch fetch https://ndownloader.figshare.com/files/26000006 -o .tmp/x`
- `verify` 的 `--root` 默认是当前目录，不是 manifest 所在目录；不传就全报 `missing`，不是 bug。
- **curl 和 PowerShell 的 TLS 经常失败（SSL error 35），python urllib 反而正常**。探路用 python，别用 curl。
- **`gh` 在机器级 PATH 里，但已运行的 host 进程没继承**。命令里前插 `export PATH="/c/Program Files/GitHub CLI:$PATH"` 就能用；新进程可能直接可用，先 `command -v gh` 试一下。
- **MSYS 的 `kill` 常常杀不掉真正的 `python.exe`**，会留下还在写文件的孤儿进程，制造「续传后文件超出预期大小」的假 bug。按端口杀更稳：
  `pwsh -c "Stop-Process -Id (Get-NetTCPConnection -LocalPort 8792 -State Listen).OwningProcess -Force"`
- **bash 运行时偶尔起不来**（`CreateFileMapping ... Win32 error 5`）。不是权限问题，直接重试同一条命令。
- 会话目前是 `danger-full-access` + `never` 审批，不需要申请沙箱升级。

## 提交与发布约定

- 消息用 `type: 概要` + 正文讲动机，正文里写 `closes #N` 会自动关 issue。
- 提交前必须：`ruff check` + `ruff format --check` + `pytest` 全绿。CI 在 Linux/Windows × 3.10–3.13 上重复这些，绿了才算完。
- 一次提交只做一件事；两个 issue 混在一起时宁可再拆。
- 公开仓库，提交前确认没有把内网地址、账号、token 写进去。
