# acadfetch 优化计划（联合大脑工作稿）

> 基线：`main` @ e891841，v0.1.1。206–210 tests 全绿、coverage 89%、`ruff` 干净。
> 标注：**事实**＝本轮实测或代码行；**假设**＝推断；**建议**＝待决策。
> 分级：**必须做 / 值得做 / 可选实验**。
> 探针脚本留在 `.tmp/`（已 gitignore）。

---

## 第 1 步：项目理解摘要

1. `acadfetch` 是一个零运行时依赖的 Python CLI：按 DOI/Zenodo/Figshare/arXiv/直链批量取文件。
2. 给谁用：要复现论文数据集的研究者、数据工程师、以及需要"取数据"那一步的流水线作者。
3. 核心价值不是"下载"，是**取完之后还能证明拿到的是什么**：断点续传、全局限速、按仓库公布的校验和验证、落 `manifest.json`（DOI + 许可 + 每文件 SHA-256）。
4. 它明确划了边界：不是搜索器、不抓付费墙、不是引文管理器（README:23-25）。
5. 工程质量高于同类早期项目：OIDC 发布、覆盖率为门、`-W error`、wheel/sdist 内容断言、真实抓取的 fixture、路径逃逸检查。
6. 当前最可能短板（一句话）：**"首次成功"这条路径是断的** —— README 自己那条命令在抖动链路上必失败，失败后连 `.part` 都删掉。
7. 次短板：三条"完成但字节错"的路径仍可达（HTML 错误页、`416`、超时毒化），且四处文档声明与实现相反。
8. 定位风险：`zenodo_get` 已有续传 + md5，所以"能续传"本身卖不动，差异必须落在**跨源 + 可离线复验的凭据**上。

**是否理解正确？** 若"价值 = 可复验的取数凭据"这一条理解偏了，第 4 步之后的选择会整体偏移。

---

## 第 2 步：项目体检

### 需要你先补的 5 个信息（会直接改变计划）

1. 主 KPI 是哪一个：首次成功率 / 被引用的 manifest / 被某条流水线采纳 / star？（决定优先 interop 还是可靠性）
2. 是否允许破 manifest `schema`（加 `resolved_name`、`verified_reason`）到 schema 2？
3. 每周真实可投入时长？（当前 CI 的体量已超出"一人 2 小时"）
4. 能否接受夜间 live CI（会向 zenodo/figshare/arxiv 发真实请求）？
5. `figshare` 还要不要保？它的 WAF 特判是 `client.py` 里最复杂的部分，也是最脆的一环。

### A. 用户价值

- **事实** 谁会来：跑复现的研究者、数据工程、流水线作者。替代品：`zenodo_get`（仅 Zenodo、AGPL、无 manifest）、`pooch`（库、无续传）、`curl`/`wget` 循环、`datalad`（重）。
- **事实** 首次成功路径断在第一步：`README.md:11` 的示例 DOI 在本机**两次都失败**（`cannot read from timed out object`，50 s / 44 s，0 字节，且留下的目录里只有 `manifest.json`）。
- **事实** `README.md:64-68` 的 Usage 示例前两条是占位符：`zenodo:123456` → 404，`figshare:987654` → 403（实测）。
- **事实** 默认输出目录 `acadfetch-out/`（`cli.py:124`）在 `-n` 下不打印，`.gitignore:10` 只忽略 `out/`，于是它会脏用户的分析仓库。
- **建议** 首次成功路径其实很短（一条命令），**必须先让它真的通**，再谈增长。

### B. 产品质量

- **事实（严重）** 任何 ≥1.0 s 的读停顿会**永久毒化**响应：`_chunks`（`downloader.py:110-135`）把 socket 超时压到 1 s 轮询，第一次超时被 `except TimeoutError` 吞掉，**下一次 `read1` 抛 `OSError("cannot read from timed out object")`** 且该响应再不可读。本地复现：一个 100 KB 文件 + 中间停 2 s → 稳定失败，19.7 s 内烧完 5 次重试。阈值实测 0.5 s 成功 / 1.2 s 失败。
- **事实（严重）** 失败即删除 `.part`（`downloader.py:315-316`），与 `README.md:33`"keeps the partial"、`:24-25`"死在被 VPN 打断的 80%"直接矛盾。Ctrl-C 反而保留 → 语义分裂。
- **事实** `http.client.IncompleteRead` 未捕获（`downloader.py:301` 只捕 `OSError/ConnectionError/ValueError`；`HTTPException` 不是其子类）→ traceback、退出 1、无 manifest、同批已完成结果在 `:408-409` 丢失。
- **事实** 从不读 `Content-Type`（`downloader.py:168-170`），一个 HTML 错误页会以 `downloaded` 落成 `.pdf`，复跑还会被 `:240-241` 当已完成跳过。
- **事实** `416` 时若尺寸未知直接当"已完成"（`:163-166`），`bytes */N` 从不解析 → 陈旧字节被改名成"成功"。
- **事实** manifest 记录的是 `naming.sanitize(f.name)`（`manifest.py:42`），而落盘用的是 `naming.unique(...)`（`downloader.py:233`）。重名记录 → 正确下载后 `verify` 报 **MISMATCH**（已复现：`a.bin` / `a (2).bin` 落盘正确，manifest 两行都写 `a.bin`，第二行 MISMATCH）。
- **事实** `Content-MD5` 是 base64，却按 hex 解析（`client.py:226` vs `verify.py:31`）→ 直链路径的校验是死代码，静默降级为 `verified: null`。
- **事实** `dict(resp.headers)`（`client.py:208`）毁掉大小写不敏感，小写 `content-length` / `x-checksum-sha256` 全漏。
- **事实** 校验和不可解析时无任何告警（`verify.py:29-32`）。
- **事实** 无磁盘预检、改名前后无 `fsync`；对目录路径无处理（`downloader.py:341` 抛未捕获 `PermissionError`）。
- **事实** `--rate 0` 直接吐 traceback；`-j 0`/`--retries -1` 静默夹取；`--max-size` 以 `HTTP 0 for …` 上报；`-n` 下 `--only` 被完全忽略。
- **事实** 唯一的重复 `return written`（`downloader.py:227-229`）；fallback 失配后重下的是 `file` 不是 `source`（`:333`）。
- **事实** `urllib` 重定向全量转发头，不区分主机/协议（`client.py:123-124`）→ Bearer 会跟到别的 host，并且会打断 S3 presigned URL；`README.md:95` 的"不会发到无关主机"只对首跳成立。

