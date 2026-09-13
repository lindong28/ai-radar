# ADR-20260913-c7d4：每日 capture 自行推送数据 submodule，只 supersede ADR-060 的授权条款

- Status: accepted（2026-09-13，用户裁决）
- Date: 2026-09-13
- Supersedes: [ADR-060](./060-normalize-and-freeze-aihot-benchmark-manifests-before-v1.md) 的「经显式授权 push」**仅就每日 capture 这一条路径**；该 ADR 的**顺序**要求原样保留

## Context

`scripts/capture_aihot_daily.sh` 每天抓 AIHOT 的滚动窗口，commit 进数据 submodule
`benchmarks/aihot`（→ `lindong28/ai-radar-data` 的 `captures/daily`），**但从不推送**。
`grep push` 在该脚本上返回 0 处；脚本自己的 `KNOWN EXPOSURE` 注释早就写着修法是推送，没人做。

**2026-09-13 实测到它的两种代价，第一种已经发生**：

1. **下游静默停更。** 当天 02:04Z 的 capture 成功了（窗口 09-11..09-12、09-12..09-13），
   日志末行 `No remote push or repository integration was performed.`，`rc=0`。
   所有读 `origin/captures/daily` 的量具因此停在 09-11 的语料上。
   **这条卡了 aihot-fit 这条工作线两天，没有任何信号**——过程见
   [issues/aihot-fit-eval.md](../issues/aihot-fit-eval.md)。
2. **数据可能被删。** 那条 commit 挂在 capture worktree 的 submodule **游离 HEAD** 上，
   无任何分支指向：`git worktree remove` 会连对象一起销毁，而 retention 自己的 14 天钟摆
   也不看有没有推过。AIHOT 只服务 7 天滚动窗，超窗即不可重抓。

阻碍不是技术的，是授权的。ADR-060 第 18 行写着：

> 顺序必须是 data local commit → **经显式授权 push并验证远端 exact ref** → 主仓记录 gitlink；
> **data push、远端配置、主仓 push与主分支整合互不隐含授权。**

一个无人值守的每日自动推，正是这句拒绝的那种**常设隐含授权**。

## Decision

**用户 2026-09-13 裁决：每日 capture 自行推送数据 submodule。**

本 ADR 只改**授权模型**，且只对这一条路径：

| ADR-060 的要求 | 本 ADR |
|---|---|
| 顺序：local commit → push + 验证远端 exact ref → 记 gitlink | **原样保留**。脚本按此顺序执行；push 或验证失败则**不记 gitlink**，避免主仓 pin 一个远端不存在的 SHA |
| 每次 push 需显式授权 | **就本路径改为一次性授权**：本 ADR 即该授权，作用域限 `benchmarks/aihot` → `captures/daily` 这一个 ref |
| 远端配置、主仓 push、主分支整合互不隐含授权 | **原样保留**。本 ADR 不授权其中任何一项 |

**授权不外推**：`origin/main`、`tencent`、其它 submodule、其它分支，一律仍按 ADR-060 与
user-scope 的「Git Push 需显式许可」逐次取得许可。

## Consequences

**要一并处理的两条风险**（2026-09-13 由独立 reviewer 指出，均已实测或静态确认）：

1. **cron 下的挂起。** 本机 `~/.ssh/config` 对 `github.com` 用加密私钥 + `UseKeychain yes`。
   Keychain 一旦锁定或 ACL 需重新确认，ssh 会弹 **GUI** 口令框——那不走 stdin，
   `ConnectTimeout` 管不到，cron 点不掉，会把当天的槽位挂死且日志空白
   （`exec >>"$LOG" 2>&1` 已经把 stdout 吞了）。**两道，且第二道不依赖第一道**：
   - `BatchMode=yes`：把 ssh **自己发起**的交互式询问变成干净失败。
     ⚠️ **它能否拦下 macOS Keychain 那个 GUI 弹窗，本仓未验证**，不要当成已解决。
   - **外层 wall-clock 超时**（`bounded`，默认 300s，`AIHOT_CAPTURE_NET_TIMEOUT` 可调）：
     本机无 `timeout(1)`（实测 `command not found`），故自带一个 bash 3.2 兼容的实现。
     它**不依赖上一条成立**，`push` 与 `ls-remote` 都走它——`ConnectTimeout` 只管 connect 阶段，
     握手后的半开连接、停滞的 DNS、慢链路上的大推送都在它射程之外。
2. **non-fast-forward 不自愈。** 脚本对并发发布无锁（其注释自陈）。一旦本地与远端分叉，
   push 每天以同样理由失败直到人工介入。**本 ADR 不加自动 reconcile**——本仓的既定纪律是
   为**已观察到的**失败加机制，而这条尚未发生；它由既有的 `rc=1` + 飞书告警暴露。

**告警已接上**（同日）：`crontab` 里该条目已套 `run-or-alert --key ai-radar-aihot-capture`，
与 `ai-radar-db-sync` / `ai-radar-cost-report` 同形。在此之前 `rc` 没有任何消费方——
脚本首行 `exec >>"$LOG" 2>&1` 连 cron 的邮件通道都堵死了，这正是上面第 1 种代价能静默两天的原因。

