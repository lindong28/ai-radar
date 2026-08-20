# AIHOT 视觉复刻：测得 token 与响应式声明

> Phase 0.3 测得值提取。只记录 2026-08-02 AIHOT 快照中的编译后 CSS；没有修改产品代码。暗色是 `:root,:root[data-theme=dark]` 的默认值，浅色由 `:root[data-theme=light]` 覆盖；未覆盖项在浅色下继承默认值。

## 取值与追溯约定

- 所有声明均来自 `reference/aihot-snapshot-20260802/css/` 下的 5 个 bundle。表中保留编译后拼写，便于用 `rg --fixed-strings` 回查。
- “我方对应”是语义落点，不表示当前值已经相等；写着“新建”的项给出了符合现有 `--bg` / `--panel` / `--ink` 风格的建议名。
- B、C 中的 `var(--x)` 若属于全局 token，直接按 A 表的 light/dark 值解析；日报局部 `--d-*` 另在 B.3 给出二次解析。

## A. Token 映射（133/133）

| AIHOT token | light 值 | dark/默认值 | 我方现有对应或建议 | bundle |
|---|---|---|---|---|
| `--bg-0` | `#f4f5f6` | `#10151c` | 现有 `var(--bg)` | `cb396f1063c803b6.css` |
| `--bg-1` | `#eff1f2` | `#171d26` | 现有 `var(--panel-soft)` | `cb396f1063c803b6.css` |
| `--bg-2` | `#e2e4e7` | `#1b2230` | 新建 `--panel-strong` | `cb396f1063c803b6.css` |
| `--surface-0` | `rgba(28,39,51,0.025)` | `rgba(255,255,255,0.03)` | 现有 `var(--panel-soft)` | `cb396f1063c803b6.css` |
| `--surface-1` | `rgba(28,39,51,0.04)` | `rgba(255,255,255,0.04)` | 新建 `--surface-soft` | `cb396f1063c803b6.css` |
| `--surface-2` | `rgba(28,39,51,0.06)` | `rgba(255,255,255,0.07)` | 新建 `--surface-hover` | `cb396f1063c803b6.css` |
| `--surface-3` | `rgba(28,39,51,0.09)` | `rgba(255,255,255,0.10)` | 新建 `--surface-pressed` | `cb396f1063c803b6.css` |
| `--surface-card` | `#ffffff` | `#171d26` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--surface-card-hover` | `#ffffff` | `#1b2230` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--surface-elevated` | `#ffffff` | `#1b2230` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--text-0` | `#1c2733` | `#e8ebf2` | 现有 `var(--ink)` | `cb396f1063c803b6.css` |
| `--text-1` | `#5c6672` | `#98a2b3` | 现有 `var(--soft)` | `cb396f1063c803b6.css` |
| `--text-2` | `#6b7684` | `#7b869a` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--text-2-on-page` | `#65707e` | `#7b869a` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--text-soft-strong` | `rgba(28,39,51,0.92)` | `rgba(232,235,242,0.85)` | 新建 `--ink-soft-strong` | `cb396f1063c803b6.css` |
| `--text-soft-medium` | `rgba(28,39,51,0.55)` | `rgba(232,235,242,0.55)` | 新建 `--ink-soft-medium` | `cb396f1063c803b6.css` |
| `--border` | `#e2e4e7` | `rgba(255,255,255,0.08)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--border-strong` | `#d8dbdf` | `rgba(255,255,255,0.12)` | 现有 `var(--line-strong)` | `cb396f1063c803b6.css` |
| `--border-soft` | `#eceef0` | `rgba(255,255,255,0.06)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--border-emphasis` | `#8a94a2` | `rgba(255,255,255,0.22)` | 现有 `var(--line-strong)` | `cb396f1063c803b6.css` |
| `--border-card-subtle-solid` | `#c9cdd2` | `rgba(255,255,255,0.14)` | 现有 `var(--line-strong)` | `cb396f1063c803b6.css` |
| `--shadow` | `0 12px 32px rgba(28,39,51,0.12)` | `0 16px 40px rgba(0,0,0,0.45)` | 现有 `var(--shadow-pop)` | `cb396f1063c803b6.css` |
| `--shadow-soft` | `0 4px 12px rgba(28,39,51,0.06)` | `0 6px 18px rgba(0,0,0,0.35)` | 新建 `--shadow-soft` | `cb396f1063c803b6.css` |
| `--shadow-card` | `0 1px 2px rgba(28,39,51,0.05)` | `none` | 现有 `var(--shadow-card)` | `cb396f1063c803b6.css` |
| `--shadow-card-hover` | `0 6px 18px rgba(28,39,51,0.09)` | `none` | 现有 `var(--shadow-pop)` | `cb396f1063c803b6.css` |
| `--shadow-thumb` | `0 1px 3px rgba(28,39,51,0.14)` | `0 1px 3px rgba(0,0,0,0.4)` | 新建 `--shadow-thumb` | `cb396f1063c803b6.css` |
| `--accent-cyan` | `#135e6b` | `#4fa3b3` | 现有 `var(--accent)` | `cb396f1063c803b6.css` |
| `--accent-amber` | `#b8873a` | `#d3b26a` | 现有 `var(--gold)` | `cb396f1063c803b6.css` |
| `--accent-rose` | `#b3402a` | `#d86a52` | 现有 `var(--danger)` | `cb396f1063c803b6.css` |
| `--accent-emerald` | `#2f7d5c` | `#5fc79a` | 新建 `--success` | `cb396f1063c803b6.css` |
| `--accent-cyan-fg` | `#135e6b` | `#6cb8c6` | 现有 `var(--accent-ink)` | `cb396f1063c803b6.css` |
| `--accent-amber-fg` | `#96702e` | `#d3b26a` | 现有 `var(--gold)` | `cb396f1063c803b6.css` |
| `--accent-rose-fg` | `#b3402a` | `#d86a52` | 现有 `var(--danger)` | `cb396f1063c803b6.css` |
| `--accent-emerald-fg` | `#2f7d5c` | `#5fc79a` | 新建 `--success-ink` | `cb396f1063c803b6.css` |
| `--rank-1` | `#b3402a` | `#d86a52` | 现有 `var(--danger)` | `cb396f1063c803b6.css` |
| `--rank-2` | `#c2703f` | `#d18a5e` | 新建 `--rank-second` | `cb396f1063c803b6.css` |
| `--rank-3` | `#b8873a` | `#d3b26a` | 现有 `var(--gold)` | `cb396f1063c803b6.css` |
| `--rank-rest` | `#6b7684` | `#7b869a` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--note-fg` | `#42707c` | `#8fb8a8` | 新建 `--note-ink` | `cb396f1063c803b6.css` |
| `--note-bg` | `color-mix(in srgb,var(--note-fg) 6%,transparent)` | `color-mix(in srgb,var(--note-fg) 10%,transparent)` | 新建 `--note-bg` | `cb396f1063c803b6.css` |
| `--code-block-bg` | `#ffffff` | `#10151c` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--code-block-border` | `#e2e4e7` | `rgba(255,255,255,0.10)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--code-block-ink` | `#1c2733` | `#c9e3ea` | 现有 `var(--ink)` | `cb396f1063c803b6.css` |
| `--code-block-muted` | `#65707e` | `#98a2b3` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--code-syntax-keyword` | `#006d77` | `#78dce8` | 新建 `--code-syntax-keyword` | `cb396f1063c803b6.css` |
| `--code-syntax-string` | `#2f6f44` | `#a9dc76` | 新建 `--code-syntax-string` | `cb396f1063c803b6.css` |
| `--code-syntax-number` | `#9a6700` | `#ffd866` | 新建 `--code-syntax-number` | `cb396f1063c803b6.css` |
| `--code-syntax-title` | `#6f42c1` | `#ab9df2` | 新建 `--code-syntax-title` | `cb396f1063c803b6.css` |
| `--code-syntax-variable` | `#a13d10` | `#fc9867` | 新建 `--code-syntax-variable` | `cb396f1063c803b6.css` |
| `--code-syntax-meta` | `#9c2f5c` | `#ff6188` | 新建 `--code-syntax-meta` | `cb396f1063c803b6.css` |
| `--page-gradient` | `none` | `none` | 新建 `--page-gradient` | `cb396f1063c803b6.css` |
| `--page-bg-solid` | `#f4f5f6` | `#10151c` | 现有 `var(--bg)` | `cb396f1063c803b6.css` |
| `--sidebar-bg` | `#ffffff` | `#0c1117` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--sidebar-border` | `#e2e4e7` | `rgba(255,255,255,0.07)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--radius` | `12px（继承默认）` | `12px` | 新建 `--radius` | `cb396f1063c803b6.css` |
| `--radius-sm` | `8px（继承默认）` | `8px` | 新建 `--radius-sm` | `cb396f1063c803b6.css` |
| `--radius-lg` | `16px（继承默认）` | `16px` | 新建 `--radius-lg` | `cb396f1063c803b6.css` |
| `--space-1` | `4px（继承默认）` | `4px` | 新建 `--space-1` | `cb396f1063c803b6.css` |
| `--space-2` | `8px（继承默认）` | `8px` | 新建 `--space-2` | `cb396f1063c803b6.css` |
| `--space-3` | `12px（继承默认）` | `12px` | 新建 `--space-3` | `cb396f1063c803b6.css` |
| `--space-4` | `16px（继承默认）` | `16px` | 新建 `--space-4` | `cb396f1063c803b6.css` |
| `--space-5` | `24px（继承默认）` | `24px` | 新建 `--space-5` | `cb396f1063c803b6.css` |
| `--space-6` | `32px（继承默认）` | `32px` | 新建 `--space-6` | `cb396f1063c803b6.css` |
| `--container-max` | `520px（继承默认）` | `520px` | 新建 `--container-max` | `cb396f1063c803b6.css` |
| `--container-feed` | `720px（继承默认）` | `720px` | 新建 `--container-feed` | `cb396f1063c803b6.css` |
| `--container-detail` | `720px（继承默认）` | `720px` | 新建 `--container-detail` | `cb396f1063c803b6.css` |
| `--text-size-xs` | `0.75rem（继承默认）` | `0.75rem` | 新建 `--text-size-xs` | `cb396f1063c803b6.css` |
| `--text-size-sm` | `0.8125rem（继承默认）` | `0.8125rem` | 新建 `--text-size-sm` | `cb396f1063c803b6.css` |
| `--text-size-base` | `0.875rem（继承默认）` | `0.875rem` | 新建 `--text-size-base` | `cb396f1063c803b6.css` |
| `--text-size-md` | `1rem（继承默认）` | `1rem` | 新建 `--text-size-md` | `cb396f1063c803b6.css` |
| `--text-size-lg` | `1.125rem（继承默认）` | `1.125rem` | 新建 `--text-size-lg` | `cb396f1063c803b6.css` |
| `--text-size-xl` | `1.25rem（继承默认）` | `1.25rem` | 新建 `--text-size-xl` | `cb396f1063c803b6.css` |
| `--text-size-2xl` | `1.5rem（继承默认）` | `1.5rem` | 新建 `--text-size-2xl` | `cb396f1063c803b6.css` |
| `--line-height-tight` | `1.25（继承默认）` | `1.25` | 新建 `--line-height-tight` | `cb396f1063c803b6.css` |
| `--line-height-normal` | `1.5（继承默认）` | `1.5` | 新建 `--line-height-normal` | `cb396f1063c803b6.css` |
| `--line-height-relaxed` | `1.75（继承默认）` | `1.75` | 新建 `--line-height-relaxed` | `cb396f1063c803b6.css` |
| `--dur-fast` | `120ms（继承默认）` | `120ms` | 新建 `--dur-fast` | `cb396f1063c803b6.css` |
| `--dur-base` | `160ms（继承默认）` | `160ms` | 新建 `--dur-base` | `cb396f1063c803b6.css` |
| `--dur-press` | `80ms（继承默认）` | `80ms` | 新建 `--dur-press` | `cb396f1063c803b6.css` |
| `--dur-enter` | `240ms（继承默认）` | `240ms` | 新建 `--dur-enter` | `cb396f1063c803b6.css` |
| `--nav-width` | `180px（继承默认）` | `180px` | 新建 `--sidebar-width` | `cb396f1063c803b6.css` |
| `--bp-md` | `640px（继承默认）` | `640px` | 新建 `--bp-md` | `cb396f1063c803b6.css` |
| `--bp-lg` | `960px（继承默认）` | `960px` | 新建 `--bp-lg` | `cb396f1063c803b6.css` |
| `--bp-xl` | `1200px（继承默认）` | `1200px` | 新建 `--bp-xl` | `cb396f1063c803b6.css` |
| `--touch-target` | `44px（继承默认）` | `44px` | 新建 `--touch-target` | `cb396f1063c803b6.css` |
| `--touch-target-sm` | `36px（继承默认）` | `36px` | 新建 `--touch-target-sm` | `cb396f1063c803b6.css` |
| `--scrim-overlay` | `rgba(0,0,0,0.5)` | `rgba(0,0,0,0.5)` | 新建 `--scrim` | `cb396f1063c803b6.css` |
| `--theme-accent` | `#135e6b` | `#4fa3b3` | 现有 `var(--accent)` | `cb396f1063c803b6.css` |
| `--theme-accent-rgb` | `19,94,107` | `79,163,179` | 新建 `--accent-rgb` | `cb396f1063c803b6.css` |
| `--theme-accent-hover` | `#0e4a54` | `#6cb8c6` | 现有 `var(--accent-ink)` | `cb396f1063c803b6.css` |
| `--theme-accent-contrast` | `#fcfcfd` | `#10151c` | 新建 `--accent-contrast` | `cb396f1063c803b6.css` |
| `--theme-accent-fg` | `#135e6b` | `#6cb8c6` | 现有 `var(--accent-ink)` | `cb396f1063c803b6.css` |
| `--font-body` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif（继承默认）` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | 现有 `var(--font-sans)` | `cb396f1063c803b6.css` |
| `--font-display` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif（继承默认）` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | 现有 `var(--font-display)` | `cb396f1063c803b6.css` |
| `--font-brand` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif（继承默认）` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | 现有 `var(--font-sans)` | `cb396f1063c803b6.css` |
| `--font-mono` | `ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace（继承默认）` | `ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace` | 现有 `var(--font-mono)` | `cb396f1063c803b6.css` |
| `--m-bg` | `#f4f5f6` | `#10151c` | 现有 `var(--bg)` | `cb396f1063c803b6.css` |
| `--m-surface` | `#ffffff` | `#171d26` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--m-ink` | `#1c2733` | `#e8ebf2` | 现有 `var(--ink)` | `cb396f1063c803b6.css` |
| `--m-ink-2` | `#5c6672` | `#98a2b3` | 现有 `var(--soft)` | `cb396f1063c803b6.css` |
| `--m-ink-25` | `#2b3743` | `#c3cad6` | 现有 `var(--ink-strong)` | `cb396f1063c803b6.css` |
| `--m-ink-3` | `#65707e` | `#7b869a` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--m-brand` | `#135e6b` | `#4fa3b3` | 现有 `var(--accent)` | `cb396f1063c803b6.css` |
| `--m-brand-contrast` | `#fcfcfd` | `#10151c` | 新建 `--accent-contrast` | `cb396f1063c803b6.css` |
| `--m-brand-weak` | `rgba(19,94,107,0.08)` | `rgba(79,163,179,0.12)` | 现有 `var(--accent-soft)` | `cb396f1063c803b6.css` |
| `--m-rank-1` | `#b3402a` | `#c14e36` | 现有 `var(--danger)` | `cb396f1063c803b6.css` |
| `--m-rank-2` | `#a3642f` | `#b45a3a` | 新建 `--mobile-rank-second` | `cb396f1063c803b6.css` |
| `--m-rank-3` | `#96702e` | `#966520` | 现有 `var(--gold)` | `cb396f1063c803b6.css` |
| `--m-rank-rest-bg` | `#eceef0` | `rgba(255,255,255,0.07)` | 新建 `--mobile-rank-rest-bg` | `cb396f1063c803b6.css` |
| `--m-rank-rest-ink` | `#65707e` | `#98a2b3` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--m-border` | `#e2e4e7` | `rgba(255,255,255,0.10)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--m-divider` | `#eceef0` | `rgba(255,255,255,0.06)` | 现有 `var(--line)` | `cb396f1063c803b6.css` |
| `--m-chip-border` | `#d8dbdf` | `rgba(255,255,255,0.14)` | 现有 `var(--line-strong)` | `cb396f1063c803b6.css` |
| `--m-chip-ink` | `#5c6672` | `#98a2b3` | 现有 `var(--soft)` | `cb396f1063c803b6.css` |
| `--m-chip-active-bg` | `#1c2733` | `#e8ebf2` | 现有 `var(--ink)` | `cb396f1063c803b6.css` |
| `--m-chip-active-ink` | `#ffffff` | `#10151c` | 现有 `var(--panel)` | `cb396f1063c803b6.css` |
| `--m-field-bg` | `#eceef0` | `rgba(255,255,255,0.06)` | 现有 `var(--panel-soft)` | `cb396f1063c803b6.css` |
| `--m-daybar-bg` | `#eff1f2` | `rgba(255,255,255,0.04)` | 现有 `var(--panel-soft)` | `cb396f1063c803b6.css` |
| `--m-handle` | `#d3d8dd` | `rgba(255,255,255,0.22)` | 新建 `--mobile-handle` | `cb396f1063c803b6.css` |
| `--m-tab-inactive` | `#65707e` | `#7b869a` | 现有 `var(--muted)` | `cb396f1063c803b6.css` |
| `--m-press` | `rgba(28,39,51,0.06)` | `rgba(255,255,255,0.07)` | 现有 `var(--panel-soft)` | `cb396f1063c803b6.css` |
| `--m-radius-card` | `12px（继承默认）` | `12px` | 新建 `--mobile-card-radius` | `cb396f1063c803b6.css` |
| `--m-radius-btn` | `12px（继承默认）` | `12px` | 新建 `--mobile-button-radius` | `cb396f1063c803b6.css` |
| `--m-shadow-card` | `0 1px 2px rgba(28,39,51,0.05)` | `none` | 现有 `var(--shadow-card)` | `cb396f1063c803b6.css` |
| `--m-tabbar-h` | `54px（继承默认）` | `54px` | 新建 `--mobile-tabbar-height` | `cb396f1063c803b6.css` |
| `--m-gutter` | `18px（继承默认）` | `18px` | 新建 `--mobile-gutter` | `cb396f1063c803b6.css` |
| `--m-poster-stage` | `#10151c（继承默认）` | `#10151c` | 现有 `var(--bg)` | `cb396f1063c803b6.css` |
| `--m-poster-ink` | `rgba(255,255,255,0.92)（继承默认）` | `rgba(255,255,255,0.92)` | 新建 `--poster-ink` | `cb396f1063c803b6.css` |
| `--m-poster-ink-dim` | `rgba(255,255,255,0.55)（继承默认）` | `rgba(255,255,255,0.55)` | 新建 `--poster-ink-muted` | `cb396f1063c803b6.css` |
| `--m-poster-btn-bg` | `rgba(255,255,255,0.10)（继承默认）` | `rgba(255,255,255,0.10)` | 新建 `--poster-button-bg` | `cb396f1063c803b6.css` |
| `--m-poster-accent` | `#135e6b（继承默认）` | `#135e6b` | 现有 `var(--accent)` | `cb396f1063c803b6.css` |
| `--m-poster-shadow` | `0 24px 64px rgba(0,0,0,0.45)（继承默认）` | `0 24px 64px rgba(0,0,0,0.45)` | 新建 `--poster-shadow` | `cb396f1063c803b6.css` |
| `--theme-transition` | `background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` | `background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` | 新建 `--theme-transition` | `cb396f1063c803b6.css` |