### C. 开发者体验

- **事实** 安装是干净的（零依赖，`pipx`/`uvx` 都可行），但 README 没有 `uvx` 一行；`pipx install git+…` 与 `pip install -e .` 混在同一个"or"里。
- **事实** `fetch -h` 里 12 个 flag 有 4 个没 help（`-j`、`--manifest-out`、`-n`、`-q`，`cli.py:257-262`）；`--help` 不讲目标语法、退出码、默认目录、token。
- **事实** `fetch` 不带目标只给 argparse 的 "required: target"。`sources` 只打印 5 个单词。
- **事实** `CONTRIBUTING.md:7` 的本地命令与 CI 不一致（缺 `--cov-fail-under=85` 与 `-X dev -W error`，`ci.yml:49,53`）。
- **事实** 可复用的 HTTP 测试器私有在 `test_downloader.py:15-107`，没有 `conftest.py`；`slow_server.py:1` 的说明与实际速率（1 MiB/s）不符。
- **事实** 声称 `Typing :: Typed`（`pyproject.toml:31`）并发布 `py.typed`，但**没有任何类型检查门**，还有 6 处 `noqa: ANN*`。
- **事实** 无 `--json`，脚本只能解析人读文本。

### D. 开源增长

- **事实** 第一屏很强：hero 输出、"问题 / 循环里会发生什么 / 我们做什么"三行对照表、明确的"不是什么"。
- **事实** 模板与策略异常成熟：`pull_request_template` 把不变量变成勾选清单，`SECURITY.md:38-44` 预先写下自己的已知限制。
- **事实** 缺口：无 `CITATION.cff`、无 `CODE_OF_CONDUCT.md`、无 feature-request 模板（`config.yml` 关掉了空 issue）、无 dependabot/CodeQL/pip-audit、action 全部浮在 major tag 上（`pypa/gh-action-pypi-publish@release/v1` 还是分支）。
- **事实** `docs/` 只有 1 篇 789 行的中文 3.15 审计，没有任何使用文档；而 README 把它当门面（`:210`）。
- **事实（发布风险）** `release.yml:27` 写"same gate, same commands"，但 `:34` 只是裸 `pytest -q` —— 没有覆盖率门、没有 `-W error`、没有 `twine check`、没有 wheel 内容断言。**发布门比 PR 门弱，且已经漂移。**`release.yml` 还无 concurrency（两个 tag 会抢同一个文件名）、无 timeout、无"tag 必须在 main 上"的守卫。
- **假设** 现在 ~0 star、1 open issue；推荐主要靠"看得见的 30 秒"和与既有工作流的接口，而不是功能数量。

---

## 第 3 步：多角色圆桌（每角色 3 条，非显而易见）

### 新用户
1. `acadfetch fetch` 不带目标时，直接打三条可粘贴的真实命令（不是 usage dump）。理由：这是 5 分钟内唯一的岔路，现在这里是一个死胡同。
2. README 第一行给 `uvx acadfetch fetch 10.5281/zenodo.23612969`，示例里顺带展示 manifest 的头两行。理由：把"为什么不 curl"变成能看见的产物，而不是让人相信。
3. 失败时给下一条命令（超时 → `--rate 1 -j 1` 重试；403 → `--retry-403 2`），不要只给 `cannot read from timed out object`。理由：现在这句是 urllib 的内部话术，且上方的 `0 B` 在撒谎。

### 长期贡献者
1. 把不变量写成 `docs/invariants.md`，每条挂一个测试名，PR 模板勾选项链过去。理由：不变量现在散在 `CONTRIBUTING.md:16-21` 与注释里，改 downloader 的人拿不到全景。
2. 把 `test_downloader.py:15-107` 的故障注入器提到 `conftest.py` 并加 `answers_416` / `stall_after` 旋钮。理由：新 adapter 作者现在要读 100 行私有类才能写测试。
3. 加 `acadfetch --record-fixture <target>`（开发者命令）：自动重放并脱敏 token，产出符合 `tests/fixtures/README.md` 的抓包。理由：fixture 的获取现在是手抄的一段 `python -c`。

