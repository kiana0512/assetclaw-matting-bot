# Batch Matting SOP

## Trigger

User says: "帮我抠图 / 批量抠图 / remove background / matting"

## Required Information

Before calling any skill, confirm:
- `input_dir` — where are the source images?
- `output_dir` — where to save results? (can default to `_matted` suffix dir)

If either is missing, ask. Don't guess silently.

## Standard Flow

```
1. file.list_allowed(input_dir)     → verify images exist
2. batch.create(input_dir, output_dir, workflow_type="matting_v1")
3. batch.start(batch_id)
4. queue.status()                    → show current load
5. [wait, then check]
6. batch.status(batch_id)            → report progress
7. [on completion] batch.status      → final summary
```

## On Failure

```
task.list_failed(batch_id)   → what failed?
log.tail(log_name="worker")  → why?
comfyui.status()             → is ComfyUI up?
```

## Safety Rules

- Never overwrite existing output_dir contents without confirmation
- Never cancel mid-run without user confirmation
- Always report: succeeded / failed / total
- Remind user to check output_dir when done