覆盖计数：默认/暗色块 132 个 token，独立 `:root` 的 `--theme-transition` 1 个；浅色显式覆盖 86 个，其余逐项写出继承后的实际值。

## B. 组件几何值

本节记录编译 CSS 中真正下发的声明，而不是源码意图。表按 bundle 内源码顺序排列；同一 selector/property 若重复，后行覆盖前行。媒体查询内覆盖不在这里重复，统一列于 C；因此“base”代表宽屏基础态。

### B.0 局部变量的最终解析

| 局部 token | >960px | 641–960px | ≤640px | 来源 |
|---|---|---|---|---|
| `--tl-time-w` | `64px` | `64px`（隐藏 `.feed-desktop`，不渲染） | `44px`（隐藏 `.feed-desktop`，不渲染） | `0e23a4c20d977d43.css` |
| `--tl-rail-w` | `22px` | `22px`（隐藏 `.feed-desktop`，不渲染） | `16px`（隐藏 `.feed-desktop`，不渲染） | `0e23a4c20d977d43.css` |
| `--tl-dot-top` | `20px` | `16px`（隐藏 `.feed-desktop`，不渲染） | `20px`（≤640 后置规则覆盖 ≤960 值；隐藏 `.feed-desktop`，不渲染） | `0e23a4c20d977d43.css` |
| `--tl-accent` | 普通/精选 `var(--accent-cyan)` → L `#135e6b` / D `#4fa3b3`；收藏 `var(--accent-amber)` → L `#b8873a` / D `#d3b26a` | 同左 | 同左 | `0e23a4c20d977d43.css` |

### B.1 `.chip`、segmented、card 与 timeline（base 编译声明）

| bundle | selector（编译后、源码顺序） | 实际声明 | `var()` 解析 |
|---|---|---|---|
| `0e23a4c20d977d43.css` | `.card,.panel,.surface` | `background: var(--surface-card); border: 1px solid var(--border); border-radius: var(--radius); box-shadow: var(--shadow-card); transition: var(--theme-transition)` | `--surface-card: L=#ffffff / D=#171d26`<br>`--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--radius: L=12px / D=12px`<br>`--shadow-card: L=0 1px 2px rgba(28,39,51,0.05) / D=none`<br>`--theme-transition: L=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease / D=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` |
| `0e23a4c20d977d43.css` | `.chip` | `display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; border: 1px solid var(--border-strong); background: var(--surface-card); color: var(--text-1); font-size: 12px; line-height: 1; white-space: nowrap; transition: var(--theme-transition)` | `--border-strong: L=#d8dbdf / D=rgba(255,255,255,0.12)`<br>`--surface-card: L=#ffffff / D=#171d26`<br>`--text-1: L=#5c6672 / D=#98a2b3`<br>`--theme-transition: L=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease / D=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` |
| `0e23a4c20d977d43.css` | `.segmented` | `gap: 22px; padding: 0; border: 0; border-bottom: 1px solid var(--border-soft); background: none; box-shadow: none; transition: var(--theme-transition)` | `--border-soft: L=#eceef0 / D=rgba(255,255,255,0.06)`<br>`--theme-transition: L=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease / D=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` |
| `0e23a4c20d977d43.css` | `.seg-item,.segmented` | `display: inline-flex; align-items: center; border-radius: 0` | — |
| `0e23a4c20d977d43.css` | `.seg-item` | `justify-content: center; min-width: 0; padding: 7px 1px 9px; color: var(--text-1); font-size: 13px; line-height: 1; font-family: var(--font-body); letter-spacing: 0; text-align: center; text-decoration: none; transition: color var(--dur-fast) ease,box-shadow var(--dur-fast) ease` | `--text-1: L=#5c6672 / D=#98a2b3`<br>`--font-body: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--dur-fast: L=120ms / D=120ms` |
| `0e23a4c20d977d43.css` | `.seg-item:hover` | `background: none; color: var(--text-0)` | `--text-0: L=#1c2733 / D=#e8ebf2` |
| `0e23a4c20d977d43.css` | `.seg-item-active,.seg-item-active:hover` | `background: none; color: var(--accent-cyan-fg); font-weight: 600; box-shadow: inset 0 -2px 0 var(--accent-cyan)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6`<br>`--accent-cyan: L=#135e6b / D=#4fa3b3` |
| `0e23a4c20d977d43.css` | `.timeline` | `--tl-time-w: 64px; --tl-rail-w: 22px; --tl-dot-top: 20px; display: grid; gap: 22px` | — |
| `0e23a4c20d977d43.css` | `.timeline-item` | `--tl-accent: var(--accent-cyan); display: grid; grid-template-columns: var(--tl-time-w) var(--tl-rail-w) 1fr; gap: 0; align-items: start; padding-bottom: 12px` | `--accent-cyan: L=#135e6b / D=#4fa3b3`<br>`--tl-time-w: 见 B.0 时间线局部变量`<br>`--tl-rail-w: 见 B.0 时间线局部变量` |
| `0e23a4c20d977d43.css` | `.timeline-item:last-child` | `padding-bottom: 0` | — |
| `0e23a4c20d977d43.css` | `.timeline-item-selected` | `--tl-accent: var(--accent-cyan)` | `--accent-cyan: L=#135e6b / D=#4fa3b3` |
| `0e23a4c20d977d43.css` | `.timeline-item-starred` | `--tl-accent: var(--accent-amber)` | `--accent-amber: L=#b8873a / D=#d3b26a` |
| `0e23a4c20d977d43.css` | `.timeline-rail` | `position: relative; min-height: 100%` | — |
| `0e23a4c20d977d43.css` | `.timeline-rail:before` | `content: none` | — |
| `0e23a4c20d977d43.css` | `.timeline-dot` | `position: absolute; left: 50%; top: var(--tl-dot-top); width: 7px; height: 7px; border-radius: 999px; transform: translateX(-50%); background: var(--tl-accent); box-shadow: 0 0 0 3px var(--bg-0)` | `--tl-dot-top: 见 B.0 时间线局部变量`<br>`--tl-accent: 见 B.0 时间线局部变量`<br>`--bg-0: L=#f4f5f6 / D=#10151c` |
| `0e23a4c20d977d43.css` | `.timeline-card` | `min-width: 0; padding: 15px 18px 14px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--surface-card); box-shadow: var(--shadow-card); transition: border-color var(--dur-base) ease,background var(--dur-base) ease,box-shadow var(--dur-base) ease,transform var(--dur-base) ease; cursor: pointer` | `--radius: L=12px / D=12px`<br>`--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--surface-card: L=#ffffff / D=#171d26`<br>`--shadow-card: L=0 1px 2px rgba(28,39,51,0.05) / D=none`<br>`--dur-base: L=160ms / D=160ms` |
| `0e23a4c20d977d43.css` | `.timeline-card:hover` | `border-color: var(--border-card-subtle-solid); background: var(--surface-card-hover); box-shadow: var(--shadow-card-hover); transform: translateY(-1px)` | `--border-card-subtle-solid: L=#c9cdd2 / D=rgba(255,255,255,0.14)`<br>`--surface-card-hover: L=#ffffff / D=#1b2230`<br>`--shadow-card-hover: L=0 6px 18px rgba(28,39,51,0.09) / D=none` |
| `0e23a4c20d977d43.css` | `.timeline-card-head` | `display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px` | — |
| `0e23a4c20d977d43.css` | `.timeline-score` | `display: inline-flex; align-items: center; gap: 4px; font-family: var(--font-mono); font-size: 12px; font-weight: 600; line-height: 1; padding: 0; border: 0; background: none; letter-spacing: 0; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace` |
| `0e23a4c20d977d43.css` | `.timeline-score:before` | `content: ""; width: 5px; height: 5px; border-radius: 999px; background: currentColor` | — |
| `0e23a4c20d977d43.css` | `.timeline-score.score-high` | `color: var(--accent-emerald-fg)` | `--accent-emerald-fg: L=#2f7d5c / D=#5fc79a` |
| `0e23a4c20d977d43.css` | `.timeline-score.score-mid` | `color: var(--accent-cyan-fg)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `0e23a4c20d977d43.css` | `.timeline-score.score-muted` | `color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.timeline-score.score-pending` | `color: var(--text-2); font-weight: 500` | `--text-2: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.timeline-score.score-pending:before` | `content: none` | — |
| `0e23a4c20d977d43.css` | `.timeline-selected-badge` | `display: inline-flex; align-items: center; gap: 3px; font-size: 10.5px; font-weight: 600; line-height: 1; padding: 3px 7px; border-radius: 3px; letter-spacing: .04em; color: var(--accent-amber-fg); background: color-mix(in srgb,var(--accent-amber) 12%,transparent); border: 0; box-shadow: none; text-shadow: none; -webkit-user-select: none; user-select: none; font-variant-east-asian: proportional-width` | `--accent-amber-fg: L=#96702e / D=#d3b26a`<br>`--accent-amber: L=#b8873a / D=#d3b26a` |
| `0e23a4c20d977d43.css` | `.timeline-selected-badge:before` | `content: "\2726"; font-size: 9.5px; font-weight: 400; color: currentColor; display: inline-block; transform: translateY(-.5px); filter: none` | — |
| `0e23a4c20d977d43.css` | `.timeline-divider` | `border: none; border-top: 1px dashed var(--border-strong); margin: 10px 0 0` | `--border-strong: L=#d8dbdf / D=rgba(255,255,255,0.12)` |
| `0e23a4c20d977d43.css` | `.timeline-star` | `appearance: none; position: relative; border: 1px solid transparent; background: transparent; color: var(--text-2); padding: 4px; border-radius: var(--radius-sm); cursor: pointer; line-height: 0; opacity: .55; transition: opacity .12s ease,background .12s ease,border-color .12s ease,color .12s ease,transform 80ms ease` | `--text-2: L=#6b7684 / D=#7b869a`<br>`--radius-sm: L=8px / D=8px` |
| `0e23a4c20d977d43.css` | `.timeline-card:hover .timeline-star` | `opacity: .92` | — |
| `0e23a4c20d977d43.css` | `.timeline-star:hover` | `background: var(--surface-1); border-color: var(--border-strong); color: var(--text-0)` | `--surface-1: L=rgba(28,39,51,0.04) / D=rgba(255,255,255,0.04)`<br>`--border-strong: L=#d8dbdf / D=rgba(255,255,255,0.12)`<br>`--text-0: L=#1c2733 / D=#e8ebf2` |
| `0e23a4c20d977d43.css` | `.timeline-star:active` | `transform: scale(.96)` | — |
| `0e23a4c20d977d43.css` | `.timeline-star.is-starred` | `opacity: 1; color: var(--accent-rose-fg)` | `--accent-rose-fg: L=#b3402a / D=#d86a52` |
| `0e23a4c20d977d43.css` | `.timeline-star svg` | `width: 16px; height: 16px` | — |
| `0e23a4c20d977d43.css` | `.local-starred-marks .timeline-score,.local-starred-marks .timeline-selected-badge` | `flex-shrink: 0` | — |
| `0e23a4c20d977d43.css` | `.page-header-feed.page-header-compact .segmented` | `padding: 0; gap: 18px` | — |
| `0e23a4c20d977d43.css` | `.page-header-feed.page-header-compact .seg-item` | `min-width: 0; padding: 7px 1px 9px` | — |
| `0e23a4c20d977d43.css` | `.src-detail-chips .chip` | `font-size: 11px` | — |
| `0e23a4c20d977d43.css` | `.timeline-card:hover .fc-read .timeline-title,.timeline-card:hover .fc-read .uc-body` | `opacity: .85` | — |

本表 39 条编译规则。关键 cascade：`.segmented` 同时吃到 `.seg-item,.segmented` 的 `display:inline-flex;align-items:center;border-radius:0`；`.seg-item-active` 保留 `.seg-item` 的全部几何，再以 `color/font-weight/box-shadow` 覆盖。`.timeline-dot` 的 `top` 和 `background` 分别由上表 `--tl-dot-top`、`--tl-accent` 决定。

### B.2 热点、移动 feed、chip 与底部 tab（base 编译声明）