### 维护者
1. 夜间 live smoke：3 个固定公开记录，失败只开 issue 不红 CI。理由：把 API 漂移从用户 bug 报告移到自己的告警，这是唯一能替代人工的杠杆。
2. `needs-repro` 标签 + issue 表单里强制贴 `-n` 输出（模板已有此项，但没强制）。理由：往返次数是单人维护的真实成本。
3. 把 `release.yml` 里已经写好的门禁抽成 `scripts/release_check.py`，本地与 CI 共用。理由：现在发布门比 PR 门弱，修的时候顺便让它可本地复跑。

### 性能工程师
1. SHA-256 只算一次：现在下载后 `verify.matches` 全读一遍（`verify.py:45-47`），`manifest._sha256` 再全读一遍（`manifest.py:62`），失配重下再来一遍 —— 3 个满盘读。改成传输中流式算，或至少并行。
2. `manifest.build` 的哈希是串行主线程，100 GB 记录时它是一堵墙。
3. 并发提交改成有界队列（`downloader.py:405-407` 一次性 submit 全部），5000 文件的记录下 futures 与进度键都会失控。

### 安全工程师
1. 自定义 `RedirectHandler`：跨主机剥 `Authorization`、https→http 阻断（或至少告警）、把跳转链记进 manifest。理由：`README.md:95` 的承诺现在是假的。
2. 检出 `X-Amz-*` 签名 query 时不要附加 `Authorization`（会 400）。
3. `--header` 的广播范围与 env token 的优先级显式化；加 `--dump-request` 脱敏回放。理由：`--header` 现在既广播到所有源、又留在 argv 里，而 `auth.py:3-5` 的 docstring 说正是为了避开 argv。

### 文档工程师
1. 一页按症状索引的 troubleshooting（403 / 超时 / 文件名被改 / verify MISMATCH / 长路径 / 令牌）。
2. "为什么 manifest 不签名"单独成节并链到 `SECURITY.md` 的已知限制，而不是埋在一行。
3. 把 212 行的 README 拆成第一屏 + `docs/{usage,verify,manifest,adapters,faq}`，同一屏内先修掉 3 处与实现相反的声明。

### 竞品产品经理
1. `-w/--urls` 导出交给 aria2/rclone。理由：`zenodo_get` 有，缺它是可感知短板；也让 acadfetch 成为上游而不是对手。
2. `--cite` 打印推荐引用 + manifest 路径，直接贴进方法节。理由：把"可复现"从 README 的形容词变成论文里的一行。
3. 定位从"又一个下载器"改成"流水线里的取数据那一层"，并明确"不做"：不建库、不解析全文、不碰 paywall。理由：唯一能同时避开 `pooch`（库）和 `zenodo_get`（单源）的坐标。

### 苛刻的 Reviewer
1. `_drop(part, meta_path)` 与 README 的续传承诺矛盾 → 要么改代码，要么改文案，不能两存。
2. `human(None) == "?"` 被当尺寸用，`-n` 打出 `would fetch x (?)`，读者会当成真实大小。
3. `Typing :: Typed` + `py.typed` + 零类型门 + 6 个 `noqa: ANN` → 要么加门槛，要么撤掉声明。

### 只想快速试用的开发者
1. `uvx` 一行，且放在 Install 最前面。
2. `-n` 的输出末尾给"复制这一行开始下"。
3. `--json` 一行结果，不用解析人读文本就能进脚本。

---

## 第 4 步：机会地图（发散 30 → 去重 21）

先列 30 个候选（去重前的原始池）：
①1 s 轮询毒化 ②失败删 `.part` ③`IncompleteRead` 未捕获 ④丢同批结果 ⑤HTML 页当文件 ⑥`416` 未知尺寸 ⑦`bytes */N` 不解析 ⑧manifest 名字不去重 ⑨`Content-MD5` base64 ⑩头大小写 ⑪校验和不可解析无告警 ⑫重定向带 token ⑬presigned 冲突 ⑭`--header` 广播/argv ⑮无磁盘预检/`fsync` ⑯目录路径崩 ⑰`--rate 0` traceback ⑱`-j`/`--retries` 静默夹取 ⑲`--max-size` 报成 HTTP ⑳`-n` 忽略 `--only` ㉑Usage 占位符 ㉒无 `uvx` ㉓默认目录不可见/脏仓库 ㉔`-h` 缺 4 个 flag ㉕`fetch` 无参无引导 ㉖无 `--json` ㉗无 `-w/--urls` ㉘无 glob ㉙命名竞态/`.part` 名占用 ㉚三次全量哈希 ㉛发布门弱于 PR 门 ㉜无 tag-on-main ㉝action 浮 tag ㉞无 dependabot/CodeQL/pip-audit ㉟无类型门 ㊱真实 SIGINT 未测 ㊲无 `conftest.py` ㊳无 feature 模板 ㊴无 CITATION/CoC ㊵文档只 1 篇审计 ㊶3 处声明反了 ㊷子目录被展平 ㊸无代理/CA 开关 ㊹无 BagIt 导出 ㊺无 snakemake/pooch 集成 ㊻nightly smoke ㊼有界队列 ㊽治理/发布手册。

去重合并（30+ → 21，见下）后按类归位。**类型**列用 `必须/值得/可选`。

