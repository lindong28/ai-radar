# 20260916-d362：已确认 outbox 清理遇采集写锁时延后

- Status: accepted
- Date: 2026-09-16
- Decision review: 独立 Codex 单轮 L1，七项成立，放行；实现与生产效果另验。

## Context

用户已批准「启用并验证」独立采集。首轮真实输入完整落盘，但主库确认后删除队列副本遭遇采集写锁，ingest 返回 database is locked。主库保留 1 个 ack，队列副本未删。隔离回归以真实 SQLite BEGIN IMMEDIATE 复现，失败位置为 _discard 中的 DELETE；这不是数据丢失，但会令已获授权的处理链不必要地退出。

## Decision

仅在主库 items/runtime/ack 事务成功提交之后的清理操作捕获错误码 SQLITE_BUSY；首次遇到后本轮停止清理尝试，继续导入原定批次。未删除 payload 保留，下轮凭 ack 幂等清理。主提交、身份或哈希校验、其他 I/O 错误仍失败，不按报错文案分类。

不扩大等待预算、不取 collector 锁阻塞抓取、不删除未确认数据。本项细化 [e3a8](20260916-e3a8-decouple-collection-from-processing.md) 的确认后清理和失败重试，不改变原始归档、AI 或调度。代价是已确认副本暂时占用队列空间；pending_batches 是尚未清理的队列记录数，可能包含已确认项，不能一律解释成未导入新闻。

## Verification boundary

行动前已取得旧代码失败读数；修正后须验证主库完成全部 batch、队列在锁占用时保留、释放后仅清理而不重复应用，并保留非 SQLITE_BUSY 错误失败路径。真实长期增长和吞吐尚未测量。

修正后 `test_ingestion.py` 与 `test_ingestion_scheduler.py` 共 24 项通过，含真实 SQLite 写锁持有/释放以及其他错误失败路径；ruff 与本模块 mypy 通过。独立实现增量审查通过，审查者另跑 5 项定向用例通过，无遗留 findings。生产导入终态由 operations 记录。
