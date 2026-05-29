# Animation Automation Plan — Future Skills Roadmap

## Overview

Full pipeline for game animation asset production on Win3090.

## Pipeline Steps

1. **方案策划** — animation brief, frame list, table-driven config
2. **video.download_or_import** — import source animation video
3. **video.extract_frames** — extract image sequence at target FPS
4. **frames.rename_from_table** — rename frames per spreadsheet (e.g., 001_idle_01.png)
5. **frames.delete_bad_frames** — remove blurry/rejected frames
6. **frames.dedupe_similar** — remove near-duplicate frames
7. **matting.batch (= batch.create + batch.start)** — background removal
8. **noise.cleanup** — post-process noise and edge artefacts
9. **image.package_review** — package for QA review
10. **asset.import_engine** — import to game engine (Unity/UE)
11. **animation.state_machine.create** — create animator state machine
12. **animation.kframe.create** — set keyframes from frame sequence
13. **qa.review_effects** — QA review of final effects
14. **resource.cleanup** — delete intermediate files after approval
15. **p4.submit** — submit to Perforce version control

## Skill Status

| Step | Skill | Status |
|------|-------|--------|
| Import video | video.download_or_import | 🔲 Planned |
| Extract frames | video.extract_frames | 🔲 Planned |
| Rename frames | frames.rename_from_table | 🔲 Planned |
| Delete bad | frames.delete_bad_frames | 🔲 Planned |
| Dedupe | frames.dedupe_similar | 🔲 Planned |
| Matting | batch.create / batch.start | ✅ Implemented |
| Noise cleanup | noise.cleanup | 🔲 Planned |
| Package | image.package_review | 🔲 Planned |
| Import engine | asset.import_engine | 🔲 Planned |
| State machine | animation.state_machine.create | 🔲 Planned |
| Keyframes | animation.kframe.create | 🔲 Planned |
| QA | qa.review_effects | 🔲 Planned |
| Cleanup | resource.cleanup | 🔲 Planned |
| P4 submit | p4.submit | 🔲 Planned |

## Notes for AI Brain

- The AI brain orchestrates this pipeline via skills — it does NOT write code or run scripts
- Each step is a registered skill that the brain calls in sequence
- User approval required before irreversible steps (delete, submit)
- Full automation possible once all skills are implemented