| ID | 类 | 类型 | 候选（证据） |
| --- | --- | --- | --- |
| C01 | 性能与安全 | 必须 | 读循环抗停顿：`select()` 轮询代替 socket 超时，停滞不再毒化响应（`downloader.py:110-135`，复现 0.5 s 过 / 1.2 s 挂） |
| C02 | 技术债 | 必须 | 捕获 `HTTPException`/`IncompleteRead`，`future.result()` 加护栏，一批里一个坏文件不吃掉整批结果（`:301`、`:408-409`） |
| C03 | 技术债 | 必须 | `Result` 携带最终落盘名，manifest 不再写 `sanitize` 名（`manifest.py:42` vs `downloader.py:233`，已复现 MISMATCH） |
| C04 | 技术债 | 必须 | 只在字节不可信时删 `.part`（校验失配/偏移不符）；超时、5xx、`--max-size` 保留（`:315-316`） |
| C05 | 性能与安全 | 必须 | 拒收 `text/html` / 魔数不符的体，明确报错而不是落成 `.pdf`（`:168-170`） |
| C06 | 技术债 | 值得 | `416` 必须有尺寸对账，解析 `bytes */N`；远端变小则重下而不是报 size mismatch（`:163-166`、`:92-101`） |
| C07 | 性能与安全 | 值得 | 自定义重定向策略：跨主机剥 `Authorization`、https→http 告警、跳转链入 manifest（`client.py:123-124`） |
| C08 | Quick Win | 值得 | `Content-MD5` 按 base64 解；头部统一大小写不敏感（`client.py:226`、`:208`） |
| C09 | 开发者体验 | 必须 | 首次成功路径：Usage 换成 3 个真实可达目标、Install 前置 `uvx`、`-n` 打印将写入的目录、忽略 `acadfetch-out/`（`README.md:11,64-68`、`cli.py:124`、`.gitignore:10`） |
| C10 | 开发者体验 | 值得 | 错误信息给下一步；`--max-size` 报本地拒绝；`-n` 尊重 `--only`；`-j`/`--retries`/`--rate` 有校验（`cli.py:126-138,143,218-219`） |
| C11 | 自动化 | 值得 | 夜间 live smoke（3 个固定记录，失败开 issue 不红主 CI）+ fixture 漂移告警 |
| C12 | 开发者体验 | 战略 | 互操作三件套：`--json` 机器可读汇总、`-w/--urls` 交给 aria2/rclone、`--glob` |
| C13 | 性能与安全 | 值得 | 校验和缺失/不可解析时显式告警；manifest 记 `verified_reason`（`verify.py:29-32`） |
| C14 | 技术债 | 值得 | 命名去重加锁；`.part`/`.part.meta.json` 名预占；子目录保留（不展平）与长路径处理（`:405-407`、`naming.py:56`） |
| C15 | 性能 | 值得 | 哈希只算一次（传输中流式），manifest 哈希并行；大记录改有界队列（`verify.py:45-47`、`manifest.py:62`、`:405`） |
| C16 | 自动化与治理 | 必须 | 发布门与 PR 门一致（覆盖率门/`-W error`/`twine check`/wheel 断言/`--no-deps` 安装）；tag 必须在 main 上；发布 concurrency；publish action 固定 SHA；发布后冒烟（`release.yml:27,34`） |
| C17 | 技术债 | 值得 | 类型门（mypy 单格必过），清掉 6 处 `noqa: ANN`；否则撤 `Typing :: Typed`（`pyproject.toml:31`） |
| C18 | Quick Win | 值得 | 补 5 个确定性测试：真 SIGINT、`416`、续传失效三连、ENOSPC、失配自愈（含 fallback） |
| C19 | 文档与示例 | 值得 | troubleshooting + recipes（CI/Snakemake/Makefile）+ quickstart；先修 3 处反了的声明（`README.md:33,82,95`） |
| C20 | 社区与治理 | 可选实验 | 信任包：`CITATION.cff`、`CODE_OF_CONDUCT.md`、feature 模板、dependabot、发布手册 |
| C21 | 战略投入 | 可选实验 | 生态位：BagIt/`checksums.txt` 导出、snakemake 存储插件、pooch downloader |

被合并掉的：④→C02、⑦→C06、⑬⑭→C07、⑮⑯→C06/C14、⑰⑱⑲⑳→C10、㉑㉒㉓→C09、㉔㉕→C09/C10、㉖㉗㉘→C12、㉚㊼→C15、㉛㉜㉝㉞→C16、㉟→C17、㊱㊲→C18、㊳㊴㊽→C20、㊵㊶→C19、㊷㊸→C14、㊹㊺→C21、㊻→C11、㊾→C15。

---

## 第 5 步：评分收敛

**口径**（避免只给分数）：影响 5=最大；成本 5=最省；风险 5=最低；差异化 5=最强；可验证性 5=最容易证伪。总分 25。