**保留的耐久检查**：即便自动推上线，脚本仍在每次运行结束时检查 submodule HEAD 是否在
某个 remote-tracking ref 上，**包括今天什么都没 stage 的日子**——那正是历史上静默的那几天。
它是 push 失败或被跳过时的第二道读数，不因 push 存在而移除。

**已知未覆盖，写明以免被当成已保障**（2026-09-13，独立 reviewer 两轮后仍在）：

- **SSH 链路零测试覆盖**：全部 59 个用例的 remote 都是文件系统路径，
  `BatchMode` 与 `GIT_SSH_COMMAND` 的实际效果从未被任何一次测试触发。
- **外层超时在测试里从未在真实网络命令上触发**（fixture 的 remote 是文件系统路径，没有慢命令）；
  机制本身有读数：管道场景 0s、退出码透传、`sleep 30` 在 2s 界内被杀 rc=143，
  **以及 case 20——父进程被杀时其子进程也必须终结**（反向变异：拆掉子进程终结 → 60/1 红）。
  case 20 是复核轮报出来的：`bounded` 原本只 TERM 父进程，而 `git push` 会 fork `ssh`，
  于是 job 不再挂、但 `ssh` 可能变成孤儿继续占着那个 Keychain 弹窗。
  **最初的手测用 `sleep 30`——一个没有子进程的叶子命令，结构上看不见这个口子。**
- **pointer commit 失败后次日不重试**：submodule 已推、次日无新内容可 stage 时，
  耐久检查读到"已推送"判 durable、rc 可为 0，而 superproject 的 gitlink 停在更早的 SHA，
  **那一天的 pin 被静默跳过**（数据因祖先关系不丢，可追溯的 pin 链条出现无信号断点）。
  按本仓「为已观察到的失败加机制」的纪律**不修**，记录在此。

**独立 reviewer 对上面两条未覆盖项的严重度判定（2026-09-13，第二轮）**：两条都判**应修**，
且**② SSH/Keychain 那条优先于 ① 超时未在真实慢命令上触发**——理由是两个未验证假设
**顺着同一条因果链摞在一起**（BatchMode 拦不拦得住弹窗 → 拦不住时 `bounded` 能不能真正终结那个
卡住的子进程），任一个不成立都会让「防挂起」这条诉求打折扣。
建议的验证是**人为锁一次 Keychain、手跑一次脚本看实际行为**，而不是长期停在「注释里写着未验证」。
本轮未做（要动本机 Keychain 状态），记在此。

## 🔚 Keychain 那条假设：裁决为不验证，并记下两次失败的尝试

**用户 2026-09-13 裁决：不做侵入式验证，靠外层超时兑现。**

**重新定级的理由**（reviewer 把它列为最高优先，是在**子进程终结落地之前**）：
本轮真正付出代价的故障是「capture 静默两天、cron 槽位被占」这一类，而它已被
`bounded` 外层超时 **+ 子进程终结**关掉，两者都有反向变异覆盖（拆掉子进程终结 → 60/1 红）。
**即便 `BatchMode` 完全无效，job 也不会再挂**——残留风险降为「桌面上可能多一个没人关的口令框」，
不是数据或可用性故障。

### 两次非侵入式实证都是空仪器，记下来省得有人重做

| 尝试 | 读数 | 为什么不算证据 |
|---|---|---|
| ① 造带口令的一次性 key，`-o IdentitiesOnly=yes -o IdentityFile=...` | 两臂**都成功**（3–4s 拿到 refs） | `IdentityFile` 是**累加**的，`-o` 不排除 `~/.ssh/config` 里的条目 ⇒ ssh 走了可用 key，**口令路径从未被触发** |
| ② 加 `-F /dev/null`、清 `SSH_AUTH_SOCK` | 两臂**都在 3s 失败**、输出逐字相同 | `stdin </dev/null` 让**不带 BatchMode 的对照臂也无法提示** ⇒ 对照退化，两臂同形 |

⇒ **构造不出非侵入式的区分性检查**：能让对照臂真正弹出提示的条件（真 tty / GUI askpass），
恰好就是侵入的那部分。第三条路（伪造 `SSH_ASKPASS`）量的是 **askpass 路径**而非 Keychain 路径，
**仍是代理判据**，一并未采。

**所以本档对这条的最终表述是**：`BatchMode=yes` 的作用**未验证**，且**不依赖它**——
防挂起由外层超时与子进程终结承担，那两条有读数、有变异覆盖。

## 🔴 2026-09-14 更正：认证靠的是 ssh-agent，不是 Keychain；已按 ADR-013 补上发现

上面整段把 `BatchMode` 与 Keychain 当成这条路径的认证问题，**方向错了**。
用户重启后要我核 cron 还能不能跑，形态比对给出决定性读数——**同一环境，唯一变量是 `SSH_AUTH_SOCK`**：

| 臂 | 结果 |
|---|---|
| `env -i` + `BatchMode=yes`，**无 agent**（cron 的真实形态） | `git@github.com: Permission denied (publickey)` |
| 完全相同，**只多 `SSH_AUTH_SOCK`** | ✅ 读回 `623728b` |

