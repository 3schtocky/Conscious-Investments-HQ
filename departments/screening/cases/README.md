# Screening cases

Past runs kept as test cases. They are never loaded into an agent's prompt, so no company here can colour a future screen.

## Known-winner check (point-in-time)
Run the Gems screen as of a past date and see whether names that later ran appear near the top. Used to judge changes to the criteria, not to find ideas.
- Eos Energy: should have ranked high ahead of its run.
- Rambus: same.
See `Equity Research` (`erb screen --preset gems --as-of ...`) and the Phase 5 notes for how the check was run and its survivorship caveats.

## How to add a case
One file per case: the date, the screen run, what Scout picked and dropped (with reasons), what happened next, and what the playbook should learn from it. When the lesson is general, put one company-free line into `../lessons.md`.