| # | 方向 | 类型 | 影响 | 成本 | 风险 | 差异化 | 可验证 | 总分 | 一句话理由 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| C01 | 读循环抗停顿 | 必须 | 5 | 4 | 5 | 4 | 5 | **23** | 唯一让 README 首条命令在真实链路能跑完的修复；本地 2 s 停顿就能证伪，改动集中在 25 行内 |
| C03 | manifest 用最终名 | 必须 | 4 | 4 | 5 | 4 | 5 | **22** | 现在"下对了却 verify 报 MISMATCH"，直接摧毁核心产物可信度；两行改动可复现 |
| C04 | 失败保留 `.part` | 必须 | 4 | 5 | 5 | 4 | 4 | **22** | 与 README 承诺对齐的最低成本项；风险几乎为零，但要看住磁盘占用 |
| C02 | `HTTPException` 与结果护栏 | 必须 | 5 | 4 | 5 | 3 | 4 | **21** | 一坏文件今天能掀翻整批并丢 manifest；修的是兜底而非主路径，差异一般但影响高 |
| C09 | 首次成功路径 | 必须 | 4 | 5 | 5 | 3 | 4 | **21** | 纯文档/文案/一个默认值，零代码风险；但依赖 C01 才真正"成功" |
| C05 | 拒收 HTML 错误页 | 必须 | 4 | 4 | 4 | 3 | 5 | **20** | 挡的是"看起来成功其实是登录/拦截页"；魔数表一旦过宽会误杀，所以要短名单 |
| C08 | `Content-MD5` 与头大小写 | 值得 | 3 | 5 | 5 | 3 | 4 | **20** | 让一条已写好的校验路径不再是死代码；半小时内可完成 |
| C06 | `416`/远端缩小/预检簇 | 值得 | 3 | 4 | 5 | 3 | 4 | **19** | 恢复的是"续传语义本身"，折叠了磁盘预检与目录路径崩溃 |
| C07 | 重定向与 token 边界 | 值得 | 4 | 3 | 4 | 5 | 3 | **19** | 唯一"别人抄不走"的安全叙事；但要碰传输细节，回归面比前几项大 |
| C10 | 错误信息与 flag 语义 | 值得 | 4 | 4 | 4 | 3 | 4 | **19** | 把失败变成下一步；并顺手修掉 `-n` 忽略 `--only` 这类"沉默撒谎" |
| C11 | 夜间 live smoke | 值得 | 4 | 3 | 4 | 4 | 4 | **19** | 把外部 API 漂移变成自己的告警；需要接受外部请求与偶发红 |
| C13 | 校验缺口告警 | 值得 | 3 | 4 | 4 | 4 | 4 | **19** | `verified: null` 目前无声；让它带上原因，才谈得上"诚实的降级" |
| C14 | 命名竞态与 `.part` 占用 | 值得 | 3 | 5 | 4 | 2 | 5 | **19** | 已在 40 次压力下未复现，属于潜伏项；加锁 + 预占即可，测试好写 |
| C12 | `--json`/`-w`/glob | 战略 | 4 | 3 | 3 | 5 | 3 | **18** | 决定能否进入流水线；但三种输出面同时加，短期维护负担上升 |
| C16 | 发布门对齐与供应链 | 必须 | 4 | 3 | 4 | 3 | 4 | **18** | 发布门比 PR 门弱是结构性风险；纯 YAML 改动，可被 review 完全验证 |
| C18 | 5 个确定性测试 | 值得 | 4 | 3 | 4 | 3 | 4 | **18** | 现在 SIGINT/416/ENOSPC 全靠人肉；测试本身确定性好，但要先有注入旋钮 |
| C19 | 文档重构与声明纠偏 | 值得 | 4 | 3 | 4 | 3 | 3 | **17** | 3 处反了的声明比缺文档更伤信任；拆分 212 行是体力活 |
| C15 | 哈希一次 + 有界队列 | 值得 | 3 | 3 | 3 | 4 | 4 | **17** | 3 次满盘读是真实的线性浪费；只在几十 GB 以上才痛 |
| C17 | 类型门或撤声明 | 值得 | 3 | 3 | 3 | 3 | 3 | **15** | 二选一都行，**两存最差**；mypy 首跑会有一次集中清理 |
| C20 | 信任包（CoC/CITATION/依赖更新/探针） | 可选 | 2 | 5 | 5 | 2 | 3 | **17** | 便宜且体面；但对"今天能不能用"帮助有限 |
| C21 | 生态位（BagIt/插件） | 可选 | 4 | 2 | 3 | 5 | 3 | **17** | 真正决定"被推荐"；成本与外部维护者依赖最高，放长线 |

**排序**：C01 → C03 → C04 → C02 → C09 为第一批（正确性 + 首次成功）；C05 → C08 → C06 → C07 → C10/C11/C13/C14 为第二批；C12/C16/C18/C19/C15/C17/C20/C21 为第三批。

**Top 10** = C01、C03、C04、C02、C09、C05、C08、C06、C07、C10。

**为什么把 C12/C21 压在 Top 10 外（尽管差异化更高）**：两者的收益都要等"首次成功"成立之后才能兑现。在一条 README 命令会失败的产品上先做 `--json`，等于给一个漏水的桶装龙头。

---

## 第 6 步：Top 5 实验卡

> Top 10 里的 C02/C04 并入卡 1 的验收条件（同一处代码，同一类回归），故出 5 张卡。

