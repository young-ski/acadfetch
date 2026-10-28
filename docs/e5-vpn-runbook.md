# E5 — 学校 VPN 那一格

E5 现在**不是门禁**（4/5 已由 E1/E2/E3/E4 满足）。它只回答一个问题：换一条**路由**、可能还有一层**代理/MITM**之后，首次成功还成立吗。

只有下面 A、D 两道闸都过了，这一格才算数。不过闸也别硬跑 —— 无效的 E5 比没有 E5 更糟，因为它会被当成"测过了"。

- 用时：15–20 分钟，其中真跑约 1 分钟。
- 不许加 `--proxy`（那是 E2 的活），E5 是纯真实网络。
- 全程 PowerShell。`curl` 一律写 `curl.exe`（5.1 里 `curl` 是 `Invoke-WebRequest` 的别名）。
- 判据每条都要 `Tee-Object -Append` 落盘，最后回贴给我。

先建证据目录，后面每个文件都往里放：

```powershell
$E = Join-Path $env:TEMP "e5-vpn"
New-Item -ItemType Directory -Force $E | Out-Null
$E
```

---

## A0. 先装一个取证书的小函数

在小工具还热的时候先定义它。Windows 的 `curl.exe` 走 schannel，`-v` 不打印证书，所以证书要用 .NET 拿：

```powershell
function Get-PeerCert($hostName, $port = 443) {
  # 裸 TLS 握手最可靠：HttpWebRequest 在 PS7 上不认 ServerCertificateValidationCallback
  $hostName = ($hostName -replace '^https?://', '') -replace '/.*$', ''
  $tcp = [System.Net.Sockets.TcpClient]::new()
  $ssl = $null
  try {
    $tcp.Connect($hostName, $port)
    $ssl = [System.Net.Security.SslStream]::new(
      $tcp.GetStream(), $false, { param($sender, $cert, $chain, $errors) return $true })
    $ssl.AuthenticateAsClient($hostName)
    $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]$ssl.RemoteCertificate
    [pscustomobject]@{
      thumb   = $cert.GetCertHashString()
      issuer  = $cert.Issuer
      subject = $cert.Subject
    }
  } catch {
    [pscustomobject]@{ thumb = "none"; issuer = "none"; subject = "none"; error = $_.Exception.Message }
  } finally {
    if ($ssl) { $ssl.Dispose() }
    $tcp.Dispose()
  }
}
```

## A. 先量基线，未连 VPN

**必须在连接之前做**，否则没有可比对象。

```powershell
Get-Process Radmin -ErrorAction SilentlyContinue | Select-Object Id,ProcessName
Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Sort-Object RouteMetric |
  Select-Object -First 3 ifIndex,NextHop,RouteMetric | Format-Table
curl.exe -sS -m 15 https://ipinfo.io/json | Tee-Object "$E/pre.json"
nslookup zenodo.org | Tee-Object "$E/pre-dns.txt"
Get-PeerCert zenodo.org | ConvertTo-Json | Tee-Object "$E/pre-tls.json"
Get-Content "$E/pre-tls.json"
```

`pre.json` 里要能看见 `"ip"` 和 `"org"`（`org` 形如 `AS12345 Some Network`）。`pre-tls.json` 的 `issuer` 就是现在的 CA —— 抄下这两行的原文。

Radmin VPN 若是开着的先断开：它 metric 9256，和学校隧道抢默认路由没有好处。

## B. 接入

按学校实际办法连（Windows 内置 VPN、学校客户端、或它们给的配置文件）。服务器地址/账号从学校文档取，我不替你猜。

连上后**立刻**确认隧道接管了默认路由：

```powershell
Get-NetAdapter | Where-Object Status -eq "Up" | Select-Object Name,InterfaceIndex,LinkSpeed
Get-NetIPConfiguration |
  Select-Object InterfaceAlias,@{n='gw';e={$_.IPv4DefaultGateway.NextHop}} | Format-Table
Get-NetRoute -DestinationPrefix "0.0.0.0/0" | Sort-Object RouteMetric |
  Select-Object -First 3 ifIndex,NextHop,RouteMetric | Format-Table
```

默认路由的 `ifIndex` 应当指向 VPN 适配器，且 metric 最小。若仍指向 WLAN，见 D 的第一条。

## C. 连上后再量一遍

```powershell
curl.exe -sS -m 15 https://ipinfo.io/json | Tee-Object "$E/post.json"
nslookup zenodo.org | Tee-Object "$E/post-dns.txt"
Get-PeerCert zenodo.org | ConvertTo-Json | Tee-Object "$E/post-tls.json"
Get-Content "$E/post-tls.json"
```

> `ipinfo.io` 走不通就换 `curl.exe -sS -m 15 https://checkip.amazonaws.com`（只有 IP，没有 ASN，`asn_differs` 只能记 `unknown`）。

## D. 四道闸

逐条落盘。**任一为 0 就先别跑**，按表下面的处置走。

