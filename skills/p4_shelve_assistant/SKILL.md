# P4 Shelve Assistant

Use this skill when the user wants to inspect P4 status, manage Unity UI emoji / character animation import results, create a changelist, reconcile managed UI asset paths, shelve a changelist, or generate a Feishu-ready report.

Do not use this skill when the user asks to submit, merge/copy into main or trunk, create streams, process non-UI managed directories, bypass the allowlist, or save a P4 password. Refuse submit-like requests with: "当前 P4 助手是 Shelve-only 模式，不支持 submit。请使用 shelve 并把 changelist ID / shelf ID 交给负责人 review。"

Default safety boundaries:

- Shelve-only.
- No submit.
- Managed paths only:
  - `Assets/Art/UI/SpritesAnim/Emoji/...`
  - `Assets/Art/UI/SpritesAnim/CharacterAnim/...`
- No password storage.
- Delete requires explicit `--allow-delete`.

Recommended commands:

```bash
python -m tools.p4_assistant.cli status --workspace spark_client_ui
python -m tools.p4_assistant.cli check --workspace spark_client_ui
python -m tools.p4_assistant.cli preview --workspace spark_client_ui
python -m tools.p4_assistant.cli create-cl --workspace spark_client_ui --desc "..."
python -m tools.p4_assistant.cli reconcile --workspace spark_client_ui --cl <CL>
python -m tools.p4_assistant.cli shelve --workspace spark_client_ui --cl <CL>
python -m tools.p4_assistant.cli report --workspace spark_client_ui --cl <CL>
```

Agent reply templates:

- After preview: summarize add/edit/delete/move counts, list notable files, and say no P4 state was changed.
- After shelve: provide CL, shelf ID, stats, and paste the generated Feishu report text.
- If delete appears: stop unless the user explicitly confirms `--allow-delete`; list delete files clearly.
- If outside allowlist appears: block the operation and tell the user which files must be removed or handled separately.
- If login is missing: ask the user to run `p4 login`; never ask for or store the password.