### 卡 1 — 传输能扛住停顿（C01 + C02 + C04）
- **假设**：把"1 s socket 超时"换成"`select()` 1 s 轮询 + 长 socket 超时"后，抖动链路上的完成率显著上升，且 Ctrl-C 仍在 1 s 内生效。
- **最小改动**：`_chunks` 先 `select` 再 `read1`；`except` 列表加 `HTTPException`；失败路径保留 `.part`，只在校验失配/偏移不符时删。
- **收益**：用户不再丢 80% 的进度；维护者不再收到"VPN 断了就从头开始"的 issue。
- **验收**：`stall_after=2s` 的本地用例从 FAIL 变 ok；原 206 tests 全绿；一次 2 s 停顿只触发 0 次重试；Ctrl-C 后 `.part` 大小在 1 s 内停止增长。
- **验证方法**：benchmark 脚本（`.tmp/stall2.py` 已就绪）跑 0.5/1.2/2/5 s 四档；再对 Zenodo 真实记录跑 3 次。
- **失败/回滚**：若 `select` 在某些平台返回可读但 `read1` 仍阻塞（Windows/SSL 缓冲），回退到"超时即干净结束并保留 `.part`"，至少不丢数据。
- **工作量**：1 天。

### 卡 2 — manifest 名字与落盘一致（C03）
- **假设**：把最终名放进 `Result` 后，重名记录在 `verify` 下全部 ok。
- **最小改动**：`fetch_file` 把 `naming.unique` 的结果存进 `Result`；`manifest.build` 用它，缺失时回退 `sanitize`。
- **收益**：核心产物重新可信；`verify` 的绿灯重新有意义。
- **验收**：现有 `.tmp/dup_probe.py` 从 1 ok + 1 MISMATCH 变为 2 ok；schema 1 的旧 manifest 仍可读。
- **验证方法**：复现脚本 + 新单测（重名两条都必须 ok）；再手工造一份"含 `a (2).bin` 的旧清单"确认兼容。
- **失败/回滚**：若必须破 schema，则升 schema 2 并保留 1 的读取分支；回滚只需还原两行。
- **工作量**：半天。

### 卡 3 — 错页不再假装成功（C05 + C13）
- **假设**：拒收 `text/html` 与魔数不符的体，能消掉"下到的是登录/拦截页"这一类静默错误。
- **最小改动**：`_transfer` 读 `Content-Type`，命中短名单（`text/html`）或扩展名-魔数不符就报错；`verified: null` 时 manifest 记原因。
- **收益**：用户不会在半年后发现"论文 PDF 是 403 页面"。
- **验收**：新用例（服务器对 `.pdf` 返回 HTML）必须 FAIL 且不留成品；`--retry-403 0` 下 403 仍是终局。
- **验证方法**：本地 fixture + 一次真实 figshare 403 复现；人工走查 `.pdf`/`.tar.gz`/`.csv` 三类魔数白名单是否过窄。
- **失败/回滚**：白名单误杀时退到"只对 `text/html` 生效"；再退到只告警不失败。
- **工作量**：1 天。

### 卡 4 — 校验头不再死代码（C08）
- **假设**：`Content-MD5` 按 base64 解码 + 头名统一小写查表后，直链路径的校验真正生效。
- **最小改动**：`checksum_of` 里对 `Content-MD5` 走 base64→hex；`head()` 返回保留原始头并按大小写不敏感查；`verify.parse` 失败时给出原因而不是静默 `None`。
- **收益**：直链（S3/机构仓）也能被校验；`verified: null` 从"我们没看"变成"源确实没给"。
- **验收**：`Content-MD5` 用例从 `parse → None` 变为可校验；小写 `content-length` 的 HEAD 能取到尺寸。
- **验证方法**：单测（base64 正确/错误各一）；用 `--header` 直连一个带 `Content-MD5` 的公网对象对跑。
- **失败/回滚**：若某源把 hex 塞进 `Content-MD5`，先试 base64 再回退 hex；两侧都失败就只告警。
- **工作量**：半天。

### 卡 5 — 失败会告诉你下一步（C10）
- **假设**：把超时/403/超限映射成带"下一条命令"的人话，能让同一批失败里的人显著更多地重试成功。
- **最小改动**：错误映射表（超时 → `--rate 1 -j 1` 重试且提示 `.part` 会续）；`--max-size` 改本地措辞；`-n` 尊重 `--only`；`-j`/`--retries`/`--rate` 加类型校验，`--rate 0` 不再 traceback。
- **收益**：首次失败不再等于流失；也不会再看到 `HTTP 0 for …`。
- **验收**：五条错误信息人工走查通过（都含"下一步"）；`-n --only nope.pdf` 与真实 fetch 行为一致；`--rate 0` 退出 1 且一句话。
- **验证方法**：人工走查 + 3 个新 CLI 用例；上线后看"同一用户 10 分钟内重跑率"（无埋点则用 issue 抽样）。
- **失败/回滚**：提示文案是纯文本，随时可改；行为变更（`-n` 尊重 `--only`）若有人依赖旧行为，加 `--yes-all-files` 不必。
- **工作量**：半天。

---

## 第 7 步：反向思考

1. **只能做一件事**：卡 1（抗停顿 + 保留 `.part`）。理由是它同时否掉了三件当前最贵的事：README 命令跑不完、进度全丢、以及"续传"这个卖点是假的。
2. **只能删一个功能**：`figshare`（或降级成"实验性"并文档标注）。WAF block-page 判定、`--retry-403`、双端点回退是 `client.py` 里最复杂的三角，而它只换来第三个源；Zenodo + arXiv 已覆盖绝大多数公开数据。删掉后 `client.py` 能瘦一大圈，也少一类"403 到底是不是终局"的判断。
3. **5 分钟内最可能卡在哪**：抄 `README.md:11` 那条命令 → 网络抖一下 ≥1 s → `cannot read from timed out object`、0 字节、无 `.part`、无下一步（本机两次复现）。第二卡点是没装 `pipx`，而 Install 里没有 `uvx`。
4. **每周只有 2 小时该自动化什么**：夜间 live smoke + 依赖/action 版本更新 + fixture 漂移告警。这是唯一能把"我有没有把别人的 API 弄坏"从人肉变自动的杠杆；其余门禁已经够多。
5. **"别人愿意推荐"最缺什么**：一个 30 秒内能看完的成败对照（gif/asciinema）+ 一个能塞进别人既有工作流的接口（Snakemake 存储插件 / pooch downloader / CI recipe）。缺的不是功能数量，是"放进我这条流水线要几行"的答案。