```powershell
$pre   = Get-Content "$E/pre.json"     -Raw | ConvertFrom-Json
$post  = Get-Content "$E/post.json"    -Raw | ConvertFrom-Json
$preT  = Get-Content "$E/pre-tls.json" -Raw | ConvertFrom-Json
$postT = Get-Content "$E/post-tls.json" -Raw | ConvertFrom-Json

"egress_changed=" + $(if ($pre.ip -ne $post.ip) {1} else {0}) | Tee-Object "$E/gates.txt" -Append
"asn_differs="    + $(if ($pre.org -ne $post.org) {1} else {0}) | Tee-Object "$E/gates.txt" -Append
"mitm="           + $(if ($preT.issuer -ne $postT.issuer) {1} else {0}) | Tee-Object "$E/gates.txt" -Append
"cert_changed="   + $(if ($preT.thumb -ne $postT.thumb) {1} else {0}) | Tee-Object "$E/gates.txt" -Append
if (Select-String -Path "$E/post-dns.txt" -Pattern "0\.0\.0\.0" -Quiet) { $dns = "blackhole" }
elseif (Select-String -Path "$E/post-dns.txt" -Pattern "188\.185\.42\.113" -Quiet) { $dns = "ok" }
else { $dns = "other" }
"vpn_dns=$dns"     | Tee-Object "$E/gates.txt" -Append
"issuer=$($postT.issuer)" | Tee-Object "$E/gates.txt" -Append
Get-Content "$E/gates.txt"
```

| 闸 | 期望 | 不满足怎么办 |
| --- | --- | --- |
| `egress_changed` | 1 | 隧道没接管：多半是 split tunnel（只路由校内网段）。要么在客户端里开全隧道，要么记为 `e5_invalid=split-tunnel` 收工，别跑 |
| `asn_differs` | 1 | 学校 VPN 出口就在校园网内 → `asn=same`，这一格是"同 ASN 换路径"，仍可跑，但结论要降级 |
| `mitm` | 0 或 1 | 为 1 说明证书换了（校园 CA / 代理）。**照跑**，但标 `mitm=1`：测的是有代理时的行为，不是端到端 TLS |
| `cert_changed` | 任意 | 仅作记录。issuer 不变而指纹变了，通常是换了个 PoP，不算拦截 |
| `vpn_dns` | 任意 | hosts 里那行 pin 还在，DNS 被绕过；记下值即可，`blackhole` 属正常 |

## E. 开跑

同一个会话、三次、不加任何额外参数：

```powershell
python scripts/first_success.py --env-id E5-vpn --attempts 3 --timeout 180 --gap 3 --csv .tmp/e5-vpn.csv --keep
```

## F. 期间别动

- 别重连、别切适配器、别开关防火墙 —— 三次尝试必须落在**同一个**隧道会话上。
- 跑完第三次前，确认隧道还活着：

```powershell
Get-NetAdapter | Where-Object Status -eq "Up" | Select-Object Name,InterfaceIndex
```

## G. 判定

```powershell
$rows   = Import-Csv .tmp/e5-vpn.csv
$pass   = ($rows | Where-Object { $_.exit -eq "0" -and $_.verify -eq "0" }).Count
$failed = ($rows | Where-Object { $_.exit -ne "0" }).Count
$times  = ($rows | ForEach-Object { $_.elapsed_s }) -join '/'
$rows | Format-Table attempt,exit,elapsed_s,bytes,part_kept,verify,error -Auto
"e5=$pass/3 failed=$failed elapsed=$times"
```

**可接受 = 3/3**，且没有任何"先过后来 verify 失败"的行。

| 结果 | 结论 |
| --- | --- |
| 3/3，全 `verify=0` | 通过。回贴耗时三行 |
| 2/3 | 未过。看失败那行的 `error`：`cannot read from timed out object` 说明回归；`403`/`html-page` 说明是 WAF，记 `waf=1` |
| 0–1/3 | 未过。先看 `gates.txt` 是不是闸没关好，再下结论 |

## H. 收尾与留证

无论成败。

```powershell
# 重算一遍，别依赖上一步还留在作用域里的变量
$rows   = Import-Csv .tmp/e5-vpn.csv
$pass   = ($rows | Where-Object { $_.exit -eq "0" -and $_.verify -eq "0" }).Count
$failed = ($rows | Where-Object { $_.exit -ne "0" }).Count
$times  = ($rows | ForEach-Object { $_.elapsed_s }) -join '/'
"e5=$pass/3 failed=$failed elapsed=$times" | Tee-Object "$E/result.txt" -Append
Get-FileHash .tmp/e5-vpn.csv -Algorithm SHA256 | Tee-Object "$E/hash.txt"
Get-Content "$E/gates.txt" | Tee-Object "$E/result.txt" -Append
Copy-Item .tmp/e5-vpn.csv $E/
```

然后断开 VPN，清 DNS，确认工作树：

```powershell
ipconfig /flushdns
git status --short
```

- hosts 里 `188.185.42.113 zenodo.org # acadfetch first-success bench` 这行**留着**，E5 与 E1/E4 的可比性靠它。
- 别删 `.part`（`.tmp/afbench-e5-vpn-*` 下）。
- `git status` 只应看到被忽略的 `.tmp/` 和 `acadfetch-out/`。

## 回贴什么

1. `$E/gates.txt` 五行原文。
2. G 步的表格（或整份 `e5-vpn.csv`）。
3. `hash.txt` 一行。
4. 一句人话：VPN 是哪种（全隧道/分隧道）、有没有弹证书警告、中途有没有掉线。

有这四样，我把它并进 `OPTIMIZATION-PLAN.md` 的剖面表并核指纹；缺哪样都只能记成"未采"。