| bundle | selector（编译后、源码顺序） | 实际声明 | `var()` 解析 |
|---|---|---|---|
| `0e23a4c20d977d43.css` | `.hot-topics-head` | `display: flex; align-items: center; gap: 10px; margin-bottom: 0; padding: 11px 16px; border-bottom: 1px solid var(--border-soft)` | `--border-soft: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `0e23a4c20d977d43.css` | `.hot-topics-flame` | `display: none` | — |
| `0e23a4c20d977d43.css` | `.hot-topics-title` | `font-family: var(--font-body); font-size: 15px; font-weight: 800; letter-spacing: .04em; color: var(--text-0)` | `--font-body: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--text-0: L=#1c2733 / D=#e8ebf2` |
| `0e23a4c20d977d43.css` | `.hot-topics-more` | `margin-left: auto; font-weight: 600; color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.hot-topics-more:hover` | `color: var(--accent-cyan-fg)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `0e23a4c20d977d43.css` | `.hot-topics-hint` | `margin-left: auto; font-size: var(--text-size-xs); color: var(--text-2)` | `--text-size-xs: L=0.75rem / D=0.75rem`<br>`--text-2: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.hot-topics-more` | `flex: none; color: var(--accent-cyan-fg); font-size: var(--text-size-xs); font-weight: 700; text-decoration: none` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6`<br>`--text-size-xs: L=0.75rem / D=0.75rem` |
| `0e23a4c20d977d43.css` | `.hot-topics-more:hover` | `text-decoration: underline` | — |
| `0e23a4c20d977d43.css` | `.hot-topics-list` | `list-style: none; margin: 0; padding: 4px 0 6px; display: grid` | — |
| `0e23a4c20d977d43.css` | `.hot-topics-row` | `display: flex; align-items: center; gap: 12px; padding: 8px 16px; min-width: 0; transition: background .14s ease` | — |
| `0e23a4c20d977d43.css` | `.hot-topics-row:hover` | `background: var(--surface-0)` | `--surface-0: L=rgba(28,39,51,0.025) / D=rgba(255,255,255,0.03)` |
| `0e23a4c20d977d43.css` | `.hot-topics-row:last-child` | `border-radius: 0 0 calc(var(--radius) - 1px) calc(var(--radius) - 1px)` | `--radius: L=12px / D=12px` |
| `0e23a4c20d977d43.css` | `.hot-topics-rank` | `flex: none; width: 20px; text-align: center; font-family: var(--font-body); font-size: 14px; font-weight: 700; line-height: 1; color: var(--rank-rest)` | `--font-body: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--rank-rest: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.hot-topics-rank-1` | `color: var(--rank-1); font-size: 15px; font-weight: 900` | `--rank-1: L=#b3402a / D=#d86a52` |
| `0e23a4c20d977d43.css` | `.hot-topics-rank-2` | `color: var(--rank-2); font-size: 15px; font-weight: 900` | `--rank-2: L=#c2703f / D=#d18a5e` |
| `0e23a4c20d977d43.css` | `.hot-topics-rank-3` | `color: var(--rank-3); font-size: 15px; font-weight: 900` | `--rank-3: L=#b8873a / D=#d3b26a` |
| `0e23a4c20d977d43.css` | `.hot-topics-link` | `flex: 1 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 600; line-height: 1.5; color: var(--text-0); text-decoration: none; transition: color var(--dur-fast) ease` | `--text-0: L=#1c2733 / D=#e8ebf2`<br>`--dur-fast: L=120ms / D=120ms` |
| `0e23a4c20d977d43.css` | `.hot-topics-link:hover` | `color: var(--accent-cyan-fg)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `0e23a4c20d977d43.css` | `.hot-topics-meta` | `flex: none; display: inline-flex; align-items: center; gap: 6px; font-size: var(--text-size-sm); font-weight: 600; color: var(--text-2); white-space: nowrap; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | `--text-size-sm: L=0.8125rem / D=0.8125rem`<br>`--text-2: L=#6b7684 / D=#7b869a` |
| `0e23a4c20d977d43.css` | `.dup-tooltip.hot-topics-tooltip` | `left: auto; right: 0; top: calc(100% + 6px); bottom: auto; z-index: 60; width: max-content; max-width: min(420px,calc(100vw - 32px)); max-height: min(60vh,420px); overflow-y: auto; white-space: normal` | — |
| `0e23a4c20d977d43.css` | `.dup-tooltip.hot-topics-tooltip .dup-tooltip-item` | `white-space: normal; overflow-wrap: anywhere` | — |
| `0e23a4c20d977d43.css` | `.hot-topics-index:after` | `content: ""; position: absolute; top: 100%; left: 0; right: 0; height: 8px` | — |
| `9b65374e8a4754c4.css` | `.m-daily,.m-detail,.m-feed,.m-tabbar` | `display: none` | — |
| `9b65374e8a4754c4.css` | `.m-all-types-summary:focus-visible,.m-chip:focus-visible,.m-chips-search:focus-visible,.m-daily-entry-title:focus-visible,.m-daily-nav a:focus-visible,.m-detail-back:focus-visible,.m-detail-bar-ext:focus-visible,.m-detail-exportbtn:focus-visible,.m-detail-lang-opt:focus-visible,.m-detail-readbtn:focus-visible,.m-detail-related-row:focus-visible,.m-detail-save.timeline-star:focus-visible,.m-detail-share:focus-visible,.m-detail-sharebtn:focus-visible,.m-detail-summary-head:focus-visible,.m-filter-note-clear:focus-visible,.m-hotcard-link:focus-visible,.m-more-row:focus-visible,.m-nojs-next:focus-visible,.m-poster-close:focus-visible,.m-poster-save:focus-visible,.m-poster-share:focus-visible,.m-row:focus-visible,.m-search-cancel:focus-visible,.m-search-submit:focus-visible,.m-sentinel-retry:focus-visible,.m-tab:focus-visible,.m-xcard:focus-visible` | `outline: 2px solid var(--m-brand); outline-offset: 2px; border-radius: var(--m-radius-btn)` | `--m-brand: L=#135e6b / D=#4fa3b3`<br>`--m-radius-btn: L=12px / D=12px` |
| `9b65374e8a4754c4.css` | `.m-hotcard` | `background: var(--m-surface); border: 1px solid var(--m-border); border-radius: var(--m-radius-card); box-shadow: var(--m-shadow-card); padding: 13px 16px 4px; transition: var(--theme-transition)` | `--m-surface: L=#ffffff / D=#171d26`<br>`--m-border: L=#e2e4e7 / D=rgba(255,255,255,0.10)`<br>`--m-radius-card: L=12px / D=12px`<br>`--m-shadow-card: L=0 1px 2px rgba(28,39,51,0.05) / D=none`<br>`--theme-transition: L=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease / D=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` |
| `9b65374e8a4754c4.css` | `.m-hotcard-head` | `display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px` | — |
| `9b65374e8a4754c4.css` | `.m-hotcard-title` | `font-family: var(--font-brand); font-size: 17px; font-weight: 900; color: var(--m-ink)` | `--font-brand: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--m-ink: L=#1c2733 / D=#e8ebf2` |
| `9b65374e8a4754c4.css` | `.m-hotcard-top5` | `font-size: 11px; letter-spacing: 1px` | — |
| `9b65374e8a4754c4.css` | `.m-hotcard-more,.m-hotcard-top5` | `color: var(--m-brand); text-decoration: none; font-weight: 700` | `--m-brand: L=#135e6b / D=#4fa3b3` |
| `9b65374e8a4754c4.css` | `.m-hotcard-more` | `display: inline-flex; align-items: center; min-height: var(--touch-target); font-size: 12px` | `--touch-target: L=44px / D=44px` |
| `9b65374e8a4754c4.css` | `.m-hotcard-list` | `list-style: none; margin: 0; padding: 0` | — |
| `9b65374e8a4754c4.css` | `.m-hotcard-row` | `display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--m-divider)` | `--m-divider: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `9b65374e8a4754c4.css` | `.m-hotcard-row:last-child` | `border-bottom: 0` | — |
| `9b65374e8a4754c4.css` | `.m-hotcard-rank` | `width: 24px; height: 24px; border-radius: 7px; display: flex; align-items: center; justify-content: center; flex-shrink: 0; font-family: var(--font-brand); font-size: 14px; font-weight: 900; background: var(--m-rank-rest-bg); color: var(--m-rank-rest-ink)` | `--font-brand: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--m-rank-rest-bg: L=#eceef0 / D=rgba(255,255,255,0.07)`<br>`--m-rank-rest-ink: L=#65707e / D=#98a2b3` |
| `9b65374e8a4754c4.css` | `.m-hotcard-rank-1` | `background: var(--m-rank-1); color: #ffffff` | `--m-rank-1: L=#b3402a / D=#c14e36` |
| `9b65374e8a4754c4.css` | `.m-hotcard-rank-2` | `background: var(--m-rank-2); color: #ffffff` | `--m-rank-2: L=#a3642f / D=#b45a3a` |
| `9b65374e8a4754c4.css` | `.m-hotcard-rank-3` | `background: var(--m-rank-3); color: #ffffff` | `--m-rank-3: L=#96702e / D=#966520` |
| `9b65374e8a4754c4.css` | `.m-hotcard-link` | `flex: 1 1; min-width: 0; color: var(--m-ink); text-decoration: none; line-height: 1.42; font-size: 14px; font-weight: 600` | `--m-ink: L=#1c2733 / D=#e8ebf2` |
| `9b65374e8a4754c4.css` | `.m-hotcard-link-4` | `color: var(--m-ink-25)` | `--m-ink-25: L=#2b3743 / D=#c3cad6` |
| `9b65374e8a4754c4.css` | `.m-hotcard-index` | `flex-shrink: 0; padding-top: 3px; font-size: 11.5px; color: var(--m-ink-3); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | `--m-ink-3: L=#65707e / D=#7b869a` |
| `9b65374e8a4754c4.css` | `.m-rows` | `display: block` | — |
| `9b65374e8a4754c4.css` | `.m-row-wrap` | `display: grid; grid-template-columns: minmax(0,1fr); border-bottom: 1px solid var(--m-border); content-visibility: auto; contain-intrinsic-size: auto 120px` | `--m-border: L=#e2e4e7 / D=rgba(255,255,255,0.10)` |
| `9b65374e8a4754c4.css` | `.m-row` | `display: flex; flex-direction: column; gap: 6px; width: 100%; padding: 11px 0; border: 0; background: none; text-align: left; text-decoration: none; color: inherit; cursor: pointer; font-family: var(--font-body); touch-action: manipulation; -webkit-tap-highlight-color: transparent; min-width: 0` | `--font-body: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `9b65374e8a4754c4.css` | `.m-row-title` | `font-size: 15px; font-weight: 600; color: var(--m-ink); line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere` | `--m-ink: L=#1c2733 / D=#e8ebf2` |
| `9b65374e8a4754c4.css` | `.m-row-meta` | `display: flex; align-items: center; justify-content: space-between; width: 100%; gap: 12px` | — |
| `9b65374e8a4754c4.css` | `.m-row-src` | `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap` | — |
| `9b65374e8a4754c4.css` | `.m-row-src,.m-score` | `font-size: 12px; color: var(--m-ink-3)` | `--m-ink-3: L=#65707e / D=#7b869a` |
| `9b65374e8a4754c4.css` | `.m-score` | `font-family: var(--font-mono); font-weight: 600; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; flex-shrink: 0` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace` |
| `9b65374e8a4754c4.css` | `.m-score-mid` | `color: var(--m-brand)` | `--m-brand: L=#135e6b / D=#4fa3b3` |
| `9b65374e8a4754c4.css` | `.m-score-high` | `color: var(--accent-emerald-fg)` | `--accent-emerald-fg: L=#2f7d5c / D=#5fc79a` |
| `9b65374e8a4754c4.css` | `.m-row-wrap.fc-read .m-row-title,.m-xcard-wrap.fc-read .m-xcard-text,.m-xcard-wrap.fc-read .m-xcard-trans` | `opacity: .55` | — |
| `9b65374e8a4754c4.css` | `.m-row-all` | `flex-direction: row; align-items: flex-start; gap: 12px; padding: 14px 0` | — |
| `9b65374e8a4754c4.css` | `.m-row-time` | `width: 40px; flex-shrink: 0; font-family: var(--font-mono); font-size: 12px; color: var(--m-ink-3)`（≤960 可见目标；live 字形宽 `36.1px`、字重 `400`、行高 `18px`、左对齐） | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--m-ink-3: L=#65707e / D=#7b869a` |
| `9b65374e8a4754c4.css` | `.m-row-body` | `flex: 1 1; min-width: 0; display: flex; flex-direction: column; gap: 6px` | — |
| `9b65374e8a4754c4.css` | `.m-row-all .m-row-title` | `font-size: 16px` | — |
| `9b65374e8a4754c4.css` | `.m-row-all .m-row-src` | `font-size: 12.5px` | — |
| `9b65374e8a4754c4.css` | `.m-row-summary` | `font-size: 13.5px; line-height: 1.55; color: var(--m-ink-2); display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere` | `--m-ink-2: L=#5c6672 / D=#98a2b3` |
| `9b65374e8a4754c4.css` | `.m-row-reason-block` | `display: block; background: var(--note-bg); border-radius: var(--radius-sm); padding: 8px 10px` | `--note-bg: L=color-mix(in srgb,var(--note-fg) 6%,transparent) / D=color-mix(in srgb,var(--note-fg) 10%,transparent)`<br>`--radius-sm: L=8px / D=8px` |
| `9b65374e8a4754c4.css` | `.m-row-reason-clamp` | `display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; overflow-wrap: anywhere; font-size: 12.5px; line-height: 1.55; color: var(--note-fg)` | `--note-fg: L=#42707c / D=#8fb8a8` |
| `9b65374e8a4754c4.css` | `.m-row-reason-label` | `font-weight: 700; color: var(--note-fg)` | `--note-fg: L=#42707c / D=#8fb8a8` |
| `9b65374e8a4754c4.css` | `.m-row-linked` | `display: block; margin-top: 3px; color: var(--m-brand); font-size: 11.5px; line-height: 1.3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis` | `--m-brand: L=#135e6b / D=#4fa3b3` |
| `9b65374e8a4754c4.css` | `.m-chips-row` | `display: flex; align-items: flex-start; gap: 8px` | — |
| `9b65374e8a4754c4.css` | `.m-chips-row .m-chips` | `flex: 1 1; min-width: 0` | — |
| `9b65374e8a4754c4.css` | `.m-chips-search` | `flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; min-width: var(--touch-target); min-height: var(--touch-target); color: var(--m-ink-2); text-decoration: none; -webkit-tap-highlight-color: transparent` | `--touch-target: L=44px / D=44px`<br>`--m-ink-2: L=#5c6672 / D=#98a2b3` |
| `9b65374e8a4754c4.css` | `.m-chips-search-icon` | `width: 18px; height: 18px` | — |
| `9b65374e8a4754c4.css` | `.m-chips` | `display: flex; gap: 7px; overflow-x: auto; padding-bottom: 12px; scrollbar-width: none; -webkit-overflow-scrolling: touch` | — |
| `9b65374e8a4754c4.css` | `.m-chips::-webkit-scrollbar` | `display: none` | — |
| `9b65374e8a4754c4.css` | `.m-chips-label` | `align-self: center; flex-shrink: 0; margin-right: 2px; font-size: 11px; font-weight: 700; color: var(--m-ink-3); letter-spacing: .04em` | `--m-ink-3: L=#65707e / D=#7b869a` |
| `9b65374e8a4754c4.css` | `.m-chip` | `flex-shrink: 0; display: inline-flex; align-items: center; min-height: var(--touch-target-sm); padding: 5px 13px; font-size: 12.5px; font-weight: 500; color: var(--m-chip-ink); background: var(--m-surface); border: 1px solid var(--m-chip-border); border-radius: 999px; text-decoration: none; -webkit-tap-highlight-color: transparent` | `--touch-target-sm: L=36px / D=36px`<br>`--m-chip-ink: L=#5c6672 / D=#98a2b3`<br>`--m-surface: L=#ffffff / D=#171d26`<br>`--m-chip-border: L=#d8dbdf / D=rgba(255,255,255,0.14)` |
| `9b65374e8a4754c4.css` | `.m-chip.is-active` | `background: var(--m-chip-active-bg); color: var(--m-chip-active-ink); font-weight: 700; border-color: transparent` | `--m-chip-active-bg: L=#1c2733 / D=#e8ebf2`<br>`--m-chip-active-ink: L=#ffffff / D=#10151c` |

本表 70 条编译规则，覆盖全部 `.hot-topics-*`，完整 `.m-hotcard-*`（含要求的 rank）、`.m-score*`、`.m-row*`、`.m-chip*` 和 `.m-tab*`。active/press 及 display 切换见 C 的 ≤960px 组。

### B.3 日报的 `--d-*` 桥接

`b7fdde76251cc8ef.css` 在 `.daily-shell` 内把日报局部 token 映射回全局 token；浅色另把 `--d-bg` 改成 `--surface-card`。解析后如下：

| 日报 token | light 实际值 | dark 实际值 | 映射表达式 | bundle |
|---|---|---|---|---|
| `--d-bg` | `#ffffff` | `#10151c` | light 特例映射 `--surface-card`；dark 映射 `--bg-0` | `b7fdde76251cc8ef.css` |
| `--d-text` | `#1c2733` | `#e8ebf2` | `--text-0` | `b7fdde76251cc8ef.css` |
| `--d-text-soft` | `#5c6672` | `#98a2b3` | `--text-1` | `b7fdde76251cc8ef.css` |
| `--d-text-dim` | `#5c6672` | `#98a2b3` | `--text-1` | `b7fdde76251cc8ef.css` |
| `--d-text-faint` | `#6b7684` | `#7b869a` | `--text-2` | `b7fdde76251cc8ef.css` |
| `--d-accent` | `#135e6b` | `#6cb8c6` | `--accent-cyan-fg` | `b7fdde76251cc8ef.css` |
| `--d-accent-soft` | `color-mix(in srgb,#135e6b 55%,transparent)` | `color-mix(in srgb,#4fa3b3 55%,transparent)` | `color-mix(...var(--accent-cyan) 55%...)` | `b7fdde76251cc8ef.css` |
| `--d-accent-dim` | `rgba(19,94,107,0.07)` | `rgba(79,163,179,0.07)` | `rgba(var(--theme-accent-rgb),0.07)` | `b7fdde76251cc8ef.css` |
| `--d-rule` | `#eceef0` | `rgba(255,255,255,0.06)` | `--border-soft` | `b7fdde76251cc8ef.css` |
| `--d-rule-strong` | `#e2e4e7` | `rgba(255,255,255,0.08)` | `--border` | `b7fdde76251cc8ef.css` |
| `--sans` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | `system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` | `--font-body` | `b7fdde76251cc8ef.css` |

### B.4 `.daily-*` / period / reader 全部 base 块

