# Natural Language → Skill Call Examples

## Example 1: Batch Matting

**User:** 帮我把 E:\assetclaw-matting-bot\storage\batch_inputs 里的图片抠图，输出到 batch_outputs。

**Tool Calls:**
```json
[
  {"skill":"batch.create","arguments":{"input_dir":"E:\\assetclaw-matting-bot\\storage\\batch_inputs","output_dir":"E:\\assetclaw-matting-bot\\storage\\batch_outputs","workflow_type":"matting_v1"}},
  {"skill":"batch.start","arguments":{"batch_id":"<returned_id>"}}
]
```
**Reply:** 批次已创建，共 N 张图。Worker 已开始处理。

---

## Example 2: Check Progress

**User:** 现在跑到哪了？

**Tool Calls:**
```json
[{"skill":"queue.status","arguments":{}}]
```
**Reply:** 队列：排队 X / 运行中 Y / 失败 Z

---

## Example 3: Full Progress Check

**User:** 帮我看 BATCH_ABC 批次进展。

**Tool Calls:**
```json
[{"skill":"batch.status","arguments":{"batch_id":"BATCH_ABC"}}]
```
**Reply:** 批次 BATCH_ABC：成功 42/50，失败 2，运行中 3

---

## Example 4: Investigate Failures

**User:** 帮我看失败原因。

**Tool Calls:**
```json
[
  {"skill":"task.list_failed","arguments":{"batch_id":"BATCH_ABC"}},
  {"skill":"log.tail","arguments":{"log_name":"worker","lines":50}},
  {"skill":"comfyui.status","arguments":{}}
]
```

---

## Example 5: Check What's in a Directory

**User:** E:\batch_inputs 里现在有什么图？

**Tool Calls:**
```json
[{"skill":"file.list_allowed","arguments":{"path":"E:\\batch_inputs","max_items":20}}]
```

---

## Example 6: Cancel a Batch

**User:** 取消 BATCH_ABC。

**AI should confirm first:**
"请确认：取消 BATCH_ABC 将停止所有排队任务（已在运行的将完成）。确认取消？"

After user confirms:
```json
[{"skill":"batch.cancel","arguments":{"batch_id":"BATCH_ABC"}}]
```

---

## Example 7: Full Status Check

**User:** 机器现在状态怎么样？

**Tool Calls:**
```json
[
  {"skill":"queue.status","arguments":{}},
  {"skill":"worker.status","arguments":{}},
  {"skill":"comfyui.status","arguments":{}}
]
```
