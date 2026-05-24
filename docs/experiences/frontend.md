# Frontend 经验

> Append-only. 前端开发相关的坑点和 pattern.

## 2026-05-24 日期分组与时间显示的时区必须一致

- Problem: 前端 date bucket 分组使用 `Asia/Shanghai` 时区，但条目的可见时间（`HH:mm`）使用浏览器本地时区渲染。当用户不在 `Asia/Shanghai` 时区时，条目的显示时间与所在的日期分组不一致（例如一条 23:50 CST 的条目在 UTC 时区显示为次日，但仍在前一天的分组里）。
- Solution: `web/static/app.js` 中的 `timeKey()` 格式化时统一使用 `Asia/Shanghai` 时区，使渲染时间与日期分组对齐。
- Applies when: 修改前端日期/时间显示逻辑时——所有时间格式化必须使用与分组相同的时区（`Asia/Shanghai`），不能依赖浏览器默认时区。