[ADR-013](./013-db-sync-cron-agent-socket-auth.md)（accepted，2026-08-09）**早就为兄弟 DB-sync cron
解决过同一问题**：cron 无 `SSH_AUTH_SOCK`，`~/.ssh/id_rsa` 带 passphrase，只有登录用户的 agent 认得过，
所以它逐个探测 `/var/run/com.apple.launchd.*/Listeners`。
**它当年还明确否决了 Option B「靠 `UseKeychain yes` 从 keychain 取 passphrase」**，
理由正是「passphrase 是否已入 keychain 未确认」——即本 ADR 先前那段假设，当年就被判为未确认。

⇒ **已在 push 之前补上同样的 agent 发现**（比兄弟简化：不做指纹匹配，取第一个持 key 的 agent；
选错退化为本块已有的 push 失败并告警，而复制一份指纹推导要跟着 `ssh -G` 走）。
`env -i` 下实测：发现到 socket、随后 `ls-remote` 读回 `623728b`。
**继承 ADR-013 Option A 的限制**：没有登录会话就没有 agent，此时它**响亮地**失败（rc=1 → 飞书）。

**`BatchMode=yes` 保留**，但降级为「防交互提示」，不再声称它解决认证；
防挂起仍由外层超时 + 子进程终结承担（那两条有读数、有变异覆盖）。

## 🔴 同日第二条：陈旧的 remote-tracking ref 会天天误报

耐久检查读的是 `git branch -r --contains HEAD`，那是**本地缓存、不是远端**。
实测：capture worktree 的 `origin/captures/daily` 仍是 `137a468`，而远端早已是前一天从**主 checkout**
推上去的 `623728b` ⇒ 该检查会**每天**对着一份其实已耐久的数据报警，而读者分不出真假。

⇒ 已改为：本地 ref 不含 HEAD 时，**先向远端 `ls-remote` 确认**（走同一个 `bounded` 超时）再决定报不报警。
新增用例 21 复现该状态；反向变异（拆掉这一步）→ **62/3 红**。

## 2026-09-14 第三条：外层超时补上了真实网络形态的覆盖

先前这条记作「零覆盖」，理由是 `bounded` 唯一的读数是 `sleep 30`——一个没有 socket 的叶子命令，
**生产里没有任何东西长这个样子**。已补 **case 22**：把 remote 指向不可路由的黑洞地址
`ssh://nobody@10.255.255.1/x`，ssh 会挂满自己的 `ConnectTimeout`（实测 **15s**），
而 push 与随后的耐久读回各挂一次 ⇒ 无界时整轮约 30s。把界设成 3s 后必须远早于此返回、非零、且不记 gitlink。

**反向变异**（把 `bounded` 从 push 上拿掉）：`blackhole: bound fired` 变红，
读数 `unbounded(15s)`，整轮 **30s vs 20s**。

⇒ **「SSH 链路零测试覆盖」这句不再准确**，要按两半分开说：

| 面 | 覆盖状态 |
|---|---|
| ssh 传输 + 外层超时真的开火 | ✅ case 22，带反向变异 |
| **agent 发现** | ✅ case 23，带反向变异（见下一节；本行上一版写的 ❌ 已作废） |
| **`BatchMode` 抑制交互提示** | ❌ 零覆盖，且**已裁决不验证**（见上一节）；防挂起不依赖它 |

## 2026-09-14 第四条：「agent 发现无法自动化」是错的，104 字节而已

上一节把 agent 发现记作「自动化零覆盖」，并给了理由「fixture 造不出 launchd socket」。
**理由只对一半，结论整条错**：造不出的是 `/var/run/com.apple.launchd.*/` 下的 socket（要 root），
而这条路径是不是唯一可行的手段，当时**一次也没查过**——判定依据只有一条
`ssh-agent -a "$scratchpad/Listeners"` 返回 rc=1、socket 没建出来。

真因是 macOS `sockaddr_un` 的 **104 字节**路径上限，scratchpad 路径 118 字符；
换成 `/tmp/agt.XXXX`（13 字符）后 `ssh-agent -a` rc=0、`ssh-add` 加钥成功。
**一个失败的 rc 不说明它为什么失败**，而把它读成「这条路走不通」，
恰好删掉了后面所有该做的检查——这正是反向断言最典型的失效形态。

⇒ 唯一真正挡路的是 glob 写死。已加 `AIHOT_CAPTURE_AGENT_SOCK_GLOB`（默认值即原字面量，
生产行为逐字节不变），**case 23** 起一个真的 `ssh-agent` 冒充 launchd 的：
glob 故意也匹配到密钥文件，于是 `-S` 守卫与 `ssh-add` 探测两条都走到。
两个臂：发现到 socket 且无 WARNING；glob 指向空目录时 WARNING 必须响。

**反向变异**（把发现循环短路成 `if false`）：`agent: discovered the socket` 与 `agent: no warning`
双双变红（**69/2**），而否定臂照常 ok——它测的正是另一个方向。全套 **71 passed**。
