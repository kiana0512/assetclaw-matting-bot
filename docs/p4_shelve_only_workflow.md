# P4 Shelve-only Workflow For Unity UI Emoji Assets

This workflow is for Unity UI emoji / character animation imports under:

- `Assets/Art/UI/SpritesAnim/Emoji/...`
- `Assets/Art/UI/SpritesAnim/CharacterAnim/...`

The assistant is Shelve-only. It creates pending changelists and shelves them for review. It does not submit.

## Flow

1. Unity plugin imports JSON and frames.
2. Artist or TA reviews / keyframes / checks animation.
3. Run `preview` to see P4 reconcile results.
4. Create a changelist with a clear import description.
5. Reconcile only the managed UI paths into that changelist.
6. Shelve the changelist.
7. Generate the Feishu report and share CL / shelf / file summary.
8. Teammates review, unshelve, or modify.

## Safety Rules

- Submit is disabled.
- Main workspace is allowed for status, check, preview, create changelist, reconcile managed paths, shelve, and report.
- Merge, copy, stream creation, submit, and white-list bypasses are forbidden.
- Delete is blocked unless `--allow-delete` is passed.
- `.meta` warnings require human review.
- Passwords are never saved in YAML, README, examples, or reports.

## Manual Verification

```bash
python -m tools.p4_assistant.cli status --workspace spark_client_ui
python -m tools.p4_assistant.cli check --workspace spark_client_ui
python -m tools.p4_assistant.cli preview --workspace spark_client_ui
python -m tools.p4_assistant.cli create-cl --workspace spark_client_ui --desc "[UI Emoji Import] creamy/danny animations"
python -m tools.p4_assistant.cli reconcile --workspace spark_client_ui --cl <CL>
python -m tools.p4_assistant.cli shelve --workspace spark_client_ui --cl <CL>
python -m tools.p4_assistant.cli report --workspace spark_client_ui --cl <CL>
```