| bundle | selector（编译后、源码顺序） | 实际声明 | `var()` 解析 |
|---|---|---|---|
| `b7fdde76251cc8ef.css` | `.daily-shell` | `min-width: 0; --d-bg: var(--bg-0); --d-text: var(--text-0); --d-text-soft: var(--text-1); --d-text-dim: var(--text-1); --d-text-faint: var(--text-2); --d-accent: var(--accent-cyan-fg); --d-accent-soft: color-mix(in srgb,var(--accent-cyan) 55%,transparent); --d-accent-dim: rgba(var(--theme-accent-rgb),0.07); --d-rule: var(--border-soft); --d-rule-strong: var(--border); --sans: var(--font-body); background: var(--d-bg); color: var(--d-text); min-height: 100vh; font-family: var(--sans); line-height: 1.7; font-feature-settings: "palt" 1; transition: background .22s ease,color .18s ease; margin: -24px -28px -72px` | `--bg-0: L=#f4f5f6 / D=#10151c`<br>`--text-0: L=#1c2733 / D=#e8ebf2`<br>`--text-1: L=#5c6672 / D=#98a2b3`<br>`--text-2: L=#6b7684 / D=#7b869a`<br>`--accent-cyan-fg: L=#135e6b / D=#6cb8c6`<br>`--accent-cyan: L=#135e6b / D=#4fa3b3`<br>`--theme-accent-rgb: L=19,94,107 / D=79,163,179`<br>`--border-soft: L=#eceef0 / D=rgba(255,255,255,0.06)`<br>`--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--font-body: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-bg: L=#ffffff / D=#10151c`<br>`--d-text: L=#1c2733 / D=#e8ebf2`<br>`--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `:root[data-theme=light] .daily-shell` | `--d-bg: var(--surface-card); background: var(--d-bg)` | `--surface-card: L=#ffffff / D=#171d26`<br>`--d-bg: L=#ffffff / D=#10151c` |
| `b7fdde76251cc8ef.css` | `:root[data-theme=light] .app-main:has(>.daily-shell)` | `background: var(--surface-card); transition: var(--theme-transition)` | `--surface-card: L=#ffffff / D=#171d26`<br>`--theme-transition: L=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease / D=background-color 220ms ease,background 220ms ease,color 180ms ease,border-color 180ms ease,box-shadow 220ms ease` |
| `b7fdde76251cc8ef.css` | `:root[data-theme=light]:has(.daily-shell)` | `background-color: var(--surface-card)` | `--surface-card: L=#ffffff / D=#171d26` |
| `b7fdde76251cc8ef.css` | `html:has(.daily-shell)` | `scroll-behavior: smooth` | — |
| `b7fdde76251cc8ef.css` | `.daily-layout` | `display: flex; align-items: stretch; min-height: 100vh` | — |
| `b7fdde76251cc8ef.css` | `.daily-side` | `flex: 0 0 clamp(240px,20vw,320px); width: clamp(240px,20vw,320px); align-self: stretch; border-right: 1px solid var(--d-rule); position: sticky; top: 0; height: 100vh; overflow-y: auto; padding: 0 16px 32px 24px` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-side::-webkit-scrollbar` | `width: 6px` | — |
| `b7fdde76251cc8ef.css` | `.daily-side::-webkit-scrollbar-thumb` | `background: var(--d-rule-strong); border-radius: 3px` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.daily-main` | `flex: 1 1; min-width: 0; padding: 64px 32px 160px; display: flex; justify-content: center` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-nav` | `font-family: var(--sans); font-size: 13px; display: flex; flex-direction: column; gap: 24px; padding-top: 32px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch` | `display: grid; grid-template-columns: repeat(3,1fr); border: 1px solid var(--d-rule-strong); border-radius: 6px; overflow: hidden` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch-item` | `padding: 11px 0; text-align: center; font-family: var(--sans); font-size: 13px; font-weight: 600; letter-spacing: 1px; color: var(--d-text-dim); text-decoration: none; transition: color .15s,background .15s` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch-item+.daily-side-switch-item` | `border-left: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch-item:hover` | `color: var(--d-text)` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch-item.is-active` | `background: var(--d-accent-dim); color: var(--d-accent)` | `--d-accent-dim: L=rgba(19,94,107,0.07) / D=rgba(79,163,179,0.07)`<br>`--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-side-switch-item:focus-visible` | `outline: 2px solid var(--d-accent); outline-offset: -2px` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-side-empty` | `font-size: 12px; color: var(--d-text-faint); padding: 12px 0` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-side-months` | `display: flex; flex-direction: column; gap: 6px` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-month` | `border-bottom: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-side-month:last-of-type` | `border-bottom: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-month>summary` | `list-style: none; cursor: pointer; padding: 10px 10px 10px 14px; display: flex; align-items: center; justify-content: space-between; -webkit-user-select: none; user-select: none; position: relative` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-month>summary::-webkit-details-marker` | `display: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-month>summary:before` | `content: ""; position: absolute; left: 0; top: 50%; width: 6px; height: 6px; border-right: 1.5px solid var(--d-text-faint); border-bottom: 1.5px solid var(--d-text-faint); transform: translateY(-50%) rotate(-45deg); transition: transform .18s ease` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-side-month[open]>summary:before` | `transform: translateY(-50%) rotate(45deg)` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-month-name` | `font-family: var(--sans); font-size: 14px; color: var(--d-text-soft); letter-spacing: .5px; font-weight: 600; flex: 1 1` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-soft: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-side-month-count` | `font-size: 11px; color: var(--d-text-faint); letter-spacing: .5px` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-side-day-list` | `list-style: none; margin: 4px 0 12px; padding: 0; display: flex; flex-direction: column; gap: 1px` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-day` | `display: flex; align-items: baseline; gap: 12px; padding: 8px 10px 8px 0; text-decoration: none; color: var(--d-text-dim); border-radius: 4px; transition: background .12s,color .12s` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-side-day:hover` | `background: var(--d-rule); color: var(--d-text)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-side-day.is-active` | `background: var(--d-accent-dim); color: var(--d-accent)` | `--d-accent-dim: L=rgba(19,94,107,0.07) / D=rgba(79,163,179,0.07)`<br>`--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-side-day-num` | `font-family: var(--sans); font-size: 11px; letter-spacing: .5px; color: var(--d-text-faint); white-space: nowrap; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; padding-left: 14px; min-width: 52px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-side-day.is-active .daily-side-day-num` | `color: var(--d-accent); font-weight: 600` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-side-day-headline` | `font-family: var(--sans); font-size: 12.5px; line-height: 1.45; flex: 1 1; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; letter-spacing: .2px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-side-archive` | `font-family: var(--sans); font-size: 11px; color: var(--d-text-faint); text-decoration: none; letter-spacing: 1.5px; text-transform: uppercase; display: inline-flex; align-items: center; gap: 4px; padding: 10px 0; border-top: 1px solid var(--d-rule); transition: color .15s` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a`<br>`--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-side-archive-icon` | `width: 13px; height: 13px; flex-shrink: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-archive:hover` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-paper` | `width: 100%; max-width: 760px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel` | `background: color-mix(in srgb,var(--d-text-faint) 16%,transparent); border-radius: 4px; animation: daily-skel-pulse 1.4s ease-in-out infinite` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-paper-skel` | `padding-top: 2px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-masthead` | `display: grid; justify-items: center; gap: 12px; margin-bottom: 28px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-eyebrow` | `width: 220px; height: 11px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-title` | `width: 300px; height: 34px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-subline` | `width: 240px; height: 12px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-rules` | `width: 100%; height: 5px; border-top: 2px solid var(--d-rule-strong); border-bottom: 1px solid var(--d-rule)` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-skel-toc` | `width: 100%; height: 180px; margin-bottom: 28px; border-radius: var(--radius-sm)` | `--radius-sm: L=8px / D=8px` |
| `b7fdde76251cc8ef.css` | `.daily-skel-body` | `display: grid; gap: 14px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-line` | `width: 100%; height: 14px` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-line-short` | `width: 62%` | — |
| `b7fdde76251cc8ef.css` | `.daily-skel-switch` | `height: 34px; border-radius: var(--radius-sm)` | `--radius-sm: L=8px / D=8px` |
| `b7fdde76251cc8ef.css` | `.daily-side-nav .daily-skel-line` | `height: 12px` | — |
| `b7fdde76251cc8ef.css` | `.daily-side-nav .daily-skel-line-short` | `width: 70%` | — |
| `b7fdde76251cc8ef.css` | `.daily-masthead` | `text-align: center; margin-bottom: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-masthead-eyebrow` | `font-family: var(--font-mono); font-size: 10.5px; color: var(--d-text-faint); letter-spacing: .12em; text-transform: uppercase; display: flex; align-items: center; justify-content: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-eyebrow .sep` | `color: var(--d-text-faint); opacity: .6` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-title` | `font-family: var(--sans); font-size: 34px; font-weight: 800; letter-spacing: .02em; line-height: 1; margin: 0 0 10px; color: var(--d-text); display: flex; align-items: baseline; justify-content: center; gap: 0; flex-wrap: wrap` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-title .accent` | `color: var(--d-accent); position: relative; display: inline-block` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-title .zh` | `margin-left: 12px` | — |
| `b7fdde76251cc8ef.css` | `.daily-masthead-meta` | `display: flex; align-items: center; justify-content: center; gap: 8px; padding-top: 0; border-top: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-masthead-date` | `font-family: var(--sans); font-size: 12px; color: var(--d-text-faint); letter-spacing: .04em; margin: 0; font-weight: 400` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-meta-rule` | `height: auto; background: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-masthead-meta-rule:before` | `content: "·"; color: var(--d-text-faint)` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-masthead-tagline` | `font-family: var(--sans); font-size: 12px; color: var(--d-text-faint); letter-spacing: .04em; text-transform: none; margin: 0; font-weight: 400` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-masthead:after` | `content: ""; display: block; height: 0; margin-top: 14px; border-top: 2px solid var(--d-text); padding-top: 2px; border-bottom: 1px solid var(--d-text); box-sizing: content-box` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-desktop .reader-toc` | `margin: 0 0 28px` | — |
| `b7fdde76251cc8ef.css` | `.daily-section` | `padding: 0 0 24px; margin: 0; scroll-margin-top: 24px` | — |
| `b7fdde76251cc8ef.css` | `.daily-section:last-of-type` | `padding-bottom: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-section+.daily-section` | `padding-top: 24px; border-top: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-section-header` | `display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; margin-bottom: 0; padding-bottom: 10px; border-bottom: 1px solid var(--d-rule-strong)` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.daily-section-no` | `font-family: var(--font-mono); font-size: 13px; font-weight: 600; color: var(--d-accent); letter-spacing: 0; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; flex-shrink: 0` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-section-title` | `font-family: var(--sans); font-size: 17px; font-weight: 700; color: var(--d-text); letter-spacing: 0; margin: 0; line-height: 1.2` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-section-subtitle` | `font-family: var(--font-mono); font-size: 10.5px; font-weight: 400; color: var(--d-text-faint); letter-spacing: .1em; text-transform: uppercase` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-section-count` | `font-family: var(--sans); font-size: 11.5px; color: var(--d-text-dim); letter-spacing: 0; white-space: nowrap; margin-left: auto` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-section-count strong` | `font-family: var(--sans); font-size: 12.5px; color: var(--d-text); font-weight: 700; margin-right: 2px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-section-articles` | `display: block; border: 0; border-radius: 0; padding: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-article` | `padding: 14px 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-article:first-child` | `padding-top: 16px` | — |
| `b7fdde76251cc8ef.css` | `.daily-article:last-child` | `padding-bottom: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-article+.daily-article` | `border-top: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-article-title` | `font-family: var(--sans); font-size: 15px; line-height: 1.5; font-weight: 700; margin: 0 0 5px; letter-spacing: 0; color: var(--d-text)` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-article-title a` | `color: inherit; text-decoration: none; transition: color .15s` | — |
| `b7fdde76251cc8ef.css` | `.daily-article-title a:hover` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-article--lead .daily-article-title` | `font-size: 17px; margin-bottom: 5px` | — |
| `b7fdde76251cc8ef.css` | `.daily-article-source` | `font-family: var(--sans); margin: 0 0 7px; display: flex; align-items: center; gap: 8px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-article-source,.daily-article-source .role-tag` | `font-size: 11px; letter-spacing: 0; color: var(--d-text-faint)` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-article-source .role-tag` | `display: inline-block; font-weight: 600; text-transform: none; padding: 1px 6px; border: 0; border-radius: 3px; background: var(--surface-2)` | `--surface-2: L=rgba(28,39,51,0.06) / D=rgba(255,255,255,0.07)` |
| `b7fdde76251cc8ef.css` | `.daily-article-source .role-tag--official` | `color: var(--d-accent); background: rgba(var(--theme-accent-rgb),.07)` | `--d-accent: L=#135e6b / D=#6cb8c6`<br>`--theme-accent-rgb: L=19,94,107 / D=79,163,179` |
| `b7fdde76251cc8ef.css` | `.daily-article-summary` | `font-family: var(--sans); font-size: 13.5px; line-height: 1.7; color: var(--d-text-soft); margin: 0; letter-spacing: 0; text-autospace: normal` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-soft: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-article:not(.daily-article--lead) .daily-article-summary` | `overflow: hidden; display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical` | — |
| `b7fdde76251cc8ef.css` | `.daily-lead` | `margin: 28px 0 0; padding: 22px 26px; border: 1px solid var(--d-rule-strong); border-radius: 12px` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.daily-lead-tag` | `display: block; font-size: 11px; color: var(--d-accent); letter-spacing: 3px; text-transform: uppercase; margin-bottom: 10px` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-lead-tag,.daily-lead-title` | `font-family: var(--sans); font-weight: 600` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-lead-title` | `font-size: 21px; line-height: 1.45; letter-spacing: -.1px; color: var(--d-text); margin: 0 0 12px` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-lead p` | `font-family: var(--sans); font-size: 16px; line-height: 1.75; color: var(--d-text-soft); margin: 0; letter-spacing: .1px; text-autospace: normal` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-soft: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-metrics` | `margin-top: 32px; padding: 0; border-top: 1px solid var(--d-rule-strong); border-bottom: 1px solid var(--d-rule-strong); display: grid; grid-template-columns: repeat(4,1fr); gap: 0; font-family: var(--sans)` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-metric` | `text-align: center; padding: 14px 8px` | — |
| `b7fdde76251cc8ef.css` | `.daily-metric+.daily-metric` | `border-left: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-metric-value` | `font-family: var(--font-mono); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; font-size: 22px; font-weight: 600; color: var(--d-text); line-height: 1.1` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-metric-label` | `font-size: 11px; color: var(--d-text-faint); letter-spacing: 0; margin-top: 2px; text-transform: none` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-empty` | `padding: 120px 0; text-align: center; color: var(--d-text-dim); font-size: 16px` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-empty-title` | `font-family: var(--sans); font-size: 28px; font-weight: 600; color: var(--d-text); margin-bottom: 16px; letter-spacing: 1px` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-prev-next` | `display: flex; justify-content: space-between; margin-top: 18px; font-family: var(--sans); font-size: 12.5px; letter-spacing: 0` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-prev-next a,.daily-prev-next-link` | `color: var(--d-text-dim); text-decoration: none; padding: 4px 0; transition: color .15s` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-prev-next a:hover` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-prev-next-link` | `display: inline-flex; align-items: center; gap: 4px` | — |
| `b7fdde76251cc8ef.css` | `.daily-prev-next-link--disabled` | `color: var(--d-text-faint); opacity: .55` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-prev-next-icon` | `width: 13px; height: 13px; flex-shrink: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-footer` | `margin-top: 20px; text-align: center; font-family: var(--sans); font-size: 11px; color: var(--d-text-faint); letter-spacing: 0` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-index` | `max-width: 720px; margin: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-empty` | `padding: 32px 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-title` | `font-size: 32px; font-weight: 700; margin: 0 0 12px; letter-spacing: 1px` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-subtitle,.daily-index-title` | `font-family: var(--sans); text-align: center` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-index-subtitle` | `font-size: 11px; color: var(--d-text-faint); letter-spacing: 5px; text-transform: uppercase; margin-bottom: 64px` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-index-list` | `list-style: none; margin: 0; padding: 0` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-row` | `display: flex; align-items: baseline; gap: 28px; padding: 24px 0; text-decoration: none; color: var(--d-text); transition: padding .15s,color .15s` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-index-row+.daily-index-row` | `border-top: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.daily-index-row:hover` | `padding-left: 8px` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-date,.daily-index-row:hover .daily-index-headline` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.daily-index-date` | `font-family: var(--sans); font-size: 11px; letter-spacing: 1.5px; font-weight: 600; min-width: 90px; white-space: nowrap` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-index-headline` | `font-family: var(--sans); font-size: 17px; font-weight: 500; flex: 1 1; line-height: 1.55` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif` |
| `b7fdde76251cc8ef.css` | `.daily-index-headline-muted` | `opacity: .5` | — |
| `b7fdde76251cc8ef.css` | `.daily-index-events` | `font-family: var(--sans); font-size: 11px; color: var(--d-text-faint); letter-spacing: 1px; white-space: nowrap` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.period-lead` | `margin: 0 0 40px; padding: 22px 26px; border: 1px solid var(--d-rule-strong); background: var(--surface-card); border-radius: 12px` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--surface-card: L=#ffffff / D=#171d26` |
| `b7fdde76251cc8ef.css` | `.daily-desktop .reader-toc,.period-paper>.period-lead` | `margin-top: 18px` | — |
| `b7fdde76251cc8ef.css` | `.period-lead-kicker` | `font-family: var(--font-mono); font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase; color: var(--d-accent); font-weight: 600; margin-bottom: 10px` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.period-lead-headline` | `font-family: var(--sans); font-size: clamp(22px,3.2vw,28px); font-weight: 800; line-height: 1.35; color: var(--d-text); margin: 0 0 12px; text-wrap: balance` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.period-lead-overview` | `font-size: 16px; line-height: 1.9; color: var(--d-text-soft); margin: 0; max-width: 46em; text-autospace: normal` | `--d-text-soft: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.period-stats` | `display: flex; flex-wrap: wrap; gap: 0; margin: 0 0 56px; border: 1px solid var(--d-rule-strong); border-radius: 10px; overflow: hidden` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.period-stat` | `flex: 1 1 120px; padding: 16px 20px; text-align: center` | — |
| `b7fdde76251cc8ef.css` | `.period-stat+.period-stat` | `border-left: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.period-stat-value` | `font-family: var(--font-mono); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; font-size: 26px; font-weight: 700; color: var(--d-text); line-height: 1.2` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.period-stat-label` | `margin-top: 4px; font-size: 12px; color: var(--d-text-dim); letter-spacing: 1px` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.period-theme-intro` | `font-family: var(--sans); font-size: 16px; line-height: 1.75; color: var(--d-text-soft); margin: 0 0 20px; max-width: 46em; letter-spacing: .1px; text-autospace: normal` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-soft: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.period-stories` | `border: 1px solid var(--d-rule); border-radius: 12px; padding: 8px 24px` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.period-story` | `display: flex; align-items: baseline; gap: 18px; padding: 13px 0` | — |
| `b7fdde76251cc8ef.css` | `.period-story+.period-story` | `border-top: 1px dashed var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.period-story-title` | `flex: 1 1; min-width: 0; font-family: var(--sans); font-size: 15.5px; font-weight: 600; line-height: 1.55; letter-spacing: -.1px; color: var(--d-text); margin: 0` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.period-story-title a` | `color: inherit; text-decoration: none; transition: color .15s` | — |
| `b7fdde76251cc8ef.css` | `.period-story-title a:hover` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.period-story-source` | `flex: none; font-family: var(--sans); font-size: 11px; letter-spacing: 1px; color: var(--d-text-faint); white-space: nowrap; max-width: 38%; overflow: hidden; text-overflow: ellipsis` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.period-paper .daily-switchrow` | `display: none` | — |
| `b7fdde76251cc8ef.css` | `.period-paper .reader-toc` | `margin: 0 0 44px` | — |
| `b7fdde76251cc8ef.css` | `.daily-endcard` | `display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin-top: 18px; padding: 14px 18px; border: 1px solid var(--d-rule-strong); background: var(--surface-card); border-radius: 12px; box-shadow: var(--shadow-card)` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--surface-card: L=#ffffff / D=#171d26`<br>`--shadow-card: L=0 1px 2px rgba(28,39,51,0.05) / D=none` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-title` | `font-family: var(--sans); font-size: 13.5px; font-weight: 700; color: var(--d-text)` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-sub` | `margin-top: 2px; font-family: var(--sans); font-size: 12px; color: var(--d-text-faint)` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-actions` | `display: flex; align-items: center; gap: 10px; flex: none` | — |
| `b7fdde76251cc8ef.css` | `.daily-endcard-cta` | `display: inline-flex; align-items: center; height: 32px; padding: 0 14px; font-family: var(--sans); font-size: 12.5px; font-weight: 600; color: var(--theme-accent-contrast); background: var(--theme-accent); text-decoration: none; border: 0; border-radius: var(--radius-sm); transition: background .12s; white-space: nowrap` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--theme-accent-contrast: L=#fcfcfd / D=#10151c`<br>`--theme-accent: L=#135e6b / D=#4fa3b3`<br>`--radius-sm: L=8px / D=8px` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-cta:hover` | `background: var(--theme-accent-hover); color: var(--theme-accent-contrast)` | `--theme-accent-hover: L=#0e4a54 / D=#6cb8c6`<br>`--theme-accent-contrast: L=#fcfcfd / D=#10151c` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-minor` | `font-family: var(--sans); font-size: 12.5px; color: var(--d-text-dim); text-decoration: none` | `--sans: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.daily-endcard-minor:hover` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.reader-switch` | `display: inline-flex; gap: 2px; padding: 3px; border: 1px solid var(--d-rule-strong); border-radius: 999px; width: max-content` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.reader-switch-item` | `padding: 5px 16px; border-radius: 999px; font-size: 13px; letter-spacing: .05em; color: var(--d-text-dim); text-decoration: none; transition: color .15s` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.reader-switch-item:hover` | `color: var(--d-text)` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.reader-switch-item.is-active` | `background: var(--d-accent-dim); color: var(--d-accent); font-weight: 600` | `--d-accent-dim: L=rgba(19,94,107,0.07) / D=rgba(79,163,179,0.07)`<br>`--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.reader-toc` | `border: 1px solid var(--d-rule-strong); border-radius: 12px; padding: 16px 20px` | `--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `b7fdde76251cc8ef.css` | `.reader-toc-head` | `display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px` | — |
| `b7fdde76251cc8ef.css` | `.reader-toc-heading` | `font-size: 14px; font-weight: 600; color: var(--d-text); letter-spacing: .02em` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.reader-toc-meta` | `font-size: 12px; color: var(--d-text-dim); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.reader-toc-list` | `list-style: none; margin: 0; padding: 0` | — |
| `b7fdde76251cc8ef.css` | `.reader-toc-list li+li` | `border-top: 1px solid var(--d-rule)` | `--d-rule: L=#eceef0 / D=rgba(255,255,255,0.06)` |
| `b7fdde76251cc8ef.css` | `.reader-toc-row` | `display: flex; gap: 12px; align-items: baseline; padding: 9px 0; text-decoration: none` | — |
| `b7fdde76251cc8ef.css` | `.reader-toc-no` | `font-size: 11px; color: var(--d-accent); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; letter-spacing: .08em` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.reader-toc-body` | `flex: 1 1; min-width: 0; display: flex; flex-direction: column; gap: 2px` | — |
| `b7fdde76251cc8ef.css` | `.reader-toc-label` | `font-size: 14px; font-weight: 600; color: var(--d-text); transition: color .15s` | `--d-text: L=#1c2733 / D=#e8ebf2` |
| `b7fdde76251cc8ef.css` | `.reader-toc-row:hover .reader-toc-label` | `color: var(--d-accent)` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.reader-toc-sub` | `font-size: 13px; color: var(--d-text-dim); overflow: hidden; text-overflow: ellipsis; white-space: nowrap` | `--d-text-dim: L=#5c6672 / D=#98a2b3` |
| `b7fdde76251cc8ef.css` | `.reader-toc-count` | `font-size: 12px; color: var(--d-text-faint); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | `--d-text-faint: L=#6b7684 / D=#7b869a` |
| `b7fdde76251cc8ef.css` | `.reader-switch-item:focus-visible,.reader-toc-row:focus-visible` | `outline: 2px solid var(--d-accent); outline-offset: 2px` | `--d-accent: L=#135e6b / D=#6cb8c6` |
| `b7fdde76251cc8ef.css` | `.reader-toc-row:focus-visible` | `border-radius: var(--m-radius-btn)` | `--m-radius-btn: L=12px / D=12px` |
| `b7fdde76251cc8ef.css` | `.daily-shell .back-to-top` | `position: fixed; right: 20px; bottom: 32px; z-index: 60; width: var(--touch-target); height: var(--touch-target); display: flex; align-items: center; justify-content: center; border-radius: 999px; border: 1px solid var(--d-rule-strong); background: var(--d-bg); color: var(--d-text-dim); box-shadow: var(--shadow-soft); cursor: pointer; opacity: 0; visibility: hidden; animation: none; transform: none; transition: opacity .2s ease,visibility .2s ease,color .15s ease,border-color .15s ease` | `--touch-target: L=44px / D=44px`<br>`--d-rule-strong: L=#e2e4e7 / D=rgba(255,255,255,0.08)`<br>`--d-bg: L=#ffffff / D=#10151c`<br>`--d-text-dim: L=#5c6672 / D=#98a2b3`<br>`--shadow-soft: L=0 4px 12px rgba(28,39,51,0.06) / D=0 6px 18px rgba(0,0,0,0.35)` |
| `b7fdde76251cc8ef.css` | `.daily-shell .back-to-top.is-visible` | `opacity: 1; visibility: visible` | — |
| `b7fdde76251cc8ef.css` | `.daily-shell .back-to-top:hover` | `color: var(--d-accent); border-color: var(--d-accent-soft); transform: none` | `--d-accent: L=#135e6b / D=#6cb8c6`<br>`--d-accent-soft: L=color-mix(in srgb,#135e6b 55%,transparent) / D=color-mix(in srgb,#4fa3b3 55%,transparent)` |

本表 172 条 base 编译规则：除 `.daily-*` 外也包含同一日报 bundle 中的 `period-*`、`reader-*`、light-theme 桥接和 back-to-top；56 条响应式/减弱动效覆盖见 C。

### B.5 `.cl-*` 全部 base 块

