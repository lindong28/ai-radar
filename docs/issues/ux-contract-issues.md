# UX Contract Issues

> append-only queue. test-ux 跑测中发现的、与 ux-contract / aihot-parity-contract 演化相关的观察。owner sweep 后决定是否升级为契约修订。
>
> 协议：`~/.claude/references/ux-test-protocol.md` §4。
> type 语义：`drift`（契约声 X 实际 Y）/ `expansion`（未覆盖但合理的扩展候选）/ `redesign`（契约结构本身改进建议）。

---

## 2026-05-28 19:20 [expansion] ux-contract 未明确 `/` 和 `/all` 首屏应 SSR 预载且不显示 loading spinner

- Discovered: SSR preload plan production verification for `https://aiplanet.live/` after comparing the existing CSR loading behavior with AIHOT-style inline/preloaded content.
- Description: 当前实现已让 `/`、`/all` 和三个常见 deep link 在生产环境首屏直出 `.item-row`，Playwright gate 结果为 spinner 0、initial API 0，FCP median 均低于 1.5s。但 ux-contract 还没有把"主 feed 首屏应在 HTML/preload 阶段可见，不依赖初始 API fetch，也不出现可感知 loading spinner"作为行为契约写死。
- Recommendation: 在对应 Feed Reading / Initial Load contract 中补充：`/` 与 `/all` 的首屏内容必须通过 SSR preload 或等价机制在 HTML 到达后即可渲染；生产验证以 spinner 出现次数、首个 `.item-row` 时间、initial `/api/v1/*` 请求数为指标。

---

## 2026-05-18 22:30 [drift] aihot-parity-contract §SourceParity-AboutSurfaceReflection 假设 AIHOT 通过 /about 暴露 source pool，实际 AIHOT /about 是个人介绍页 + 公众号 QR

- Discovered: 2026-05-18-r1 / s3-parity-auditor / Layer 1 跑测时对照 AIHOT `/about`
- Description: `aihot-parity-contract.md §SourceParity-AboutSurfaceReflection` 暗含"两端 /about 都暴露 source table"的假设；实测 AIHOT `/about` (`evidence/s3/aihot-about.png`) 是"嗨,我是数字生命卡兹克 / 这个站是我做的,免费给大家用" + 公众号 QR，不暴露任何 source pool。AIHOT 的源池只能从 `/all` / `/curated` 卡片头像 + handle 推断。AI Planet `/about` 暴露 41 行 source table 是设计差异，不算 issue（VISION §6 透明原则），但当前契约措辞会让下游 test-ux 误以为可以两端 `/about` 直接对照。
- Recommendation: 修改 §0 参照锚点表中 `信源池真值` 一栏，对 AIHOT 改为 "公开站点暴露源（卡片头像 + handle，不通过 /about）"；并把 §SourceParity-AboutSurfaceReflection 改为 AI Planet 内部一致性测试（`sources.toml` ↔ `/about table`），不再要求与 AIHOT 对照。

---

## 2026-05-18 22:30 [drift] ux-contract §Feature-DailyNav 与 §Feature-DailySections 在"合法日期 + 无内容"上承诺重叠/冲突

- Discovered: 2026-05-18-r1 / s4-responsive-and-edges / Issue 6（也被 s1-first-time-visitor Issue 2 在 `/daily/1999-01-01` 上独立交叉验证）
- Description: §Feature-DailyNav 边界承诺：「访问 `/daily/<无效或无内容日期>` 时静默切到最近一期，并显示 fallback banner」；§Feature-DailySections 边界承诺：「某日全节皆空时整个 sections 区显示明确空态文案而非白屏」。两条边界在"合法日期格式但无数据"上重叠：当前实现是 `/daily/9999-99-99`（非法格式）走 §Feature-DailyNav fallback banner，`/daily/2000-01-01` 或 `/daily/1999-01-01`（合法格式 + 无数据）走 §Feature-DailySections 空态文案。契约没区分"非法格式 vs 合法 + 无内容"两种情形，导致同样是无内容用户拿到两种不同体验。
- Recommendation: 拆分边界承诺。建议措辞：
  - §Feature-DailyNav 边界："访问 `/daily/<非法日期格式>` 时静默切到最近一期 + fallback banner。"
  - §Feature-DailySections 边界（保留）："某日全节皆空时显式空态文案，不白屏。"
  - 或者反之：合法 + 无内容也走 fallback。两选一并写死。

---

## 2026-05-18 22:30 [drift] ux-contract §Feature-Pagination 措辞"超范围 page 返回空列表"，实现是 clamp 到 max page

- Discovered: 2026-05-18-r1 / s4-responsive-and-edges Issue 5 + Issue 8（s2-returning-power-user Issue 5 也在 `?page=999` 上看到了长 loading 后才发生 clamp）
- Description: §Feature-Pagination 边界："超范围 page 返回空列表，分页器仍可回退；page<1 或非数字按 1 处理。" 实测 `/all?page=999` 经过 ~9s loading 后 URL 被前端改写为 `/all?page=16`（最后一页），渲染该页内容；`/all?category=ai-models&page=2`（超范围因为 ai-models 只 1 页）则 URL 被改写为 `/all?category=ai-models`（直接剥掉 page 参数）。两种行为都不是契约措辞的"返回空列表"。
- Recommendation: 二选一并写死：
  - (a) 实现回到契约："超范围 page = 空列表 + 分页器可回退 + URL 保留"；
  - (b) 契约跟实现："超范围 page = clamp 到 max page，URL 同步改写为 max；带 filter 且总页数 1 时剥掉 page 参数。"
  目前的混合行为让深链复用 / monitoring / 用户预期都不稳定。

---

## 2026-05-18 22:30 [expansion] ux-contract §Feature-CategoryFilter 未明确"无效 slug 静默回退时是否清掉 URL 上的脏参数"

- Discovered: 2026-05-18-r1 / s2-returning-power-user Issue 9（深链 `/?category=invalid-slug` 测试）
- Description: §Feature-CategoryFilter 边界："无效 slug 静默回退到「全部」（不报错）。" 实测 `/?category=invalid-slug` 行为：列表正确渲染全部精选 ≈5s 后地址栏被改写为 `https://aiplanet.live/`（脏参数被剥）。契约没说要清也没说要保留。两种行为各有理由：清 → 防止用户把坏链发出去再次复制；保留 → 让 admin / monitoring 看到误配。
- Recommendation: 在 §Feature-CategoryFilter 边界条目补一句明确，例如：「URL 保留无效参数以便排错」或「URL 清掉无效参数防止扩散」。同理 §Feature-ChannelFilter 也需补；§Feature-Pagination 的 page<1 / 非数字行为同样未说 URL 是否清——可以一并归类为"无效 query 参数的 URL 处理策略"统一段落。
