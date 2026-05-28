# Security Model

## Authentication Tokens

| Token | Where used | Rotation |
|-------|-----------|---------|
| `WORKER_TOKEN` | Worker → Gateway HTTP calls (X-Worker-Token) | Change in .env |
| `SKILL_API_TOKEN` | OpenClaw → Skill Gateway (X-Skill-Token) | Change in .env |
| `FEISHU_APP_SECRET` | Gateway → Feishu OAuth | Feishu console |
| `OPENCLAW_API_KEY` | Gateway → OpenClaw cloud | OpenClaw console |

**Never log any of these tokens.** The Feishu client, OpenClaw client, and skill auth
are all written to avoid printing secrets.

## Path Security

### Allowed Roots
`ALLOWED_ROOTS` (semicolon-separated) restricts where batch I/O dirs and skill file
operations can point. Any path outside these roots is rejected with HTTP 400 / ValueError.

Set restrictively:
```
ALLOWED_ROOTS=E:\assetclaw-matting-bot;E:\batch_inputs;E:\batch_outputs
```

### Deny Path Patterns
`DENY_PATH_PATTERNS` blocks paths containing any of these substrings (case-insensitive):
```
.ssh;.env;AppData;Windows;Program Files
```

Always include `.env` to prevent reading credential files.

### Path Traversal
`validate_skill_path()` rejects any path containing `..` components before
allowing access. Even if the final resolved path is under an allowed root,
raw `..` patterns are blocked.

## Skill Execution Constraints

All of these are hard-coded and cannot be overridden by config or API:

| Capability | Status |
|-----------|--------|
| Shell command execution | ❌ Never allowed |
| File deletion | ❌ Never allowed |
| File content reading | ❌ Disabled by default (`ALLOW_FILE_READ_CONTENT=false`) |
| Local LLM inference on GPU | ❌ `AGENT_RUNS_ON_GPU` must always be `false` |
| Writing outside ALLOWED_ROOTS | ❌ Blocked |
| Reading secrets (.env, .ssh) | ❌ Blocked by deny patterns |

## Audit Logging

Every `/skills/v1/call` request is written to the `skill_calls` table:
- request_id
- skill name
- arguments (JSON)
- result or error
- ok (0/1)
- requested_by (api / openclaw / feishu / ...)
- created_at

Query recent calls:
```
GET /skills/v1/calls  X-Skill-Token: your_token
```

## Dangerous Operation Confirmation

When `SKILL_REQUIRE_CONFIRMATION_FOR_DANGEROUS=true`, future high-danger skills
(model3d.generate, workflow.run) will return `requires_confirmation: true` instead
of executing immediately. The caller must re-submit with an explicit confirmation flag.

Currently this only applies to unimplemented skills. batch.cancel is medium-danger
and executes directly.

## Recommendations for Production

1. Change `WORKER_TOKEN` and `SKILL_API_TOKEN` from defaults before any deployment.
2. Set `ALLOWED_ROOTS` to the minimum required set of directories.
3. Set `DENY_PATH_PATTERNS` to include at minimum: `.ssh;.env;AppData;Windows`.
4. Use HTTPS (cloudflared or nginx) — never expose the gateway on raw HTTP to the internet.
5. Rotate `OPENCLAW_API_KEY` regularly.
6. Monitor `skill_calls` table for unexpected patterns.
