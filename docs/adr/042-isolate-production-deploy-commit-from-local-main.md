# ADR-042：从本地 main 的未发布提交中隔离生产部署 commit

- 状态：Accepted
- 日期：2026-08-16
- 范围：`tencent` deployment remote 的下一次 `refs/heads/main` 更新；不改变本地 `main`、`origin` 或 EdgeOne/DNS 配置

## 背景

本次 `/wechat` 冷首屏优化已在 worktree commit `0c2e25a` 中完成，但它的父链比生产 deployment remote 多出 7 个不属于本任务的 commit。2026-08-16 的只读取证显示：`tencent/main`、服务器 `/home/ubuntu/ai-radar/.deployed-sha` 均为 `b242943`，部署 journal 为 `idle`；`git rev-list --left-right --count tencent/main...0c2e25a` 为 `0 8`。服务器 bare repo 的 `post-receive` 只消费 `refs/heads/main`，因此直接把 `0c2e25a` 推到该 ref 会同时发布 7 个未授权的 DB sync 文档与 LLM cost 系列 commit。

## 考虑过的方案

1. 直接把 `0c2e25a` 推到 `tencent/main`。否决：发布范围会夹带 7 个与当前性能任务无关的 commit。
2. 等其他流程先发布这 7 个 commit。否决：解除条件和时点不受本任务控制，会无限推迟当前线上目标。
3. 推送临时 ref 后手工调用 `deploy_code.py`，或直接修改服务器文件。否决：前者让生产 SHA 与 `main` 真相源分离，后者绕过现有原子 materialize、health check 与 rollback 链。
4. 在隔离 worktree 中以当前 `tencent/main` 为父节点，复放本任务 patch，形成单独的生产部署 commit。选择该方案。

## 决策

从 freshly fetched `tencent/main` 创建隔离 deployment worktree，把本任务的运行时与配套测试、文档 patch 复放为单个 commit `D`。`D` 只有在该基线上通过与原实现同构的聚焦测试、Ruff、diff/symlink 检查、review gate 和 patch 内容复核后，才成为 push 候选。任何 push 仍须先向用户说明 `D` 的完整 SHA、目标 ref 与 fast-forward 关系，并取得明确许可；禁止把原 `0c2e25a` 父链直接推到生产。

push 许可同时单独说明条件式恢复授权：若 post-receive/slot health 失败，或部署后真实公共入口在 HTML 约 120 秒缓存窗口后出现功能、视觉、缓存行为错误，或按 ADR-039 的最终性能验收协议——在用户 MacBook 可见浏览器中交替测试 news 与 AIHOT、每方至少 5 次真正冷连接并比较 median TTFB/FCP——确认冷访问性能相对部署前 EdgeOne 基线发生回归，则生成普通 `revert D` commit `R` 并 fast-forward push 到 `tencent/main`，禁止 force-push。用户未预先授权 `R` 时，只能先在本地生成候选，再另取 push 许可。若性能已经改善但仍未达到 AIHOT 110% 目标，不回滚已有收益，任务保持未完成并继续优化。

`D` 成功且公共验收无回归后，等当前本地 `main` 写入者提交或释放，再另行取得本地整合许可，把包含 `D` 的 `tencent/main` 合并回本地 `main`；不再合并 `0c2e25a` 分支，避免重复应用同一 patch。清账完成的机械判据是 `git merge-base --is-ancestor D main` 成功、整合后的聚焦测试通过且工作树干净。满足前不得清理原 worktree/branch，也不得把本次改动称为已整合本地 `main`。未来每次部署前都先 fetch，先用 `git merge-base --is-ancestor tencent/main candidate` 确认候选可 fast-forward，再逐条列出 `tencent/main..candidate`；任一检查失败或出现未授权 commit 时重新制作最小候选。

## 执行结果与剩余项

隔离生产 commit `D` 最终为 `aeeccfdf69f81051ac6131d9e60c8d427d58edbf`，直接以 `b242943eded59142b1de48c27a7dc5e010d86338` 为父节点，只包含本任务的 10 个文件。聚焦测试在该生产父链上通过 111 项，Ruff、symlink、diff、patch 内容复核与 review gate 均通过；用户授权后，`D` 已 fast-forward push 到 `tencent:refs/heads/main`，post-receive 完成部署，服务器 `.deployed-sha` 与公开入口均指向 `D`。缓存窗口后的公开路由、视觉、缓存边界和用户 MacBook 可见 Chrome 成对性能验收全部通过，因此没有触发普通 revert，也没有执行 EdgeOne/DNS 回切。

本地 `main` 的整合仍是独立待办：须等并发写入者释放，并按本 ADR 另取显式整合许可后，只整合包含 `D` 的生产历史；在此之前保留原 worktree 与分支，不把生产部署成功表述为本地 `main` 已完成清账。
