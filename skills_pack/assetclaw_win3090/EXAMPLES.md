# Examples

用户：看看 E 盘有哪些文件

```json
{"tool_calls":[{"skill":"file.list_allowed","arguments":{"path":"E:\\","max_items":100}}]}
```

用户：把 README.md 复制到 storage\README_copy.md

```json
{"tool_calls":[{"skill":"file.copy","arguments":{"src_path":"E:\\assetclaw-matting-bot\\README.md","dst_path":"E:\\assetclaw-matting-bot\\storage\\README_copy.md","overwrite":false}}]}
```

用户：把 storage\README_copy.md 移动到 storage\README_moved.md

```json
{"tool_calls":[{"skill":"file.move","arguments":{"src_path":"E:\\assetclaw-matting-bot\\storage\\README_copy.md","dst_path":"E:\\assetclaw-matting-bot\\storage\\README_moved.md","overwrite":false}}]}
```

用户：用 batch_inputs 创建一个抠图批次

```json
{"tool_calls":[{"skill":"matting.batch_create","arguments":{"input_dir":"E:\\assetclaw-matting-bot\\storage\\batch_inputs","output_dir":"E:\\assetclaw-matting-bot\\storage\\batch_outputs"}}]}
```

