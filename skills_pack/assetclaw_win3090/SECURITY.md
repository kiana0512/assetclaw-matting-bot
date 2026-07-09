# Security

模型必须遵守：

- 只能调用 skills。
- 不能请求 shell。
- 不能删除文件。
- 不能读取 `.env`、`.ssh`、token、secret、key。
- 不能编造执行结果。
- 路径被 deny 时要解释原因，不要绕过。
