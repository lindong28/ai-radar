# ADR-055: 新访客默认主题改为"跟随系统"

- Status: Accepted
- Date: 2026-08-17
- Supersedes: `6724dd6 feat(web): default to dark theme for new visitors` 的默认档部分

## Context

2026-08-17 与 aihot.virxact.com 做同条件成对对比时的实测：同一个可见 Chrome、`localStorage` 为空、`matchMedia('(prefers-color-scheme: dark)').matches === false`（系统为浅色），news.aiplanet.live 仍渲染 `data-theme=dark`，侧栏高亮"深色主题"；aihot 高亮"跟随系统"并渲染浅色。ai-radar 的浅色皮肤本身完备（手动切换后截图核对过）。

`6724dd6` 把新访客默认从浅色改成深色是一次**明确决策**，不是遗漏；本 ADR 只改默认档，不否定该 commit 的其余部分（尤其是它记载的那条教训：默认值同时存在于内联 FOUC 脚本与 `app.js`，只改一处会先闪一个主题再跳到另一个）。

## Decision

新访客（无合法存储值）默认档为 `system`。默认值与容错契约必须在**两条路径**上一致：

| 面 | 改动 |
|---|---|
| `web/static/app.js` | `themePreference()` 兜底 `"dark"` → `"system"` |
| 14 个公共 HTML 的内联 FOUC 脚本 | `localStorage.getItem(...)||"dark"` → 取值校验后落 `"system"` |
| 容错：存储读取 | 单独 try/catch，异常等同"无存储值"；另有会话内 fallback 变量，使存储不可用时切换仍即时生效 |
| 容错：`matchMedia` | 调用前判 `typeof ... === "function"`，不可用时常量兜底**深色**（两条路径同一兜底值）；监听注册同样先判 |
| `theme-color` meta 静态初值 | 保留 `#10151c`，语义改为"内联脚本执行前的极短兜底色"，不表达默认主题；内联脚本在首绘前按 `matchMedia` 覆写 |

已存的 `light` / `dark` / `system` 偏好不受影响。

## 为什么容错必须两侧一致

评审对原函数做过运行时对照：正常环境 `PASS theme=light`；`localStorage.getItem` 抛错时 `THROW Error`；缺 `matchMedia` 时 `THROW TypeError`。所有公共页面随后都会调用 `initThemeToggle()`，因此**只加固内联脚本会留下一条 hydration 阶段仍然抛错的路径**，形成两条不一致的容错契约。

## 验证证据

worktree 本地实例（`127.0.0.1:8793`，空 DB），真实 Chrome，两种 `prefers-color-scheme` 各测两组：

```
hydration 后：      system=light -> theme=light, mode=system, theme-color=#f4f5f6, storage=null
                    system=dark  -> theme=dark,  mode=system, theme-color=#10151c, storage=null
仅内联（app.js 被 abort，hydration 从未发生）：
                    system=light -> theme=light, mode=system, theme-color=#f4f5f6
                    system=dark  -> theme=dark,  mode=system, theme-color=#10151c
```

第二组用 `network route "**/app.js*" --abort` 隔离 hydration，证明内联脚本自身即把三项设对，而非"内联失败、被 hydration 补救"。两种系统偏好都测是为了让读数在结论为真与为假时不同——若默认仍是硬编码 dark，第一行会是 `theme=dark`。

## Scope

- 只覆盖现有 14 个公共 HTML 消费者与 `app.js` 的**主题路径**，不外扩为跨浏览器视觉对齐结论。
- **容错只覆盖主题路径**：`app.js` 中另有若干处 `window.matchMedia()` 调用（列表渲染的 `mobileFeed` 判定、响应式绑定等）未加保护。在没有 `matchMedia` 的浏览器上，主题会被正确定为深色，但随后的 hydration 仍会在那些调用点抛 `TypeError`。那是本决策之前就存在的独立问题，本 ADR 不声称整页容错，也不顺带加固——见 `docs/issues/ux-issues.md` 的相应条目。
- 未覆盖：浏览器缩放档、`/wechat` 内联 style 路径的主题表现、以及系统偏好在页面存活期间切换的过渡观感。
