---
description: Your tasks and requests found in Telegram; mark done, add notes
argument-hint: [mine | all | done N | note N text | add text]
---

Use the telegram skill. The user's request about their tasks:

$ARGUMENTS

With no request, run `tgkit tasks --json` and list the open tasks, the ones
for the user first, with who asked, in which chat, and any due date. To change
a task, run the matching `tgkit tasks done|undo|drop|note|add ...` command and
confirm what changed.