---

## 第 8 步：路线图

### 2 周 —— 让首次成功真的成立
| 目标 | 产出物 | 验收 | 依赖 |
| --- | --- | --- | --- |
| 抖动链路能跑完 | 卡 1 + 卡 1 的两个测试 | 2 s 停顿用例通过；Ctrl-C 1 s 内停；部分失败保留 `.part` | 无 |
| 不再静默下错页 | 卡 3 | HTML 体必须 FAIL；`verified_reason` 入 manifest | 无 |
| 首屏可用 | 卡 5 + README 三条真实目标 + `uvx` 前置 + 默认目录可见 | 三条命令逐条跑通（含 figshare 403 的下一步提示） | 卡 5 |
| 核心产物可信 | 卡 2 | 重名记录 `verify` 全 ok | 无 |
| 发布门不再弱于 PR 门 | `release.yml` 同步 `--cov-fail-under`/`-W error`/`twine check`/wheel 断言/`--no-deps` + tag-on-main + concurrency | 一次演练 tag 全过；故意坏一个 wheel 断言必须红 | 无 |

### 6 周 —— 让"值得推荐"成立
| 目标 | 产出物 | 验收 | 依赖 |
| --- | --- | --- | --- |
| 外部 API 漂移自有告警 | 夜间 smoke（3 固定记录，非阻塞 + 自动开 issue） | 连续 7 天有结果；故意改坏 adapter 能被抓到 | C01 稳定 |
| 校验不再有死角 | 卡 4 + `416`/远端缩小 + 磁盘预检/`fsync` | 直链 `Content-MD5` 可校验；ENOSPC 留 `.part` 且退出码正确 | 无 |
| 能进流水线 | `--json`、`-w/--urls`、`--glob` | 一份 Makefile 与一份 GH Actions 示例端到端跑通 | C03 |
| 可合作 | troubleshooting 一页 + recipes + `docs/invariants.md` + `conftest.py` 故障注入器 | 一个陌生 PR 只靠文档就能加 adapter 并跑绿 | 无 |
| 安全叙事落地 | 重定向剥 token + presigned 不附加 + `--dump-request` | 本地跨主机跳转用例证明头被剥掉 | 无 |

### 3 个月 —— 生态位与治理
| 目标 | 产出物 | 验收 | 依赖 |
| --- | --- | --- | --- |
| 成为上游而非对手 | Snakemake 存储插件 + pooch downloader + 插件目录收录 | 一条 `storage.acadfetch(...)` 规则跑通；pooch 文档页出现 | C12 |
| 与标准互操作 | BagIt/`checksums.txt` 导出 + manifest schema 2 | 导出的 BagIt 目录通过官方校验工具 | C03、schema 决策 |
| 可被引用 | `CITATION.cff` + `--cite` | `--cite` 输出可直接贴进方法节 | 无 |
| 长期可维护 | 类型门 + dependabot + `docs/releasing.md` + 矩阵瘦身到 6 格 | mypy 单格必过；发布手册被一名外部贡献者照着走通 | C16、C17 |
| 治理体面 | `CODE_OF_CONDUCT.md`、feature 模板、维护者期望与暂停条件 | 模板齐全；有人按 CoC 提过一次 | 无 |

### 需要你拍板的 3 个问题
1. **KPI 选哪个**：先把首次成功率与"下对字节"做到无需解释（我的建议），还是先做 interop 换采纳面？
2. **manifest 是否允许到 schema 2**（`resolved_name`/`verified_reason`）？不允许的话卡 2 走"复用 `sanitize` 后置去重"的窄方案。
3. **`figshare` 保还是删**？保则 C07 的安全改造要连 WAF 特判一起重构；删则 2 周内能少掉一整类判断分支。

---

## 附：必须做 / 值得做 / 可选实验 汇总

- **必须做**（不做就等于产品在撒谎）：C01、C02、C03、C04、C05、C09、C16
- **值得做**：C06、C07、C08、C10、C11、C13、C14、C15、C17、C18、C19
- **可选实验**：C12、C20、C21

---

## 附录 B：首次成功率测具（已就位）

2 周档的验收不再是"看着像好了"，而是一张表。装置已建好并跑出基线。

### 已交付

| 文件 | 作用 |
| --- | --- |
| `scripts/first_success.py` | 仪器：干净目录 + 默认参数 + 无 token，单次 600 s 超时，落 CSV，跑完立刻 `verify`。只有 exit 0 **且** verify 0 才算 PASS |
| `scripts/netem_proxy.py` | E2 剖面：CONNECT 代理，限速 + 在首个 body 的 128 KiB 后注入一次 2 s 停顿。跨平台、免管理员、可复现 |
| `.github/workflows/first-success.yml` | E3 剖面：`workflow_dispatch`，ubuntu+windows×3.15 各一格，ubuntu 加一格 3.10；E2 步骤 `continue-on-error`（红是信号，不是坏构建） |