| bundle | selector（编译后、源码顺序） | 实际声明 | `var()` 解析 |
|---|---|---|---|
| `52481b03cf298d21.css` | `.cl-page` | `display: flex; justify-content: center` | — |
| `52481b03cf298d21.css` | `.cl-shell` | `width: 100%; max-width: 880px; padding: 56px 24px 80px` | — |
| `52481b03cf298d21.css` | `.cl-eyebrow` | `font-family: var(--font-mono); font-size: 11px; letter-spacing: .16em; text-transform: uppercase; color: var(--accent-cyan-fg); opacity: .85; margin-bottom: 14px` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `52481b03cf298d21.css` | `.cl-title` | `font-family: var(--font-display); font-size: 32px; font-weight: 600; line-height: 1.35; color: var(--text-0); margin: 0 0 10px; letter-spacing: .01em` | `--font-display: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--text-0: L=#1c2733 / D=#e8ebf2` |
| `52481b03cf298d21.css` | `.cl-tag` | `margin: 0 0 64px; font-size: 14px; color: var(--text-1); line-height: 1.6` | `--text-1: L=#5c6672 / D=#98a2b3` |
| `52481b03cf298d21.css` | `.cl-days` | `display: flex; flex-direction: column; gap: 56px` | — |
| `52481b03cf298d21.css` | `.cl-day-head` | `display: flex; align-items: baseline; gap: 16px; padding-bottom: 14px; margin-bottom: 0; border-bottom: 1px solid var(--border)` | `--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `52481b03cf298d21.css` | `.cl-day-date` | `font-family: var(--font-display); font-size: 22px; font-weight: 600; color: var(--text-0); line-height: 1.4; letter-spacing: .005em` | `--font-display: L=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif / D=system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Hiragino Sans GB","HarmonyOS Sans SC","Microsoft YaHei",sans-serif`<br>`--text-0: L=#1c2733 / D=#e8ebf2` |
| `52481b03cf298d21.css` | `.cl-day-weekday` | `font-family: var(--font-mono); font-size: 12px; color: var(--text-2); letter-spacing: .06em` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--text-2: L=#6b7684 / D=#7b869a` |
| `52481b03cf298d21.css` | `.cl-entries` | `list-style: none; margin: 0; padding: 0` | — |
| `52481b03cf298d21.css` | `.cl-entry` | `display: grid; grid-template-columns: 110px 1fr; gap: 0; padding: 24px 0; border-top: 1px solid var(--border)` | `--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `52481b03cf298d21.css` | `.cl-entry:first-child` | `border-top: none` | — |
| `52481b03cf298d21.css` | `.cl-meta` | `padding-right: 24px; border-right: 1px solid var(--border); display: flex; flex-direction: column; gap: 8px; align-self: start` | `--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)` |
| `52481b03cf298d21.css` | `.cl-meta-time` | `font-family: var(--font-mono); font-size: 14px; color: var(--text-0); font-weight: 500; letter-spacing: .04em` | `--font-mono: L=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace / D=ui-monospace,SFMono-Regular,"SF Mono",Menlo,Monaco,Consolas,"Liberation Mono",monospace`<br>`--text-0: L=#1c2733 / D=#e8ebf2` |
| `52481b03cf298d21.css` | `.cl-kind` | `display: inline-flex; align-items: center; gap: 6px; font-size: 12px; font-weight: 500; letter-spacing: .02em; width: fit-content` | — |
| `52481b03cf298d21.css` | `.cl-kind-dot` | `width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0` | — |
| `52481b03cf298d21.css` | `.cl-kind--new` | `color: var(--accent-emerald-fg)` | `--accent-emerald-fg: L=#2f7d5c / D=#5fc79a` |
| `52481b03cf298d21.css` | `.cl-kind--new .cl-kind-dot` | `background: var(--accent-emerald-fg)` | `--accent-emerald-fg: L=#2f7d5c / D=#5fc79a` |
| `52481b03cf298d21.css` | `.cl-kind--improve` | `color: var(--accent-cyan-fg)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `52481b03cf298d21.css` | `.cl-kind--improve .cl-kind-dot` | `background: var(--accent-cyan-fg)` | `--accent-cyan-fg: L=#135e6b / D=#6cb8c6` |
| `52481b03cf298d21.css` | `.cl-kind--announce` | `color: var(--accent-amber-fg)` | `--accent-amber-fg: L=#96702e / D=#d3b26a` |
| `52481b03cf298d21.css` | `.cl-kind--announce .cl-kind-dot` | `background: var(--accent-amber-fg)` | `--accent-amber-fg: L=#96702e / D=#d3b26a` |
| `52481b03cf298d21.css` | `.cl-kind--removed` | `color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `52481b03cf298d21.css` | `.cl-kind--removed .cl-kind-dot` | `background: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `52481b03cf298d21.css` | `.cl-content` | `padding-left: 24px; min-width: 0` | — |
| `52481b03cf298d21.css` | `.cl-entry-title` | `margin: 0 0 12px; font-size: 17px; font-weight: 600; color: var(--text-0); line-height: 1.45; letter-spacing: .005em` | `--text-0: L=#1c2733 / D=#e8ebf2` |
| `52481b03cf298d21.css` | `.cl-entry-body` | `display: flex; flex-direction: column; gap: 12px` | — |
| `52481b03cf298d21.css` | `.cl-p` | `margin: 0; font-size: 14px; color: var(--text-1); line-height: 1.75` | `--text-1: L=#5c6672 / D=#98a2b3` |
| `52481b03cf298d21.css` | `.cl-ul` | `margin: 0; padding: 0 0 0 20px; list-style: disc; display: flex; flex-direction: column; gap: 8px` | — |
| `52481b03cf298d21.css` | `.cl-ul::marker` | `color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `52481b03cf298d21.css` | `.cl-li` | `font-size: 14px; color: var(--text-1); line-height: 1.75; padding-left: 4px` | `--text-1: L=#5c6672 / D=#98a2b3` |
| `52481b03cf298d21.css` | `.cl-li::marker` | `color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` |
| `52481b03cf298d21.css` | `.cl-li strong,.cl-p strong` | `color: var(--text-0); font-weight: 600` | `--text-0: L=#1c2733 / D=#e8ebf2` |
| `52481b03cf298d21.css` | `.cl-foot` | `margin-top: 56px; padding-top: 24px; border-top: 1px solid var(--border-soft); text-align: center; font-size: 12.5px; color: var(--text-2); letter-spacing: .02em` | `--border-soft: L=#eceef0 / D=rgba(255,255,255,0.06)`<br>`--text-2: L=#6b7684 / D=#7b869a` |

本表 34 条 base 编译规则；其余 8 条 ≤640px 覆盖见 C。`52481b03cf298d21.css` 中全部 42 条规则均已覆盖。

### B.6 `.hot-*` 全部 base 块（Phase 0.0 增量）

来源 `cdf657f8b4e0d826.css`（11,253 B / SHA-256 `45c0692e539a7525d9018341970e33b2dd8754db37c10f97dead14e46e01dab2`），2026-08-03 09:15 按冻结 `hot.html` 的内容哈希路径取得。该 bundle 共 92 个 top-level 块，其中 39 条 base 规则命中 31 个唯一 `.hot-*` selector（与 Phase 0.0 预期计数一致），另 3 个 `@media` 块见 C.2。

与 `.hot-*` 同组出现的 `.event-*` selector 属 AIHOT 的聚合 story 详情页，我方无对应数据模型（plan GAP-57 `[accepted-divergence]`），下表只在分组 selector 里保留其字面拼写以便回查，不作为我方实现目标。

我方 token 词汇表已由 Phase 1 建立，本表「我方对应」直接引用其中的名字；标注**新建**的是 `/hot` 才首次需要的 token。

| bundle | selector（编译后、源码顺序） | 实际声明 | `var()` 解析 → 我方对应 |
|---|---|---|---|
| `cdf657f8b4e0d826.css` | `.event-page,.hot-page` | `width: min(1120px, calc(100% - 48px)); margin: 0 auto; padding: 48px 0 72px; color: var(--text-0)` | `--text-0: L=#1c2733 / D=#e8ebf2` → `var(--ink)` |
| `cdf657f8b4e0d826.css` | `.event-hero,.hot-hero` | `max-width: 760px; margin-bottom: 32px` | — |
| `cdf657f8b4e0d826.css` | `.event-kicker,.event-section-eyebrow,.hot-hero-kicker,.hot-rank-eyebrow` | `font-family: var(--font-mono); font-size: var(--text-size-xs); font-weight: 700; letter-spacing: .12em; color: var(--accent-cyan-fg)` | `--text-size-xs: 0.75rem` → `var(--text-size-xs)`<br>`--accent-cyan-fg: L=#135e6b / D=#6cb8c6` → `var(--accent-ink)`<br>`--font-mono` → `var(--font-mono)` |
| `cdf657f8b4e0d826.css` | `.event-hero h1,.hot-hero h1` | `margin: 10px 0 12px; font-size: clamp(2rem, 5vw, 3.5rem); line-height: 1.12; letter-spacing: -.035em` | 字号为 viewport 流体值，无 token |
| `cdf657f8b4e0d826.css` | `.hot-hero p` | `margin: 0; max-width: 680px; font-size: var(--text-size-md); line-height: var(--line-height-relaxed); color: var(--text-1); text-wrap: pretty` | `--text-size-md: 1rem`<br>`--line-height-relaxed: 1.75`<br>`--text-1: L=#5c6672 / D=#98a2b3` → `var(--soft)` |
| `cdf657f8b4e0d826.css` | `.hot-rank-grid` | `display: grid; grid-template-columns: minmax(0,1fr); gap: 20px; align-items: start` | — |
| `cdf657f8b4e0d826.css` | `.event-section,.hot-rank-panel` | `border: 1px solid var(--border); border-radius: var(--radius-lg); background: var(--surface-card); box-shadow: var(--shadow-card)` | `--border: L=#e2e4e7 / D=rgba(255,255,255,0.08)` → `var(--border)`<br>`--radius-lg: 16px`<br>`--surface-card: L=#ffffff / D=#171d26` → `var(--panel)`<br>`--shadow-card: L=0 1px 2px rgba(28,39,51,0.05) / D=none` |
| `cdf657f8b4e0d826.css` | `.event-section-head,.hot-rank-head` | `display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; padding: 20px 22px 16px; border-bottom: 1px solid var(--border-soft)` | `--border-soft: L=#eceef0 / D=rgba(255,255,255,0.06)` → `var(--border-soft)` |
| `cdf657f8b4e0d826.css` | `.event-section-head h2,.hot-rank-head h2` | `margin: 4px 0 0; font-size: var(--text-size-xl); line-height: var(--line-height-tight)` | `--text-size-xl: 1.25rem`<br>`--line-height-tight: 1.25` |
| `cdf657f8b4e0d826.css` | `.event-section-note,.hot-rank-count` | `flex: none; font-size: var(--text-size-xs); color: var(--text-2)` | `--text-2: L=#6b7684 / D=#7b869a` → `var(--muted)` |
| `cdf657f8b4e0d826.css` | `.event-report-list,.hot-rank-list` | `list-style: none; margin: 0; padding: 0` | — |
| `cdf657f8b4e0d826.css` | `.hot-rank-row` | `display: flex; align-items: center; gap: 14px; min-width: 0; padding: 15px 20px; border-bottom: 1px solid var(--border-soft); transition: background var(--dur-base) ease` | `--border-soft` 同上<br>`--dur-base: 160ms` → **新建** `--dur-base` |
| `cdf657f8b4e0d826.css` | `.hot-rank-row:last-child` | `border-bottom: 0` | — |
| `cdf657f8b4e0d826.css` | `.hot-rank-row:hover` | `background: var(--surface-1)` | `--surface-1: L=rgba(28,39,51,0.04) / D=rgba(255,255,255,0.04)` → `var(--surface-soft)` |
| `cdf657f8b4e0d826.css` | `.hot-rank-number` | `flex: none; width: 28px; font-family: var(--font-mono); font-size: var(--text-size-sm); font-weight: 700; color: var(--rank-rest)` | `--text-size-sm: 0.8125rem`<br>`--rank-rest: L=#6b7684 / D=#7b869a` → `var(--muted)` |
| `cdf657f8b4e0d826.css` | `.hot-rank-number-1` | `color: var(--rank-1)` | `--rank-1: L=#b3402a / D=#d86a52` → `var(--danger)`（我方 dark 需确认同值） |
| `cdf657f8b4e0d826.css` | `.hot-rank-number-2` | `color: var(--rank-2)` | `--rank-2: L=#c2703f / D=#d18a5e` → **新建** `--rank-second` |
| `cdf657f8b4e0d826.css` | `.hot-rank-number-3` | `color: var(--rank-3)` | `--rank-3: L=#b8873a / D=#d3b26a` → `var(--gold)` |
| `cdf657f8b4e0d826.css` | `.hot-rank-content` | `flex: 1 1; min-width: 0` | — |
| `cdf657f8b4e0d826.css` | `.event-report-link,.hot-rank-link` | `color: var(--text-0); text-decoration: none; font-weight: 700; line-height: var(--line-height-normal); transition: color var(--dur-fast) ease` | `--line-height-normal: 1.5`<br>`--dur-fast: 120ms` → **新建** `--dur-fast` |
| `cdf657f8b4e0d826.css` | `.event-back a:hover,.event-report-link:hover,.hot-rank-link:hover` | `color: var(--accent-cyan-fg)` | → `var(--accent-ink)` |
| `cdf657f8b4e0d826.css` | `.event-meta,.event-report-meta,.hot-rank-meta` | `display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 5px; font-size: var(--text-size-xs); color: var(--text-2); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources` | `flex: none; min-width: 76px; text-align: right` | — |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources>summary` | `display: flex; flex-direction: column; align-items: flex-end; gap: 1px; list-style: none; cursor: pointer` | 原生 `<details>/<summary>`，无 JS |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources>summary::-webkit-details-marker` | `display: none` | — |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources[open]>.dup-tooltip` | `display: flex; flex-direction: column; gap: 4px` | `.dup-tooltip` 基础样式在 `0e23a4c20d977d43.css`，见 B.2 |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources-count` | `font-family: var(--font-mono); font-size: var(--text-size-xl); font-weight: 700; line-height: var(--line-height-tight); color: var(--text-0); font-feature-settings: "tnum"; font-variant-numeric: tabular-nums` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-sources-label` | `font-size: var(--text-size-xs); color: var(--text-2); white-space: nowrap` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-spark` | `flex: none; width: 104px; height: 32px` | GAP-57 `[accepted-divergence]`：我方无热度时间序列，不实现 |
| `cdf657f8b4e0d826.css` | `.hot-rank-spark-empty` | `display: block` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-spark-line` | `fill: none; stroke: var(--accent-cyan-fg); stroke-width: 2; stroke-linecap: round; stroke-linejoin: round` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-spark-dot` | `fill: var(--surface-card); stroke: var(--accent-cyan-fg); stroke-width: 2` | 同上 |
| `cdf657f8b4e0d826.css` | `.hot-rank-empty` | `padding: 28px 22px; color: var(--text-2); font-size: var(--text-size-sm)` | 空状态，我方需实现 |
| `cdf657f8b4e0d826.css` | `.hot-method-note` | `margin: 18px 4px 0; font-size: var(--text-size-xs); line-height: var(--line-height-relaxed); color: var(--text-2)` | 承载我方公式说明（plan L2-3：加权分×10 + 关联讨论×5，不复制 AIHOT 语义） |
| `cdf657f8b4e0d826.css` | `.hot-status` | `display: inline-block; padding: 1px 7px; border-radius: 4px; font-size: 11px; font-weight: 700; line-height: 1.6; white-space: nowrap; vertical-align: 2px` | GAP-57 `[accepted-divergence]`：我方无状态语义，不实现 |
| `cdf657f8b4e0d826.css` | `.hot-status-burst` | `background: color-mix(in srgb, var(--accent-rose-fg) 16%, transparent); color: var(--accent-rose-fg)` | `--accent-rose-fg: L=#b3402a / D=#d86a52`；同上不实现 |
| `cdf657f8b4e0d826.css` | `.hot-status-fresh` | `background: color-mix(in srgb, var(--accent-cyan-fg) 14%, transparent); color: var(--accent-cyan-fg)` | 同上不实现 |
| `cdf657f8b4e0d826.css` | `.hot-status-rising` | `background: color-mix(in srgb, var(--accent-amber-fg) 16%, transparent); color: var(--accent-amber-fg)` | `--accent-amber-fg: L=#96702e / D=#d3b26a`；同上不实现 |
| `cdf657f8b4e0d826.css` | `.hot-rank-title-line` | `display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap` | 我方仍需（标题行容器），只是其中不含 `.hot-status` |

**第二份 `/hot` 资产 `a8424ce4b86e0e18.css`（51,054 B / SHA-256 `afa6a4cb01cd28cfae0a8a66da1d048f5b26d3b309a49d2dfcd307a51dce9073`）不含任何 `.hot-*` selector**，它是应用外壳 bundle 的新哈希版本。与已映射的 `9b65374e8a4754c4.css`（51,006 B）逐块比对：297 块对 297 块，**唯一差异是两条声明**——

| selector | `9b65374e8a4754c4.css`（旧） | `a8424ce4b86e0e18.css`（`/hot` 上线后） |
|---|---|---|
| `.app-main` | `min-width:0; padding:24px 28px 72px; display:grid; gap:12px; align-content:start` | 同左，**新增** `grid-template-columns: minmax(0,1fr)` |
| `.app-main>*` | `width:100%` | 同左，**新增** `min-width:0` |

这是 grid item 默认 `min-width:auto` 导致宽内容撑破单列网格的标准修法。AIHOT 在上线 `/hot` 时补的，说明该问题在长标题榜单行上会实际发生。我方主壳若用同构 grid，应一并采用——它直接关系用户点名的"缩放时的排版"。

### B 覆盖计数

- B.1：39 条 core/timeline base 编译规则。
- B.2：70 条热点与移动 feed base 编译规则。
- B.4：172 条日报 base 编译规则。
- B.5：34 条 changelog base 编译规则。
- 合计：315 条表格规则（跨表保留少量 selector 交叉，以免丢失组合规则）。

**Phase 0.0 增量（单独计数，不并入上面 315）**：B.6 的 39 条 `.hot-*` base 编译规则 + C.2 的 2 个含 `.hot-*` 的 `@media` 块（合计 12 条含 `.hot-*` 的规则）+ `a8424ce4b86e0e18.css` 相对 `9b65374e8a4754c4.css` 的 2 条 `.app-main` 声明差异。原 315/38/345 三项计数保持不变。

## C. 响应式行为（38/38 个 `@media` 块）

下表和逐块 ledger 覆盖 5 个 bundle 的全部媒体查询。一个元素在 ≤640px 时会同时吃到 ≤960px 与 ≤640px 规则；bundle 内以 `M01…Mxx` 标出原始出现顺序，后出现且同 specificity 的属性覆盖前值。

| 分组 | 独立块数 | 声明规则数 | 涉及 bundle |
|---|---:|---:|---|
| `(max-width:960px)` | 13 | 212 | `0e23a4c20d977d43.css`, `9b65374e8a4754c4.css`, `b7fdde76251cc8ef.css`, `cb396f1063c803b6.css` |
| `(max-width:640px)` | 16 | 121 | `0e23a4c20d977d43.css`, `52481b03cf298d21.css`, `9b65374e8a4754c4.css`, `b7fdde76251cc8ef.css` |
| `(min-width:641px) and (max-width:960px)` | 1 | 1 | `9b65374e8a4754c4.css` |
| `(max-width:1200px)` | 1 | 2 | `0e23a4c20d977d43.css` |
| `(hover:none),(max-width:960px)` | 1 | 3 | `0e23a4c20d977d43.css` |
| `(prefers-reduced-motion:reduce)` | 6 | 6 | `0e23a4c20d977d43.css`, `9b65374e8a4754c4.css`, `b7fdde76251cc8ef.css` |

### C.0 ≤960px 的结构替换机制（不是同一 DOM 变形）

