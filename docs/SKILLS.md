# Skill Gateway — Skill Reference

All skills require `X-Skill-Token` header. Call via `POST /skills/v1/call`.

## Implemented Skills

### batch.create
- **Purpose**: Create a new batch matting job from an input directory
- **Parameters**: `input_dir`, `output_dir`, `workflow_type` (default: matting_v1), `notify_chat_id?`, `note?`
- **Returns**: `batch_id`, `total_count`, `status`
- **Danger**: medium — creates tasks and modifies queue
- **Confirmation**: not required

### batch.start
- **Purpose**: Change batch status from CREATED to RUNNING so the worker picks up tasks
- **Parameters**: `batch_id`
- **Returns**: `batch_id`, `status`, `started_at`
- **Danger**: low

### batch.status
- **Purpose**: Get full batch details including progress counters
- **Parameters**: `batch_id`
- **Returns**: full Batch model dump
- **Danger**: low (read-only)

### batch.list
- **Purpose**: List recent batches
- **Parameters**: `limit?` (default 10), `status?`
- **Returns**: `batches[]`, `count`
- **Danger**: low (read-only)

### batch.cancel
- **Purpose**: Cancel all QUEUED tasks in a batch; RUNNING tasks complete naturally
- **Parameters**: `batch_id`
- **Returns**: `batch_id`, `status`, `canceled_at`
- **Danger**: medium — irreversible for queued tasks

### queue.status
- **Purpose**: Global queue statistics
- **Parameters**: none
- **Returns**: `running_batches`, `queued_tasks`, `running_tasks`, `failed_tasks`
- **Danger**: low

### task.status
- **Purpose**: Get details of a specific task
- **Parameters**: `task_id`
- **Returns**: full Task model dump
- **Danger**: low

### task.list_failed
- **Purpose**: List failed tasks, optionally filtered by batch
- **Parameters**: `batch_id?`, `limit?` (default 20)
- **Returns**: `failed_tasks[]`, `count`
- **Danger**: low

### worker.status
- **Purpose**: Worker activity summary
- **Parameters**: none
- **Returns**: `worker_id`, `active_workers`, `running_tasks`, `queued_tasks`
- **Danger**: low

### comfyui.status
- **Purpose**: Check ComfyUI availability (fake mode returns "fake_online")
- **Parameters**: none
- **Returns**: `status`, `fake_mode`, `url`
- **Danger**: low

### file.list_allowed
- **Purpose**: List files/dirs under an allowed root (metadata only, never content)
- **Parameters**: `path`, `max_items?` (default 100, max 500)
- **Returns**: `path`, `entries[]` with `name/size/mtime/is_dir/suffix`
- **Danger**: low
- **Security**: Path must be under ALLOWED_ROOTS; DENY_PATH_PATTERNS always blocked

### log.tail
- **Purpose**: Read last N lines of gateway or worker log
- **Parameters**: `log_name` (gateway/worker/app), `lines?` (default 50, max 200)
- **Returns**: `log_name`, `lines[]`, `total_lines`, `returned_lines`
- **Danger**: low
- **Security**: Only reads from `logs/` directory

---

## Planned Skills (not yet implemented)

| Skill | Description | Danger |
|-------|-------------|--------|
| `frame.extract` | Extract frames from video files | medium |
| `model3d.generate` | Generate 3D model from images | high |
| `texture.apply` | Apply texture to a 3D asset | medium |
| `workflow.run` | Run arbitrary ComfyUI workflow by name | high |
