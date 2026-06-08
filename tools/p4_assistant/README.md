# P4 UI Shelve-only Assistant

This module manages Unity UI emoji import results in Perforce. It is intentionally **Shelve-only**:

- It can inspect status, create a pending changelist, reconcile allowed UI asset directories, shelve, and generate a Feishu-ready report.
- It never submits. `p4 submit` is disabled in code, CLI, docs, and natural-language intent handling.
- It does not store passwords. Use `p4 login`, `p4 login -s`, P4V, or existing Perforce tickets.

## Why Changelist And Shelve

Unity imports JSON + frames into assets and `.meta` files. A pending changelist groups those related files so the team can review the exact import result. Shelving uploads the changelist content for review / unshelve / further edits without submitting to main.

Main workspaces are allowed for Shelve-only operations. Submit, merge, copy, stream creation, and operations outside the managed UI paths remain forbidden.

## Standard Flow

```text
Unity plugin imports JSON + frames
Manual keyframe / visual check
p4 assistant preview
create changelist
reconcile
shelve
report
Feishu sync CL / shelf / files
Team review / unshelve / modify
```

## Config

Copy `workspaces.example.yaml` to `workspaces.local.yaml` and edit local values. `workspaces.local.yaml` is git-ignored.

Allowed fields:

```yaml
workspaces:
  spark_client_ui:
    p4port: spark-p4.lilithgames.com:1666
    p4user: keizhang
    p4client: keizhang_L-20260528ZLGJA_8024
    root: D:/Spark/Client
    mode: shelve_only
    managed_paths:
      - Assets/Art/UI/SpritesAnim/Emoji/...
      - Assets/Art/UI/SpritesAnim/CharacterAnim/...
    forbidden_paths:
      - ProjectSettings/...
      - Assets/AddressableAssetsData/...
      - Packages/...
      - Library/...
      - Temp/...
```

Do not add `password`, `passwd`, or `P4PASSWD`. If these fields appear, the tool warns and ignores them.

## Commands

```bash
python -m tools.p4_assistant.cli status --workspace spark_client_ui
python -m tools.p4_assistant.cli check --workspace spark_client_ui
python -m tools.p4_assistant.cli preview --workspace spark_client_ui
python -m tools.p4_assistant.cli create-cl --workspace spark_client_ui --desc "[UI Emoji Import] creamy/danny animations"
python -m tools.p4_assistant.cli reconcile --workspace spark_client_ui --cl 123456
python -m tools.p4_assistant.cli shelve --workspace spark_client_ui --cl 123456
python -m tools.p4_assistant.cli report --workspace spark_client_ui --cl 123456
```

Optional one-shot:

```bash
python -m tools.p4_assistant.cli shelve-ui-import --workspace spark_client_ui --desc "[UI Emoji Import] creamy/danny animations" --yes
```

Without `--yes`, one-shot stops after `check` and `preview`.

Use `--allow-delete` only after deliberately reviewing delete files. Use `--force` on `shelve` when a shelf already exists and you intend to replace it.

## Safety Boundaries

Managed paths:

- `Assets/Art/UI/SpritesAnim/Emoji/...`
- `Assets/Art/UI/SpritesAnim/CharacterAnim/...`

Forbidden examples:

- `ProjectSettings/...`
- `Assets/ProjectSettings/...`
- `Assets/AddressableAssetsData/...`
- `Assets/Art/Character/...`
- `Assets/Art/Scene/...`
- `Assets/Plugins/...`
- `Assets/Editor/...`
- `Packages/...`
- `Library/...`
- `Temp/...`
- `UserSettings/...`
- `Logs/...`

If opened or shelved files include anything outside the managed paths, shelving is blocked. Delete actions are blocked by default. Unity `.meta` mismatches are warnings so humans can review them.

## Common Issues

- Not logged in: run `p4 login`, then retry. The tool will not ask for or save passwords.
- `p4` not found: install P4 CLI or add `p4.exe` to PATH.
- Workspace mismatch: check `P4CLIENT`, `root`, and `p4 client -o`.
- Outside managed path: move those files out of the changelist or use a different process.
- Delete detected: rerun only after reviewing, with `--allow-delete`.
- `.meta` warning: check Unity generated matching `.meta` files.
- Shelf exists: rerun `shelve` with `--force` if replacing the shelf is intended.