1. **侧栏 → 底部 tab**：宽屏基础态由 `9b65374e8a4754c4.css` 让 `.app-shell` 使用 `grid-template-columns:var(--nav-width) minmax(0,1fr)`（`--nav-width:180px`），`.sidebar` 是 sticky grid；`.m-tabbar` 基础态 `display:none`。≤960px 时 `.app-shell` 变为单列。通用 `.sidebar` 规则先把它做成 `position:fixed;transform:translateX(-100%)` 的抽屉，但公开主壳还有更具体的 `.app-shell-main .sidebar{display:none}`，所以公开 feed 实际不显示抽屉；另一个、始终已在 HTML 中的 `<nav class="m-tabbar">` 改为 fixed `display:grid`。这不是把 sidebar 节点改造成 tabbar。主内容底 padding 用 `calc(var(--m-tabbar-h) + safe-area + 28px)`，`--m-tabbar-h=54px`；back-to-top 同样抬到 tabbar 上方。
2. **桌面 feed → 移动 feed**：基础态 `.m-feed{display:none}`、`.feed-desktop{display:contents}`。≤960px 时 `.m-feed{display:block;max-width:640px}`，紧邻它的 `.m-feed+.feed-desktop{display:none}`。快照 `html/home.html` 与 `html/all.html` 都同时含两套 sibling DOM，因此 timeline/card 并没有通过 media query 原地改成 `.m-row` / `.m-xcard`，而是切换整套节点。
3. **分类 tab → 药丸 chip**：桌面 `.feed-desktop` 内是 `.segmented > .seg-item`；移动 `.m-feed` 内另有 `.m-chips > .m-chip`。`.m-chip` 基础几何为 `min-height:36px;padding:5px 13px;border-radius:999px`，active 用 `--m-chip-active-bg/ink`。父级 display 切换使其中一套可见；CSS 没有把 `.seg-item` 改名或重绘成 `.m-chip`。
4. **搜索 → 图标的准确边界**：首页/精选页的移动 `.m-chips-row` 里另有 `.m-chips-search`，是 `44×44px` 最小触控目标、图标 `18×18px`，链接到 `/all#search`；对应桌面完整 `.feed-filter-form` 位于被隐藏的 `.feed-desktop`。因此首页是不同 DOM 的“完整搜索 → 图标链接”。`/all` 页并非只剩图标：其移动 `.m-feed` 自带 `.m-search` 完整表单（`.m-search-box` + 16px input + submit），同时也隐藏桌面表单。
5. **日报**：≤960px 时 `.daily-side{display:none}`、`.daily-layout` 纵向、`.daily-main{padding:0}`；`.m-daily` 与 `.daily-desktop` 也按移动/桌面双 DOM 切换。≤640px 再压缩 masthead、article、metrics、period 与 TOC。

### C.1 全量媒体查询 ledger


#### C.1.1 ≤960px — 13 blocks / 212 rules

##### `0e23a4c20d977d43.css` `M03`（35 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.app-shell-main .app-main:has(.local-starred-page)` | `padding-top: 0` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page` | `width: 100%; max-width: 640px; min-width: 0; margin: 0 auto; gap: 12px; color: var(--m-ink)` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page .page-header-feed.page-header-compact` | `padding: 12px 0 6px; border: 0; border-radius: 0; background: transparent; box-shadow: none; overflow: visible` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page .page-header-feed.page-header-compact:after,.local-starred-page .page-header-feed.page-header-compact:before` | `display: none` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page .page-header-feed.page-header-compact .header-row` | `align-items: center; gap: 10px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page .page-title` | `font-family: var(--font-display); font-size: 22px; font-weight: 900; line-height: 1.2; color: var(--m-ink)` | `0e23a4c20d977d43.css M03` |
| `.local-starred-page .page-subtitle` | `max-width: none; margin-top: 5px; color: var(--m-ink-3); font-size: 12.5px; line-height: 1.55` | `0e23a4c20d977d43.css M03` |
| `.local-starred-note` | `padding: 10px 12px; border-color: var(--m-border); border-radius: var(--m-radius-btn); background: var(--m-surface); color: var(--m-ink-3); font-size: 12.5px; line-height: 1.6` | `0e23a4c20d977d43.css M03` |
| `.local-starred-list` | `gap: 0` | `0e23a4c20d977d43.css M03` |
| `.local-starred-card` | `display: grid; grid-template-columns: minmax(0,1fr) var(--touch-target-sm); align-items: start; gap: 8px; padding: 0; border: 0; border-bottom: 1px solid var(--m-border); border-radius: 0; background: transparent; box-shadow: none; overflow: visible` | `0e23a4c20d977d43.css M03` |
| `.local-starred-card:last-child` | `border-bottom: 0` | `0e23a4c20d977d43.css M03` |
| `.local-starred-main` | `min-height: var(--touch-target); padding: 14px 0; gap: 6px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-head` | `align-items: flex-start; gap: 12px; padding-right: 0` | `0e23a4c20d977d43.css M03` |
| `.local-starred-meta` | `gap: 7px; color: var(--m-ink-3); font-size: 12.5px; min-width: 0` | `0e23a4c20d977d43.css M03` |
| `.local-starred-source` | `min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap` | `0e23a4c20d977d43.css M03` |
| `.local-starred-time` | `flex-shrink: 0; font-family: var(--font-mono); font-size: 12px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-right` | `min-width: 0; min-height: 18px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-marks` | `gap: 6px; min-height: 18px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-marks .timeline-selected-badge` | `display: none` | `0e23a4c20d977d43.css M03`；可见性未判定（冻结 DOM 不含收藏页，且 selector 不限定于 `.feed-desktop`） |
| `.local-starred-marks .timeline-score` | `padding: 0; border: 0; background: transparent; box-shadow: none; color: var(--m-ink-3); font-size: 12px; font-weight: 600; letter-spacing: 0` | `0e23a4c20d977d43.css M03`；可见性未判定（冻结 DOM 不含收藏页，且 selector 不限定于 `.feed-desktop`） |
| `.local-starred-marks .timeline-score.score-mid` | `color: var(--m-brand)` | `0e23a4c20d977d43.css M03`；可见性未判定（冻结 DOM 不含收藏页，且 selector 不限定于 `.feed-desktop`） |
| `.local-starred-marks .timeline-score.score-high` | `color: var(--accent-emerald-fg)` | `0e23a4c20d977d43.css M03`；可见性未判定（冻结 DOM 不含收藏页，且 selector 不限定于 `.feed-desktop`） |
| `.local-starred-marks .timeline-score.score-muted` | `color: var(--m-ink-3)` | `0e23a4c20d977d43.css M03`；可见性未判定（冻结 DOM 不含收藏页，且 selector 不限定于 `.feed-desktop`） |
| `.local-starred-title` | `color: var(--m-ink); font-size: 16px; font-weight: 600; line-height: 1.45; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden` | `0e23a4c20d977d43.css M03` |
| `.local-starred-summary` | `color: var(--m-ink-2); font-size: 13.5px; line-height: 1.5; -webkit-line-clamp: 3` | `0e23a4c20d977d43.css M03` |
| `.local-starred-remove` | `position: static; justify-self: end; width: var(--touch-target); min-width: var(--touch-target); height: var(--touch-target); margin-top: 10px; padding: 0; border-radius: var(--m-radius-btn); border-color: transparent; background: transparent; color: var(--m-ink-3); opacity: .78` | `0e23a4c20d977d43.css M03` |
| `.local-starred-remove:hover` | `color: var(--m-ink); background: var(--m-field-bg); border-color: var(--m-border)` | `0e23a4c20d977d43.css M03` |
| `.local-starred-remove svg` | `width: 15px; height: 15px` | `0e23a4c20d977d43.css M03` |
| `.local-starred-remove-label` | `display: none` | `0e23a4c20d977d43.css M03` |
| `.timeline` | `--tl-time-w: 64px; --tl-rail-w: 22px; --tl-dot-top: 16px; gap: 14px` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |
| `.timeline-date` | `font-size: 13px` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |
| `.timeline-time` | `font-size: 16px` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |
| `.timeline-card` | `padding: 10px 12px` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |
| `.timeline-title` | `font-size: 14px` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |
| `.timeline-time` | `padding-top: calc(var(--tl-dot-top) - 6px)` | `0e23a4c20d977d43.css M03`；隐藏 `.feed-desktop`，不渲染，不可作为 ≤960 可见目标 |

##### `0e23a4c20d977d43.css` `M06`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.field,.page-header-feed.page-header-compact .feed-filter-form .field` | `font-size: 16px` | `0e23a4c20d977d43.css M06` |

##### `9b65374e8a4754c4.css` `M01`（26 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.app-shell,body,html` | `overflow-x: hidden` | `9b65374e8a4754c4.css M01` |
| `.app-shell,body,html` | `overflow-x: clip` | `9b65374e8a4754c4.css M01` |
| `.app-shell` | `grid-template-columns: 1fr` | `9b65374e8a4754c4.css M01` |
| `.app-mobile-bar` | `display: grid; grid-template-columns: var(--touch-target) 1fr var(--touch-target); align-items: center; gap: 8px; position: sticky; top: 0; z-index: 30; margin: 0 -16px 12px; width: 100vw; box-sizing: border-box; padding: calc(8px + env(safe-area-inset-top, 0px)) 12px 8px; border-bottom: 1px solid var(--border); -webkit-backdrop-filter: none; backdrop-filter: none; transition: background-color .12s ease,border-color .12s ease` | `9b65374e8a4754c4.css M01` |
| `.app-mobile-bar,:root[data-theme=light] .app-mobile-bar` | `background: color-mix(in srgb,var(--bg-0) 98%,transparent)` | `9b65374e8a4754c4.css M01` |
| `.app-mobile-brand` | `text-align: center; font-family: var(--font-brand); font-weight: 700; font-size: 16px; letter-spacing: .04em; text-decoration: none; line-height: 1` | `9b65374e8a4754c4.css M01` |
| `.app-mobile-brand,.app-mobile-brand-text` | `color: var(--text-0)` | `9b65374e8a4754c4.css M01` |
| `.app-hamburger,.app-mobile-bar-spacer` | `width: var(--touch-target); height: var(--touch-target)` | `9b65374e8a4754c4.css M01` |
| `.app-hamburger` | `display: inline-flex; align-items: center; justify-content: center; border-radius: 12px; border: 1px solid var(--border-strong); background: var(--surface-card); color: var(--text-0); cursor: pointer; box-shadow: var(--shadow-soft); transition: background 90ms ease,border-color 90ms ease,color 90ms ease,transform 60ms ease` | `9b65374e8a4754c4.css M01` |
| `.app-hamburger:hover` | `background: var(--surface-2); border-color: var(--border-emphasis)` | `9b65374e8a4754c4.css M01` |
| `.app-hamburger:active` | `transform: scale(.94)` | `9b65374e8a4754c4.css M01` |
| `.sidebar` | `position: fixed; top: 0; left: 0; bottom: 0; height: 100vh; width: min(86vw,320px); padding: 14px 14px 18px; z-index: 1000; transform: translateX(-100%); transition: transform .19s cubic-bezier(.32,.72,0,1),background-color .14s ease,border-color .14s ease; will-change: transform; border-right: 1px solid var(--border); border-bottom: 0; display: grid; grid-template-rows: auto auto auto 1fr auto; grid-template-columns: 1fr; align-items: start; gap: 8px; overflow-y: auto; overscroll-behavior: contain; -webkit-overflow-scrolling: touch` | `9b65374e8a4754c4.css M01` |
| `.app-shell[data-sidebar-open=true] .sidebar` | `transform: translateX(0)` | `9b65374e8a4754c4.css M01` |
| `.sidebar-close` | `display: inline-flex; align-items: center; justify-content: center; justify-self: end; width: var(--touch-target); height: var(--touch-target); margin: -2px -2px 2px; border-radius: 12px; border: 1px solid transparent; background: transparent; color: var(--text-1); cursor: pointer; transition: background .12s ease,color .12s ease,border-color .12s ease` | `9b65374e8a4754c4.css M01` |
| `.sidebar-close:hover` | `background: var(--surface-1); color: var(--text-0); border-color: var(--border)` | `9b65374e8a4754c4.css M01` |
| `.sidebar-close:active` | `transform: scale(.94)` | `9b65374e8a4754c4.css M01` |
| `.sidebar-brand` | `width: 100%; max-width: 200px; margin: 0 auto` | `9b65374e8a4754c4.css M01` |
| `.side-nav` | `display: grid; gap: 4px; padding: 0 4px` | `9b65374e8a4754c4.css M01` |
| `.side-link` | `min-height: var(--touch-target); padding: 10px 12px; justify-content: flex-start; gap: 10px` | `9b65374e8a4754c4.css M01` |
| `.side-label` | `display: inline; white-space: nowrap` | `9b65374e8a4754c4.css M01` |
| `.side-link[data-tooltip]:hover:after` | `display: none` | `9b65374e8a4754c4.css M01` |
| `.side-group` | `display: block` | `9b65374e8a4754c4.css M01` |
| `.sidebar-footer` | `padding: 4px; gap: 6px` | `9b65374e8a4754c4.css M01` |
| `.sidebar-logout` | `min-height: var(--touch-target); padding: 10px 12px; justify-content: flex-start; gap: 10px` | `9b65374e8a4754c4.css M01` |
| `.theme-toggle` | `height: var(--touch-target); margin: 6px 4px` | `9b65374e8a4754c4.css M01` |
| `.app-main` | `padding: 0 16px 60px` | `9b65374e8a4754c4.css M01` |

##### `9b65374e8a4754c4.css` `M03`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.admin-shell .side-link` | `min-height: var(--touch-target); padding: 10px 12px` | `9b65374e8a4754c4.css M03` |

##### `9b65374e8a4754c4.css` `M05`（14 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.app-shell-main .sidebar` | `display: none` | `9b65374e8a4754c4.css M05` |
| `.app-shell-main .app-main` | `padding: calc(12px + env(safe-area-inset-top, 0px)) var(--m-gutter) calc(var(--m-tabbar-h) + env(safe-area-inset-bottom, 0px) + 28px); padding-left: max(var(--m-gutter),env(safe-area-inset-left,0px)); padding-right: max(var(--m-gutter),env(safe-area-inset-right,0px))` | `9b65374e8a4754c4.css M05` |
| `.daily-desktop,.m-detail+.dt-desktop,.m-feed+.feed-desktop` | `display: none` | `9b65374e8a4754c4.css M05` |
| `.m-daily,.m-feed` | `display: block; width: 100%; max-width: 640px; margin: 0 auto; min-width: 0` | `9b65374e8a4754c4.css M05` |
| `.app-shell-main .app-main:has(.m-feed)` | `padding-top: env(safe-area-inset-top,0)` | `9b65374e8a4754c4.css M05` |
| `.back-to-top` | `bottom: calc(var(--m-tabbar-h) + env(safe-area-inset-bottom, 0px) + 16px); width: var(--touch-target); height: var(--touch-target)` | `9b65374e8a4754c4.css M05` |
| `.back-to-top-detail` | `bottom: calc(env(safe-area-inset-bottom, 0px) + 16px)` | `9b65374e8a4754c4.css M05` |
| `.m-tabbar` | `position: fixed; left: 0; right: 0; bottom: 0; z-index: 900; display: grid; grid-template-columns: repeat(4,1fr); background: var(--m-surface); border-top: 1px solid var(--m-border); padding: 4px max(6px,env(safe-area-inset-right,0px)) calc(env(safe-area-inset-bottom, 0px) + 4px) max(6px,env(safe-area-inset-left,0px)); min-height: var(--m-tabbar-h); transition: var(--theme-transition)` | `9b65374e8a4754c4.css M05` |
| `.m-tab` | `position: relative; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; min-height: var(--touch-target); padding: 4px 0; border: 0; background: none; color: var(--m-tab-inactive); text-decoration: none; cursor: pointer; -webkit-tap-highlight-color: transparent; font-family: var(--font-body)` | `9b65374e8a4754c4.css M05` |
| `.m-tab-icon` | `width: 22px; height: 22px` | `9b65374e8a4754c4.css M05` |
| `.m-tab-label` | `font-size: 11px; font-weight: 600; line-height: 1` | `9b65374e8a4754c4.css M05` |
| `.m-tab-active` | `color: var(--m-brand)` | `9b65374e8a4754c4.css M05` |
| `.m-tab-active .m-tab-icon` | `stroke-width: 2.4` | `9b65374e8a4754c4.css M05` |
| `.m-tab-active .m-tab-label` | `font-weight: 700` | `9b65374e8a4754c4.css M05` |

##### `9b65374e8a4754c4.css` `M07`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.local-starred-title-badge.m-badge` | `display: inline-block` | `9b65374e8a4754c4.css M07` |