### 剖面定义

| 槽位 | 剖面 | 谁做 | 备注 |
| --- | --- | --- | --- |
| E1 | 本机直连，不注入 | 我 | 控制组 |
| E2 | 1 MiB/s + 2 s 停顿 | 我 | 唯一可复现的回归探针 |
| E3 | Actions runner | 我 | 免费、跨 ASN、钉住 3.15 与 Windows |
| E4 | 手机热点 | 你 | 3 次同一会话 |
| E5 | 学校 VPN | 你 | 先过 4 个校验（见上）；figshare 403 不计环境失败 |

### 基线（修复前，已实测）

| 剖面 | 通过 | 耗时 | `.part` 是否保留 | 失败原因 |
| --- | --- | --- | --- | --- |
| E1-direct | **6/6** | 5.6 / 6.7 / 5.9 s | 0 | — |
| E2-stall | **0/4** | 28–48 s | 0（4 次全丢） | `cannot read from timed out object` |
| E4-hotspot（移动 CGNAT，≈22 KB/s） | **2/3** | 77.1 / **62.5（失败）** / 68.9 s | 0（失败那次也丢） | 同上，1/3 发生率 |

装置有区分度：同一条命令，直连 6/6，单次 2 s 停顿 0/4，真实蜂窝 2/3 且失败那次同样丢进度。

E4 是这轮最有价值的一条：它把缺陷从"本地注入的确定性复现"升级为"真实网络 1/3 发生率"，并顺带证明到 zenodo 的有效速率只有 ~22 KB/s —— >1 s 的空隙是常态，1 秒这个轮询阈值本身选错了。

修复后 E4 必须 **3/3**（不是 2/3），且 `.part` 保留率 100%。

**修复已落地在分支 `fix/transfer-survives-stall`（提交 e209ae6，schema 2 在 `feat/manifest-schema-2`）**，12–15 行产品代码 + 3 条回归测试。本轮验收：

| 剖面 | 修复前 | 修复后 | 连接数（=重试证据） |
| --- | --- | --- | --- |
| E2-stall | 0/4 | **3/3** | 3 次尝试烧 3 次连接，零重试 |
| E1-direct | 6/6 | **3/3** | 3 |
| E4-hotspot（真实蜂窝） | 2/3（1 次 62.5 s 后失败、进度全丢） | **3/3，零失败** | 耗时 77/62/69 s → **8.0/8.4/7.5 s**；CSV 指纹 `93EAF857…24C5` |
| 全套 + 覆盖 | 210 绿 / 89% | **213 绿 / 88.85%** | — |

E4 的耗时下降约 9 倍，不只是"不再失败"，而是重试重启被整个消掉了。

根因不是"超时太短"，是 `socket.SocketIO._timeout_occurred` 一次置位就永久拒绝后续读取（`socket.py:722`），而 Windows 上 `resp.close()` 解不了阻塞的读 —— 所以轮询必须在，闩必须清。

E4 的失败 manifest 还带出两件副产物，都进卡 3：

- E4 收尾时 `git status` 出现未跟踪的 `acadfetch-out/`：默认输出目录落进仓库根，`.gitignore` 只忽略 `out/`（`.gitignore:10`）。这是 C09 的现场旁证，不是我的文件。
- `"bytes": 0`：盘上曾有几百 KB 的 `.part`，manifest 只报成品大小，失败时归零，读者看不出"其实快完了"。修复要报已写入字节 + `part_path`。
- 标题在 PS 5.1 的 `Get-Content` 下乱码（`Alan谋nda`）；需判定是显示层（GBK 解码）还是文件本身，后者则并入编码回归。

### 本机实测到的环境事实（需要记住）

学校解析器对 `zenodo.org` 返回 `0.0.0.0` / NXDOMAIN（其它站点正常），一度让 E1 无法进行。已用 hosts 固定 `188.185.42.113 zenodo.org` 绕过，并由 `netem_proxy.py --resolve host=ip` 覆盖代理侧。**这本身是 E5 类信号**：真实的受限校园网里，DNS 黑洞比 WAF 403 更常见，也是"为什么第一次就失败"的合理解释之一。

### 跑法与收尾

```bash
# E1（本机）
python scripts/first_success.py --env-id E1-direct --csv .tmp/first-success.csv

# E2（另开一个终端先起代理）
python scripts/netem_proxy.py --port 8899 --stall-after 131072 --stall-for 2 --resolve zenodo.org=188.185.42.113
python scripts/first_success.py --env-id E2-stall --proxy http://127.0.0.1:8899 --csv .tmp/first-success.csv
```

E4/E5 照此把 `--env-id` 换成 `E4-hotspot` / `E5-vpn`，多余参数不加。

收尾：E5 结束后 `ipconfig /flushdns`；hosts 里那行带 `# acadfetch first-success bench` 注释，可随时删。

### 门禁

单环境绿 = 3 次 ≥2 次通过且 3 次 verify 全 ok。2 周出口 = **5 环境 ≥4 绿** + E2 的 2 s 停顿用例 100% 通过 + `.part` 保留率 100%。

**E4/E5 逐步操作手册**: [docs/first-success-runbook.md](docs/first-success-runbook.md)
