# P4 Shelve Assistant

Use this skill for the final Unity import P4 stage: status, workspace/stream inspection, switch-stream preview, get latest, reconcile preview, single-step changelist creation, single-step reconcile, single-step shelve, and Feishu-ready report.

The animation automation default stream is `//streams/rel_0.0.1`. Do not use `//streams/0.0.1`; that is not the current Spark Client stream.

## Red Lines

- Submit is disabled forever.
- Do not run merge/copy/integrate.
- Do not create streams.
- Do not save passwords, tokens, tickets, or P4PASSWD.
- Real `create-cl`, `reconcile`, `shelve`, and real stream switch require explicit user confirmation.
- Do not batch `create-cl`, `reconcile`, and `shelve` behind one confirmation.
- Tests must mock P4Runner for write operations.

If the user asks to submit, refuse with:

```text
当前 P4 助手是 shelve-only 模式，submit 是红线操作。请只生成 changelist / shelve / report，由负责人后续处理。
```

## Safe Read-Only Commands

```bash
python -m tools.p4_assistant.cli status --workspace spark_client
python -m tools.p4_assistant.cli workspace-info --workspace spark_client
python -m tools.p4_assistant.cli streams --workspace spark_client
python -m tools.p4_assistant.cli preview --workspace spark_client
python -m tools.p4_assistant.cli report --workspace spark_client --cl 123456
python -m tools.p4_assistant.cli switch-stream --workspace spark_client --stream "//streams/rel_0.0.1" --preview
python -m tools.p4_assistant.cli get-latest --workspace spark_client --scope managed --preview
```

## Commands That Need Confirmation

```bash
python -m tools.p4_assistant.cli create-cl --workspace spark_client --desc "..." --yes
python -m tools.p4_assistant.cli reconcile --workspace spark_client --cl 123456 --yes
python -m tools.p4_assistant.cli shelve --workspace spark_client --cl 123456 --yes
python -m tools.p4_assistant.cli switch-stream --workspace spark_client --stream "//streams/rel_0.0.1" --yes
python -m tools.p4_assistant.cli get-latest --workspace spark_client --scope all --yes
```

`get-latest --scope managed` only syncs configured managed paths. `--scope all` must be explicitly confirmed.

`shelve-ui-import` is plan-only: it may run check and preview, then prints the separate confirmed commands. It must not create a CL, reconcile, or shelve by itself.

## Managed Paths

Default managed paths include imported textures, animation clips, override controllers, and importer manifests:

- `Assets/Art/UI/SpritesAnim/Emoji/...`
- `Assets/Art/UI/SpritesAnim/CharacterAnim/...`
- `Assets/Art/UI/Animation/Emoji/...`
- `Assets/Art/Character/Animation/...`
- `Assets/Res/UI/Animator/EmojiOverride/...`
- `Assets/Res/Character/Animation/Override/...`
- `Assets/Modules/UepUtility/AnimTextureImporter/Editor/Manifests/...`

Forbidden paths include project settings, packages, plugins, editor folders, library/temp/user settings/logs. Any forbidden path blocks shelve.

## Report Template

Reports must say:

- `Submit: DISABLED`
- managed paths only status
- forbidden paths status
- delete warning/blocking
- files list
- optional Unity Ready summary when `--unity-ready-manifest` is provided