##### `9b65374e8a4754c4.css` `M10`（117 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.m-detail` | `display: block; width: 100%; min-width: 0` | `9b65374e8a4754c4.css M10` |
| `.app-shell-main .app-main:has(.m-detail)` | `padding: 0 0 calc(env(safe-area-inset-bottom, 0px) + 24px)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bar` | `position: sticky; top: 0; z-index: 50; display: flex; align-items: center; justify-content: space-between; min-height: 52px; background: var(--m-surface); border-bottom: 1px solid var(--m-border); padding: 0 var(--m-gutter); padding-top: env(safe-area-inset-top,0); padding-left: max(var(--m-gutter),env(safe-area-inset-left,0px)); padding-right: max(var(--m-gutter),env(safe-area-inset-right,0px)); transition: var(--theme-transition)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-back` | `border: 0; background: none; cursor: pointer; font-family: var(--font-body); font-size: 15px; font-weight: 600; color: var(--m-brand); min-height: 44px; display: inline-flex; align-items: center; gap: 4px; touch-action: manipulation; -webkit-tap-highlight-color: transparent; text-decoration: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-back-icon` | `width: 17px; height: 17px; pointer-events: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-share` | `border: 0; background: none; cursor: pointer; color: var(--m-ink-3); min-height: 44px; min-width: 44px; display: inline-flex; align-items: center; justify-content: center; touch-action: manipulation; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M10` |
| `.m-detail-share-icon` | `width: 19px; height: 19px; pointer-events: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-save.timeline-star` | `width: var(--touch-target); height: var(--touch-target); padding: 0; display: inline-flex; align-items: center; justify-content: center; color: var(--m-ink-3); border-radius: var(--m-radius-btn); opacity: .92` | `9b65374e8a4754c4.css M10` |
| `.m-detail-save.timeline-star.is-starred` | `color: var(--accent-rose-fg); opacity: 1` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bar-right` | `gap: 2px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bar-ext,.m-detail-bar-right` | `display: inline-flex; align-items: center` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bar-ext` | `gap: 4px; min-height: 44px; padding: 0 6px; color: var(--m-brand); font-family: var(--font-body); font-size: 14px; font-weight: 600; text-decoration: none; touch-action: manipulation; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bar-ext-icon` | `width: 15px; height: 15px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-body-wrap` | `max-width: 640px; margin: 0 auto; padding: 18px var(--m-gutter) 8px; padding-left: max(var(--m-gutter),env(safe-area-inset-left,0px)); padding-right: max(var(--m-gutter),env(safe-area-inset-right,0px)); min-width: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-source` | `display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 14px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-source-left` | `display: flex; align-items: center; gap: 8px; min-width: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-avatar` | `width: 30px; height: 30px; border-radius: 50%; object-fit: cover; flex-shrink: 0; border: 1px solid var(--m-border)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-source-name` | `font-size: 13.5px; font-weight: 600; color: var(--m-ink); overflow: hidden; text-overflow: ellipsis; white-space: nowrap` | `9b65374e8a4754c4.css M10` |
| `.m-detail-handle` | `font-size: 12px; color: var(--m-ink-3); flex-shrink: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-marks` | `display: flex; align-items: center; gap: 6px; flex-shrink: 0` | `9b65374e8a4754c4.css M10` |
| `.m-score-tip` | `position: relative; display: flex` | `9b65374e8a4754c4.css M10` |
| `.m-score-tip>summary` | `position: relative; list-style: none; cursor: pointer; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M10` |
| `.m-score-tip>summary::-webkit-details-marker` | `display: none` | `9b65374e8a4754c4.css M10` |
| `.m-score-tip>summary:after` | `content: ""; position: absolute; inset: -14px` | `9b65374e8a4754c4.css M10` |
| `.m-score-tip-text` | `position: absolute; top: calc(100% + 8px); right: 0; z-index: 20; white-space: nowrap; font-size: 11.5px; color: var(--m-ink-2); background: var(--m-bg); border: 1px solid var(--m-border); border-radius: var(--m-radius-btn); padding: 7px 10px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-pending` | `font-size: 11.5px; color: var(--m-ink-3); background: var(--m-field-bg); padding: 2px 9px; border-radius: 999px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-title` | `font-family: var(--font-display); font-weight: 900; font-size: 23px; line-height: 1.4; letter-spacing: -.005em; color: var(--m-ink); margin: 0 0 14px; overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-meta` | `display: flex; flex-wrap: wrap; gap: 4px; font-size: 12.5px; margin-bottom: 18px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-meta,.m-detail-meta-sep` | `color: var(--m-ink-3)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-jump` | `display: inline-flex; align-items: center; min-height: var(--touch-target); margin: -8px 0 12px; font-size: 13px; font-weight: 600; color: var(--m-brand); text-decoration: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-reason` | `background: var(--m-brand-weak); border: 1px solid var(--m-border); border-radius: var(--m-radius-card); padding: 13px 15px; margin-bottom: 14px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-reason-label` | `display: block; font-size: 11px; font-weight: 600; letter-spacing: .04em; color: var(--m-brand); margin-bottom: 5px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-reason-text` | `margin: 0; font-size: 13.5px; line-height: 1.6; color: var(--m-ink-25)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary` | `background: var(--m-bg); border: 1px solid var(--m-divider); border-radius: var(--m-radius-card); padding: 13px 15px; margin-bottom: 18px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary-head` | `min-height: var(--touch-target); display: flex; align-items: center; gap: 8px; list-style: none; cursor: pointer; -webkit-user-select: none; user-select: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary-head::-webkit-details-marker` | `display: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary-label` | `display: block; font-size: 11px; font-weight: 600; letter-spacing: .04em; color: var(--m-brand); margin-bottom: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary-toggle` | `width: 7px; height: 7px; margin-left: auto; margin-right: 3px; border-right: 1.5px solid var(--m-brand); border-bottom: 1.5px solid var(--m-brand); transform: rotate(45deg) translateY(-2px); transition: transform var(--dur-fast) ease` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary[open] .m-detail-summary-toggle` | `transform: rotate(225deg) translate(-1px,-1px)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-summary-text` | `margin: 10px 0 0; font-size: 14px; line-height: 1.7; color: var(--m-ink-25)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article-anchor` | `scroll-margin-top: calc(66px + env(safe-area-inset-top, 0px))` | `9b65374e8a4754c4.css M10` |
| `.m-detail-processing` | `display: flex; align-items: center; gap: 9px; font-size: 13.5px; color: var(--m-ink-2); background: var(--m-bg); border: 1px solid var(--m-divider); border-radius: var(--m-radius-card); padding: 13px 15px; margin-bottom: 18px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-processing-spinner` | `width: 14px; height: 14px; border-radius: 50%; border: 2px solid var(--m-border); border-top-color: var(--m-brand); animation: dt-spin .8s linear infinite; flex-shrink: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-tweet` | `margin-bottom: 22px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-tweet .m-detail-p` | `font-size: 16px; line-height: 1.7; color: var(--m-ink)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-quoted` | `margin-top: 12px; padding: 11px 13px; border: 1px solid var(--m-border); border-radius: var(--m-radius-btn); background: var(--m-bg); font-size: 13.5px; line-height: 1.6; color: var(--m-ink-2)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-quoted-author` | `font-weight: 600; color: var(--m-ink); margin-right: 5px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article` | `margin-bottom: 22px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article-label` | `font-size: 11.5px; color: var(--m-ink-3)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article-head,.m-detail-article-label` | `padding-bottom: 9px; margin-bottom: 14px; border-bottom: 1px solid var(--m-divider)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article-head` | `display: flex; align-items: center; justify-content: space-between; gap: 12px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-article-head-label` | `font-size: 11.5px; color: var(--m-ink-3)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-lang-switch` | `display: inline-flex; border: 1px solid var(--m-chip-border); border-radius: 999px; overflow: hidden` | `9b65374e8a4754c4.css M10` |
| `.m-detail-lang-opt` | `appearance: none; border: 0; background: none; min-height: var(--touch-target); padding: 0 14px; font-family: var(--font-body); font-size: 12px; color: var(--m-ink-2); cursor: pointer; display: inline-flex; align-items: center; justify-content: center; -webkit-tap-highlight-color: transparent; transition: background .12s ease,color .12s ease` | `9b65374e8a4754c4.css M10` |
| `.m-detail-lang-opt.is-active` | `background: var(--m-brand); color: var(--m-brand-contrast); font-weight: 600` | `9b65374e8a4754c4.css M10` |
| `.m-detail-p` | `margin: 0 0 16px; font-size: 15.5px; line-height: 1.85; color: var(--m-ink-25); overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-p:last-child` | `margin-bottom: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-p a` | `color: var(--m-brand); -webkit-text-decoration: underline dotted; text-decoration: underline dotted; text-underline-offset: 3px; overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-bodynote` | `margin-top: 6px; font-size: 13px; line-height: 1.55; color: var(--m-ink-2); background: var(--m-bg); border: 1px solid var(--m-divider); border-radius: var(--m-radius-btn); padding: 12px 14px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html` | `font-size: 15.5px; line-height: 1.85; color: var(--m-ink-25); overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html>:first-child` | `margin-top: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html>:last-child` | `margin-bottom: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html p` | `margin: 0 0 16px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html h1,.m-detail-html h2,.m-detail-html h3,.m-detail-html h4,.m-detail-html h5,.m-detail-html h6` | `color: var(--m-ink); line-height: 1.35; font-weight: 700; margin: 26px 0 10px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html h1` | `font-size: 20px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html h2` | `font-size: 18.5px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html h3` | `font-size: 17px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html h4,.m-detail-html h5,.m-detail-html h6` | `font-size: 15.5px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html a` | `color: var(--m-brand); -webkit-text-decoration: underline dotted; text-decoration: underline dotted; text-underline-offset: 3px; overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html ol,.m-detail-html ul` | `margin: 0 0 16px; padding-left: 24px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html li` | `margin: 0 0 7px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html li>ol,.m-detail-html li>ul` | `margin: 7px 0 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html blockquote` | `margin: 0 0 16px; padding: 10px 12px; border: 1px solid var(--m-divider); border-radius: var(--m-radius-btn); background: var(--m-field-bg); color: var(--m-ink-2)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html blockquote p:last-child` | `margin-bottom: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html img` | `display: block; max-width: 100%; height: auto; margin: 16px auto; border-radius: var(--m-radius-btn); border: 1px solid var(--m-divider); cursor: zoom-in` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html a img` | `cursor: pointer` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html a[data-aihot-media=video-poster]` | `display: block` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html a[data-aihot-media=video-poster] img` | `margin-bottom: 9px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html a[data-aihot-media$=-link]` | `display: block; margin: 9px 0 0; padding: 10px 12px; border: 1px solid var(--m-divider); border-radius: var(--m-radius-btn); background: var(--m-field-bg); color: var(--m-ink-2); font-weight: 600; text-align: center; text-decoration: none` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html figure` | `margin: 16px 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html figcaption` | `margin-top: 7px; font-size: 12.5px; color: var(--m-ink-3); text-align: center` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html hr` | `border: none; border-top: 1px solid var(--m-divider); margin: 22px 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html code` | `font-family: var(--font-mono); font-size: .88em; background: var(--m-field-bg); border: 1px solid var(--m-divider); border-radius: 5px; padding: 1px 5px; overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html pre` | `margin: 0 0 16px; padding: 12px 14px; background: var(--m-field-bg); border: 1px solid var(--m-divider); border-radius: var(--m-radius-btn); overflow-x: auto; -webkit-overflow-scrolling: touch` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html pre code` | `display: block; font-size: 13px; line-height: 1.6; white-space: pre; background: none; border: none; border-radius: 0; padding: 0; color: var(--m-ink)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html .article-code-block` | `margin: 0 0 16px; border-color: var(--code-block-border); border-radius: var(--m-radius-btn); background: var(--code-block-bg)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html .article-code-block pre` | `margin: 0; padding: 12px 14px 14px; border: 0; border-radius: 0; background: transparent; color: var(--code-block-ink); scrollbar-width: thin; scrollbar-color: var(--code-block-muted) transparent` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html .article-code-block pre code` | `color: var(--code-block-ink)` | `9b65374e8a4754c4.css M10` |
| `.article-code-block.is-scrollable .article-code-scroll-hint` | `display: inline` | `9b65374e8a4754c4.css M10` |
| `.article-code-copy` | `min-height: 40px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html table` | `display: block; width: max-content; max-width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; border-collapse: collapse; margin: 0 0 16px; font-size: 14px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html td,.m-detail-html th` | `border: 1px solid var(--m-divider); padding: 7px 11px; text-align: left; vertical-align: top` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html th` | `background: var(--m-field-bg); color: var(--m-ink); font-weight: 600` | `9b65374e8a4754c4.css M10` |
| `.m-detail-html caption` | `caption-side: top; color: var(--m-ink-3); font-size: 12.5px; margin-bottom: 7px` | `9b65374e8a4754c4.css M10` |
| `.article-img-lightbox` | `padding: 12px` | `9b65374e8a4754c4.css M10` |
| `.article-img-lightbox-img` | `max-height: 88vh` | `9b65374e8a4754c4.css M10` |
| `.m-detail-explain` | `background: var(--m-field-bg); border: 1px solid var(--m-border); border-radius: var(--m-radius-card); padding: 14px 16px; margin-bottom: 22px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-explain p` | `margin: 0; font-size: 13.5px; line-height: 1.6; color: var(--m-ink-25)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-tags` | `display: flex; flex-wrap: wrap; gap: 7px; margin-bottom: 20px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-tag` | `font-size: 12px; color: var(--m-ink-2); background: var(--m-field-bg); border: 1px solid var(--m-border); border-radius: var(--m-radius-btn); padding: 4px 10px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-actions` | `display: flex; flex-direction: column; gap: 10px; margin-bottom: 24px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-exportbtn,.m-detail-readbtn,.m-detail-sharebtn` | `display: inline-flex; align-items: center; justify-content: center; gap: 7px; width: 100%; min-height: 48px; border-radius: var(--m-radius-btn); font-family: var(--font-body); font-size: 14.5px; font-weight: 600; text-decoration: none; cursor: pointer; touch-action: manipulation; -webkit-tap-highlight-color: transparent; transition: var(--theme-transition)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-readbtn` | `color: var(--m-brand); background: var(--m-brand-weak); border: 1px solid var(--m-brand)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-readbtn-icon` | `width: 16px; height: 16px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-exportbtn,.m-detail-sharebtn` | `color: var(--m-ink-2); background: var(--m-surface); border: 1px solid var(--m-border)` | `9b65374e8a4754c4.css M10` |
| `.m-detail-exportbtn[aria-disabled=true]` | `opacity: .4; cursor: not-allowed` | `9b65374e8a4754c4.css M10` |
| `.m-detail-exportbtn-icon` | `width: 16px; height: 16px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related` | `border-top: 1px solid var(--m-divider); padding-top: 18px; margin-bottom: 22px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-head` | `font-size: 12.5px; font-weight: 600; color: var(--m-ink-2); margin-bottom: 12px` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-list` | `list-style: none; margin: 0; padding: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-row` | `display: flex; gap: 11px; min-height: var(--touch-target-sm); padding: 10px 0; border-top: 1px solid var(--m-divider); touch-action: manipulation` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-list li:first-child .m-detail-related-row` | `border-top: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-time` | `font-size: 11.5px; color: var(--m-ink-3); flex-shrink: 0; width: 40px; padding-top: 1px; white-space: nowrap` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-main` | `display: flex; flex-direction: column; gap: 3px; min-width: 0` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-row-title` | `font-size: 14px; line-height: 1.45; color: var(--m-ink); overflow-wrap: anywhere` | `9b65374e8a4754c4.css M10` |
| `.m-detail-related-src` | `font-size: 11.5px; color: var(--m-ink-3)` | `9b65374e8a4754c4.css M10` |
| `.m-all-types-summary,.m-chip,.m-daily-entry-title,.m-detail-related-row,.m-detail-summary-head,.m-detail-tag,.m-hotcard-row,.m-more-row,.m-row,.m-xcard` | `transition: var(--theme-transition),background-color var(--dur-base) ease; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M10` |

##### `9b65374e8a4754c4.css` `M11`（7 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.m-all-types-summary:active,.m-chip:active,.m-daily-entry-title:active,.m-detail-related-row:active,.m-detail-summary-head:active,.m-detail-tag:active,.m-hotcard-row:has(.m-hotcard-link:active),.m-more-row:active,.m-row:active,.m-xcard:active` | `background-color: var(--m-press); transition: none` | `9b65374e8a4754c4.css M11` |
| `.m-chip.is-active:active` | `background-color: var(--m-chip-active-bg); opacity: .8; transition: none` | `9b65374e8a4754c4.css M11` |
| `.m-detail-summary-head` | `border-radius: var(--m-radius-btn)` | `9b65374e8a4754c4.css M11` |
| `.m-detail-exportbtn,.m-detail-readbtn,.m-detail-sharebtn,.m-poster-retry,.m-poster-save,.m-poster-share,.m-search-submit,.m-sentinel-retry` | `transition: var(--theme-transition),transform var(--dur-press) ease; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M11` |
| `.m-detail-exportbtn:active,.m-detail-readbtn:active,.m-detail-sharebtn:active,.m-poster-retry:active,.m-poster-save:active,.m-poster-share:active,.m-search-submit:active,.m-sentinel-retry:active` | `transform: scale(.98)` | `9b65374e8a4754c4.css M11` |
| `.m-chips-search,.m-daily-nav a,.m-detail-back,.m-detail-bar-ext,.m-detail-jump,.m-detail-share,.m-filter-note-clear,.m-hotcard-top5,.m-poster-close,.m-search-cancel,.m-tab` | `transition: opacity var(--dur-fast) ease; -webkit-tap-highlight-color: transparent` | `9b65374e8a4754c4.css M11` |
| `.m-chips-search:active,.m-daily-nav a:active,.m-detail-back:active,.m-detail-bar-ext:active,.m-detail-jump:active,.m-detail-share:active,.m-filter-note-clear:active,.m-hotcard-top5:active,.m-poster-close:active,.m-search-cancel:active,.m-tab:active` | `opacity: .65; transition: none` | `9b65374e8a4754c4.css M11` |

##### `b7fdde76251cc8ef.css` `M02`（4 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-shell` | `margin: 0; background: transparent; min-height: 0` | `b7fdde76251cc8ef.css M02` |
| `.daily-layout` | `flex-direction: column` | `b7fdde76251cc8ef.css M02` |
| `.daily-side` | `display: none` | `b7fdde76251cc8ef.css M02` |
| `.daily-main` | `padding: 0` | `b7fdde76251cc8ef.css M02` |

##### `b7fdde76251cc8ef.css` `M04`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-paper-skel` | `padding: 24px 18px 60px` | `b7fdde76251cc8ef.css M04` |

##### `b7fdde76251cc8ef.css` `M10`（2 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.period-paper .daily-switchrow` | `display: flex; justify-content: center; margin: 26px 0 0` | `b7fdde76251cc8ef.css M10` |
| `.period-paper .daily-switchrow+.period-lead` | `margin-top: 28px` | `b7fdde76251cc8ef.css M10` |

##### `b7fdde76251cc8ef.css` `M12`（2 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.reader-switch-item` | `display: inline-flex; align-items: center; min-height: var(--touch-target)` | `b7fdde76251cc8ef.css M12` |
| `.daily-shell .back-to-top` | `bottom: calc(var(--m-tabbar-h) + env(safe-area-inset-bottom, 0px) + 20px); right: 16px` | `b7fdde76251cc8ef.css M12` |

##### `cb396f1063c803b6.css` `M01`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `[role=button],[role=radio],[role=switch],[role=tab],a,button,label,summary` | `touch-action: manipulation; -webkit-tap-highlight-color: rgba(79,163,179,.22)` | `cb396f1063c803b6.css M01` |


#### C.1.2 ≤640px — 16 blocks / 121 rules

##### `0e23a4c20d977d43.css` `M01`（3 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.hot-topics-head` | `padding: 10px 12px` | `0e23a4c20d977d43.css M01`；隐藏 `.feed-desktop`，不渲染；可见 `.m-hotcard-head` live 等效目标为 `padding: 13px 16px 4px` |
| `.hot-topics-hint` | `display: none` | `0e23a4c20d977d43.css M01`；隐藏 `.feed-desktop`，不渲染；我方未抄 |
| `.hot-topics-row` | `gap: 8px; padding: 8px 12px` | `0e23a4c20d977d43.css M01`；隐藏 `.feed-desktop`，不渲染；可见 `.m-hotcard-row` live 等效目标为 `gap: 12px; padding: 10px 16px` |

##### `0e23a4c20d977d43.css` `M04`（20 rules）

可见性：本表所有 timeline 相关 selector（包括 `.timeline` 根与 `.timeline-*` selector arm）都命中 ≤640 时被 `.m-feed + .feed-desktop{display:none}` 隐藏的子树，实测祖先 `display:none` 且目标盒为 `0×0`，因此均不渲染、不可作为我方单 DOM 移动层的目标；组合 selector `.timeline-head-left,.uc-handle` 仅前一 arm 属于该结论，独立 `.uc-handle` 未纳入本轮判定。

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.timeline` | `--tl-time-w: 44px; --tl-rail-w: 16px; --tl-dot-top: 20px; gap: 14px` | `0e23a4c20d977d43.css M04` |
| `.timeline-day-head` | `grid-template-columns: calc(var(--tl-time-w) + var(--tl-rail-w)) minmax(0,1fr); align-items: center` | `0e23a4c20d977d43.css M04` |
| `.timeline-date` | `grid-column: 2; text-align: left; padding: 2px 0 4px; font-size: 16px; font-weight: 700; color: var(--text-0); letter-spacing: 0` | `0e23a4c20d977d43.css M04` |
| `.timeline-day-toggle` | `grid-column: 1; grid-row: 1; justify-self: center` | `0e23a4c20d977d43.css M04` |
| `.timeline-day-meta` | `display: none` | `0e23a4c20d977d43.css M04` |
| `.timeline-day-items` | `gap: 0` | `0e23a4c20d977d43.css M04` |
| `.timeline-day-items:before` | `display: block; left: calc(var(--tl-time-w) + (var(--tl-rail-w) / 2)); background: var(--border-strong)` | `0e23a4c20d977d43.css M04` |
| `.timeline-item` | `grid-template-columns: var(--tl-time-w) var(--tl-rail-w) minmax(0,1fr); gap: 0; padding-bottom: 8px` | `0e23a4c20d977d43.css M04` |
| `.timeline-rail` | `display: block` | `0e23a4c20d977d43.css M04` |
| `.timeline-time` | `display: block; padding-top: calc(var(--tl-dot-top) - 6px); padding-right: 6px; font-size: 12px; font-weight: 600; letter-spacing: 0; line-height: 1; color: var(--text-2)` | `0e23a4c20d977d43.css M04` |
| `.timeline-dot` | `top: var(--tl-dot-top); width: 7px; height: 7px` | `0e23a4c20d977d43.css M04` |
| `.timeline-card` | `padding: 13px; border-radius: var(--radius)` | `0e23a4c20d977d43.css M04` |
| `.timeline-card-head` | `display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: start; gap: 8px; margin-bottom: 8px` | `0e23a4c20d977d43.css M04` |
| `.timeline-head-left,.uc-handle` | `min-width: 0; overflow: hidden` | `0e23a4c20d977d43.css M04` |
| `.uc-handle` | `text-overflow: ellipsis; white-space: nowrap` | `0e23a4c20d977d43.css M04` |
| `.timeline-head-right` | `margin-left: 0; justify-content: flex-end; flex-wrap: wrap; max-width: 100%; row-gap: 4px` | `0e23a4c20d977d43.css M04` |
| `.timeline-title` | `font-size: 14.5px; line-height: 1.55` | `0e23a4c20d977d43.css M04` |
| `.timeline-summary` | `margin-top: 7px; font-size: 14px; line-height: 1.65; -webkit-line-clamp: 3` | `0e23a4c20d977d43.css M04` |
| `.timeline-card iframe,.timeline-card img,.timeline-card video` | `max-width: 100%; height: auto` | `0e23a4c20d977d43.css M04` |
| `.timeline-star` | `min-height: var(--touch-target); min-width: var(--touch-target)` | `0e23a4c20d977d43.css M04` |

##### `0e23a4c20d977d43.css` `M05`（8 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.feed-toolbar-row` | `flex-direction: column; align-items: stretch; gap: 8px` | `0e23a4c20d977d43.css M05` |
| `.page-header-feed .feed-toolbar-row .feed-filter-form` | `margin-left: 0; width: 100%; max-width: 100%` | `0e23a4c20d977d43.css M05` |
| `.page-header-feed .toolbar` | `overflow-x: auto; flex-wrap: nowrap; scroll-snap-type: x mandatory; -webkit-overflow-scrolling: touch; scrollbar-width: none; padding: 0 18px 2px; margin: 0 -18px; -webkit-mask-image: linear-gradient(90deg,transparent,#000 18px,#000 calc(100% - 32px),transparent); mask-image: linear-gradient(90deg,transparent,#000 18px,#000 calc(100% - 32px),transparent)` | `0e23a4c20d977d43.css M05` |
| `.page-header-feed .toolbar::-webkit-scrollbar` | `display: none` | `0e23a4c20d977d43.css M05` |
| `.page-header-feed .toolbar>*` | `flex-shrink: 0; scroll-snap-align: start` | `0e23a4c20d977d43.css M05` |
| `.feed-page-chip,.page-header-feed .toolbar .segmented .seg-item,.page-header-feed.page-header-compact .seg-item` | `min-height: var(--touch-target-sm)` | `0e23a4c20d977d43.css M05` |
| `.feed-page-chip` | `padding: 8px 12px; display: inline-flex; align-items: center` | `0e23a4c20d977d43.css M05` |
| `.btn:not(.btn-sm):not(.btn-xs),.feed-pagination-btn,.feed-pagination-num` | `min-height: var(--touch-target)` | `0e23a4c20d977d43.css M05` |

