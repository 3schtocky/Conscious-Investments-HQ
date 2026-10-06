# Audit playbook: the final audit and the redo

When every department has finished its deliverables, the final audit checks all of it before it reaches {captain}.

## How a number is checked
1. **Code first.** Every figure in the deliverable is pulled out and matched to its source: the approved model, a filing, a file the office wrote. Matches pass without any model call.
2. **Exceptions only.** Figures that match nothing go to {audit_associate} to trace. {audit_associate} reports what the figure is, where it appears, and the nearest real source or none.
3. **Ruling.** {audit_lead} reads the exceptions and rules on each: cleared (the source exists and was missed), or upheld (it is wrong or unsourced).

Do not re-audit what code already matched. Spend your attention on exceptions.

## Sending work back
A failed deliverable is never redone from scratch. {audit_lead} sends the owner a list, one line per failed part:
- where it is (file, section, sentence),
- what is wrong, in a few words,
- the correct source or figure, when one exists.

The owner redoes only those parts in a fresh context, then Audit re-checks only those parts. Audit names the problem and the source; it does not write the replacement or touch the file.

## Limits
- A part gets at most two redos. If it still fails, file an incident with the evidence and leave the deliverable held for {captain}.
- If a redo changes anything outside the listed parts, check that too.
- A deliverable passes when every listed part passes. Say so in one line.
