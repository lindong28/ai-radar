# AIHOT fit identity manifests

`eval-fit run` 与 `eval-fit judge` 在任何 provider 凭据读取和付费调用之前，调用 `~/.claude/bin/eval-identity`。完整 spec 与工具 readout 只放在 gitignored 的本地 run 目录；本目录保存 schema 约束后的非敏感 manifest，使身份前检收据不随 14 天 run 清理窗口消失。

每个 `<attempt_id>.json` 只能创建一次。它记录行为身份摘要、spec/readout 摘要、工具退出码与采信处置；不复制身份 spec 里的路径、自由文本 provenance 或配置值。