##### `0e23a4c20d977d43.css` `M07`（17 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.page-header` | `padding: 4px 0 0` | `0e23a4c20d977d43.css M07` |
| `.page-theme-feed` | `gap: 10px` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact` | `padding: 4px 0 0` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .header-row` | `gap: 10px` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .feed-header-actions` | `width: 100%; justify-content: space-between` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .page-subtitle` | `max-width: none` | `0e23a4c20d977d43.css M07` |
| `.seg-item` | `min-width: 64px; padding: 8px 12px` | `0e23a4c20d977d43.css M07` |
| `.field-compact,.filter-toolbar>.btn,.filter-toolbar>.field-compact,.filter-toolbar>.field-grow,.pagination-btns,.tag-scope-select` | `width: 100%` | `0e23a4c20d977d43.css M07` |
| `.pagination-btns .btn,.pagination-btns .btn-disabled` | `flex: 1 1; justify-content: center; display: inline-flex` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact` | `padding: 4px 0 0; border-radius: 0` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .page-title` | `font-size: 21px; line-height: 1.15` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .page-subtitle` | `margin-top: 3px; font-size: 12px; line-height: 1.45` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .page-divider` | `margin: 8px 0` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed.page-header-compact .page-header-body` | `gap: 8px; min-width: 0` | `0e23a4c20d977d43.css M07` |
| `.feed-toolbar-row` | `align-items: stretch; width: 100%; max-width: 100%; min-width: 0` | `0e23a4c20d977d43.css M07` |
| `.feed-toolbar-row>.segmented` | `width: 100%; max-width: 100%; min-width: 0; overflow-x: auto; justify-content: flex-start; -webkit-overflow-scrolling: touch; scrollbar-width: none` | `0e23a4c20d977d43.css M07` |
| `.page-header-feed .feed-toolbar-row .feed-filter-form` | `min-width: 0` | `0e23a4c20d977d43.css M07` |

##### `0e23a4c20d977d43.css` `M08`（7 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.feed-toolbar-row>.segmented::-webkit-scrollbar` | `display: none` | `0e23a4c20d977d43.css M08` |
| `.feed-toolbar-row>.segmented .seg-item` | `flex: 0 0 auto` | `0e23a4c20d977d43.css M08` |
| `.page-header-feed .feed-toolbar-row .feed-filter-search-row` | `position: relative; display: block; min-height: 38px` | `0e23a4c20d977d43.css M08` |
| `.page-header-feed .feed-toolbar-row .feed-filter-form .feed-filter-search-input` | `display: block; width: calc(100% - 66px)!important; max-width: calc(100% - 66px); min-width: 0` | `0e23a4c20d977d43.css M08` |
| `.page-header-feed .feed-toolbar-row .feed-filter-form button.feed-filter-submit` | `position: absolute; top: 0; right: 0; display: inline-flex; align-items: center; z-index: 2; width: 58px; min-width: 58px; height: 38px; padding-block: 0; padding-inline: 11px; justify-content: center; line-height: 1` | `0e23a4c20d977d43.css M08` |
| `.feed-toolbar-row>.segmented .seg-item` | `align-items: center; justify-content: center; min-height: var(--touch-target-sm); padding-block: 0; line-height: 1` | `0e23a4c20d977d43.css M08` |
| `.page-header-feed .feed-toolbar-row .feed-filter-form .feed-filter-search-input` | `height: 38px; min-height: 38px; padding-block: 0; line-height: 38px` | `0e23a4c20d977d43.css M08` |

##### `0e23a4c20d977d43.css` `M10`（6 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.source-overview` | `grid-template-columns: repeat(2,minmax(0,1fr))` | `0e23a4c20d977d43.css M10` |
| `.source-attention` | `display: grid` | `0e23a4c20d977d43.css M10` |
| `.source-attention__links` | `justify-content: flex-start; min-width: 0` | `0e23a4c20d977d43.css M10` |
| `.source-map-group__head,.source-map__column,.source-section-head` | `display: grid` | `0e23a4c20d977d43.css M10` |
| `.sg__row` | `grid-template-columns: 20px minmax(0,1fr) auto` | `0e23a4c20d977d43.css M10` |
| `.sg__interval,.sg__next` | `display: none` | `0e23a4c20d977d43.css M10` |

##### `52481b03cf298d21.css` `M01`（8 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.cl-shell` | `padding: 40px 16px 60px` | `52481b03cf298d21.css M01` |
| `.cl-title` | `font-size: 26px` | `52481b03cf298d21.css M01` |
| `.cl-tag` | `margin-bottom: 48px` | `52481b03cf298d21.css M01` |
| `.cl-days` | `gap: 44px` | `52481b03cf298d21.css M01` |
| `.cl-day-date` | `font-size: 19px` | `52481b03cf298d21.css M01` |
| `.cl-entry` | `grid-template-columns: 1fr; padding: 20px 0` | `52481b03cf298d21.css M01` |
| `.cl-meta` | `padding: 0 0 12px; border-right: none; border-bottom: 1px solid var(--border); margin-bottom: 12px; flex-direction: row; align-items: center; gap: 14px` | `52481b03cf298d21.css M01` |
| `.cl-content` | `padding-left: 0` | `52481b03cf298d21.css M01` |

##### `9b65374e8a4754c4.css` `M02`（2 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.app-main` | `padding: 0 12px 56px` | `9b65374e8a4754c4.css M02` |
| `.app-mobile-bar` | `margin: 0 -12px 10px; width: 100vw` | `9b65374e8a4754c4.css M02` |

##### `9b65374e8a4754c4.css` `M04`（5 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.x-tweet-media,.x-tweet-media-single` | `max-width: 100%` | `9b65374e8a4754c4.css M04` |
| `.x-tweet-media-single .x-tweet-media-cell,.x-tweet-media-single .x-tweet-media-img` | `max-width: 100%; max-height: 360px` | `9b65374e8a4754c4.css M04` |
| `.x-tweet-media-grid[data-count="4"],.x-tweet-media-grid[data-count="5"],.x-tweet-media-grid[data-count="6"],.x-tweet-media-grid[data-count="7"],.x-tweet-media-grid[data-count="8"],.x-tweet-media-grid[data-count="9"]` | `grid-template-columns: 1fr 1fr; grid-template-rows: none; grid-auto-rows: auto; aspect-ratio: auto` | `9b65374e8a4754c4.css M04` |
| `.x-tweet-media-grid[data-count="4"] .x-tweet-media-cell` | `aspect-ratio: 1/1` | `9b65374e8a4754c4.css M04` |
| `.x-tweet-media-grid[data-count="5"] .x-tweet-media-cell,.x-tweet-media-grid[data-count="6"] .x-tweet-media-cell,.x-tweet-media-grid[data-count="7"] .x-tweet-media-cell,.x-tweet-media-grid[data-count="8"] .x-tweet-media-cell,.x-tweet-media-grid[data-count="9"] .x-tweet-media-cell` | `aspect-ratio: 4/3` | `9b65374e8a4754c4.css M04` |

##### `b7fdde76251cc8ef.css` `M05`（2 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-masthead-title` | `font-size: 30px` | `b7fdde76251cc8ef.css M05` |
| `.daily-masthead-meta` | `gap: 6px` | `b7fdde76251cc8ef.css M05` |

##### `b7fdde76251cc8ef.css` `M06`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-section-header` | `gap: 8px` | `b7fdde76251cc8ef.css M06` |

##### `b7fdde76251cc8ef.css` `M07`（29 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-index,.daily-paper` | `max-width: none` | `b7fdde76251cc8ef.css M07` |
| `.daily-masthead-eyebrow` | `gap: 6px; margin-bottom: 10px; font-size: 10px; line-height: 1.5` | `b7fdde76251cc8ef.css M07` |
| `.daily-masthead-title` | `font-size: 30px; line-height: 1.05; margin-bottom: 8px` | `b7fdde76251cc8ef.css M07` |
| `.daily-masthead-date,.daily-masthead-tagline` | `font-size: 11.5px` | `b7fdde76251cc8ef.css M07` |
| `.daily-section` | `padding-bottom: 22px` | `b7fdde76251cc8ef.css M07` |
| `.daily-section+.daily-section` | `padding-top: 22px` | `b7fdde76251cc8ef.css M07` |
| `.daily-section-subtitle` | `display: none` | `b7fdde76251cc8ef.css M07` |
| `.daily-article` | `padding: 14px 0` | `b7fdde76251cc8ef.css M07` |
| `.daily-article-title` | `font-size: 15px; line-height: 1.5; margin-bottom: 5px` | `b7fdde76251cc8ef.css M07` |
| `.daily-article--lead .daily-article-title` | `font-size: 16.5px` | `b7fdde76251cc8ef.css M07` |
| `.daily-article-source` | `flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 7px` | `b7fdde76251cc8ef.css M07` |
| `.daily-article-summary` | `font-size: 15px; line-height: 1.75` | `b7fdde76251cc8ef.css M07` |
| `.daily-metrics` | `margin-top: 24px; display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: 0` | `b7fdde76251cc8ef.css M07` |
| `.daily-metric+.daily-metric` | `border-left: none` | `b7fdde76251cc8ef.css M07` |
| `.daily-metric:nth-child(2n)` | `border-left: 1px solid var(--d-rule)` | `b7fdde76251cc8ef.css M07` |
| `.daily-metric:nth-child(n+3)` | `border-top: 1px solid var(--d-rule)` | `b7fdde76251cc8ef.css M07` |
| `.daily-metric-value` | `font-size: 20px` | `b7fdde76251cc8ef.css M07` |
| `.daily-prev-next` | `margin-top: 48px; gap: 10px; font-size: 11px; letter-spacing: .6px` | `b7fdde76251cc8ef.css M07` |
| `.daily-prev-next a,.daily-prev-next-link` | `min-height: var(--touch-target); display: inline-flex; align-items: center` | `b7fdde76251cc8ef.css M07` |
| `.daily-footer` | `margin-top: 36px; line-height: 1.6` | `b7fdde76251cc8ef.css M07` |
| `.daily-empty` | `padding: 80px 0; font-size: 14px` | `b7fdde76251cc8ef.css M07` |
| `.daily-empty-title` | `font-size: 22px` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-title` | `font-size: 26px; text-align: left` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-subtitle` | `text-align: left; margin-bottom: 36px; letter-spacing: 3px` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-row` | `display: grid; grid-template-columns: 1fr auto; gap: 8px 12px; padding: 18px 0` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-row:hover` | `padding-left: 0` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-date` | `min-width: 0; grid-column: 1/-1` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-headline` | `min-width: 0; font-size: 16px` | `b7fdde76251cc8ef.css M07` |
| `.daily-index-events` | `align-self: end` | `b7fdde76251cc8ef.css M07` |

##### `b7fdde76251cc8ef.css` `M08`（6 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.period-lead` | `padding: 16px 18px; margin-bottom: 28px` | `b7fdde76251cc8ef.css M08` |
| `.period-stats` | `margin-bottom: 36px` | `b7fdde76251cc8ef.css M08` |
| `.period-stat` | `flex-basis: 50%; padding: 12px 10px` | `b7fdde76251cc8ef.css M08` |
| `.period-stat+.period-stat` | `border-left: none` | `b7fdde76251cc8ef.css M08` |
| `.period-stat:nth-child(2n)` | `border-left: 1px solid var(--d-rule)` | `b7fdde76251cc8ef.css M08` |
| `.period-stat:nth-child(n+3)` | `border-top: 1px solid var(--d-rule)` | `b7fdde76251cc8ef.css M08` |

##### `b7fdde76251cc8ef.css` `M09`（3 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.period-stories` | `padding: 6px 16px` | `b7fdde76251cc8ef.css M09` |
| `.period-story` | `flex-wrap: wrap; gap: 4px 12px; padding: 12px 0` | `b7fdde76251cc8ef.css M09` |
| `.period-story-title` | `flex-basis: 100%; font-size: 15px` | `b7fdde76251cc8ef.css M09` |

##### `b7fdde76251cc8ef.css` `M11`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-endcard` | `margin-top: 48px; padding: 14px 16px` | `b7fdde76251cc8ef.css M11` |

##### `b7fdde76251cc8ef.css` `M13`（3 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.reader-toc` | `padding: 14px 16px` | `b7fdde76251cc8ef.css M13` |
| `.reader-toc-label` | `font-size: 13.5px` | `b7fdde76251cc8ef.css M13` |
| `.reader-toc-sub` | `font-size: 12.5px` | `b7fdde76251cc8ef.css M13` |


#### C.1.3 641–960px — 1 blocks / 1 rules

##### `9b65374e8a4754c4.css` `M06`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.m-tabbar` | `grid-template-columns: repeat(4,minmax(0,120px)); justify-content: center` | `9b65374e8a4754c4.css M06` |


#### C.1.4 ≤1200px — 1 blocks / 2 rules

##### `0e23a4c20d977d43.css` `M09`（2 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.source-overview` | `grid-template-columns: repeat(3,minmax(0,1fr))` | `0e23a4c20d977d43.css M09` |
| `.source-map__grid` | `grid-template-columns: 1fr` | `0e23a4c20d977d43.css M09` |


#### C.1.5 hover:none 或 ≤960px（逗号是 OR） — 1 blocks / 3 rules

##### `0e23a4c20d977d43.css` `M02`（3 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.local-starred-card:focus-within .local-starred-marks,.local-starred-card:hover .local-starred-marks` | `opacity: 1; transform: none` | `0e23a4c20d977d43.css M02` |
| `.local-starred-card:focus-within,.local-starred-card:hover` | `box-shadow: none; transform: none` | `0e23a4c20d977d43.css M02` |
| `.local-starred-remove` | `opacity: 1; pointer-events: auto; transform: none; box-shadow: none` | `0e23a4c20d977d43.css M02` |


#### C.1.6 prefers-reduced-motion: reduce — 6 blocks / 6 rules

##### `0e23a4c20d977d43.css` `M11`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.feed-skel` | `animation: none` | `0e23a4c20d977d43.css M11` |

##### `0e23a4c20d977d43.css` `M12`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.dt-processing-spinner` | `animation: none` | `0e23a4c20d977d43.css M12` |

##### `9b65374e8a4754c4.css` `M08`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.m-skel` | `animation: none` | `9b65374e8a4754c4.css M08` |

##### `9b65374e8a4754c4.css` `M09`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.m-poster` | `animation: none` | `9b65374e8a4754c4.css M09` |

##### `b7fdde76251cc8ef.css` `M01`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `html:has(.daily-shell)` | `scroll-behavior: auto` | `b7fdde76251cc8ef.css M01` |

##### `b7fdde76251cc8ef.css` `M03`（1 rules）

| selector | 此断点新增/覆盖声明 | bundle/block |
|---|---|---|
| `.daily-skel` | `animation: none` | `b7fdde76251cc8ef.css M03` |

### C 覆盖计数

- 独立 `@media` 块：38。
- 媒体查询内编译规则：345。
- 查询形态：6/6，正好是题目列出的六组；没有发现其它 media query。

## C.2 `/hot` bundle 的响应式（Phase 0.0 增量）

`cdf657f8b4e0d826.css` 共 3 个 `@media` 块，均为 `max-width` 形态、落在已有的 640/960 两个断点上，未引入新断点。第 3 块只含 `.event-digest` / `.event-digest-note`（story 详情页，我方不实现），故下面只列含 `.hot-*` 的 2 块。

### C.2.1 `@media (max-width:960px)` — 2 条含 `.hot-*` 的规则

| selector | 此断点新增/覆盖声明 | 说明 |
|---|---|---|
| `.event-page,.hot-page` | `width: min(100% - 36px, 760px); padding-top: 28px; padding-bottom: 88px` | 底部 88px 为底部 tab 栏（`--m-tabbar-h: 54px`）让位，与 C.0 的移动壳一致 |
| `.hot-rank-grid` | `grid-template-columns: 1fr` | 桌面已是单列，这里是显式收敛 |

### C.2.2 `@media (max-width:640px)` — 10 条含 `.hot-*` 的规则

| selector | 此断点新增/覆盖声明 | 说明 |
|---|---|---|
| `.event-page,.hot-page` | `width: calc(100% - 28px)` | 边距从 36px 收到 28px |
| `.event-hero h1,.hot-hero h1` | `font-size: 2rem` | 关掉 `clamp()` 的流体上限 |
| `.event-heat-chart,.event-related-list,.event-report-row,.event-section-head,.hot-rank-head,.hot-rank-row` | `padding-left: 16px; padding-right: 16px` | 横向内边距从 20/22px 收到 16px |
| `.hot-rank-row` | `display: grid; grid-template-columns: 28px 64px minmax(0,1fr); grid-template-areas: "number content content" ". spark sources"; align-items: start; column-gap: 10px; row-gap: 10px` | **从 flex 单行改为两行 grid**——名次与标题占第一行，火花线与信源数落第二行。我方不实现 spark，第二行只剩信源数，需自行决定是回落单行还是保留两行（plan 未固定，属实现取舍） |
| `.hot-rank-number` | `grid-area: number` | — |
| `.hot-rank-content` | `grid-area: content` | — |
| `.hot-rank-spark` | `grid-area: spark; width: 64px; align-self: center` | GAP-57 不实现 |
| `.hot-rank-sources` | `grid-area: sources; min-width: 0; justify-self: end` | — |
| `.hot-rank-sources>summary` | `flex-direction: row; align-items: baseline; justify-content: flex-end; gap: 5px` | 竖排改横排 |
| `.hot-rank-sources-count` | `font-size: var(--text-size-lg)` | `1.125rem`，从 `--text-size-xl` 降一档 |

块内其余声明属 `.event-*`，不在我方范围。
