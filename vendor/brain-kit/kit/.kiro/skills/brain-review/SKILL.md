---
name: brain-review
description: Use after the person answered a review sheet (审批单) - when your context has a "[brain-kit] 审批单" line - to change what they sent back, record the decisions and corrections, and request the next round. Also before showing a round-2 sheet.
---
# Answering a review sheet

The `[brain-kit] 审批单` lines in your context (one or two) are what the prompt hook recorded from the person's own
message. They are the record. Repeat them at the start of your reply as they are; do not restate them differently,
add items or drop any. What the second line names as not recorded (a question, a line that did not start with an id)
is not a decision.

## Every item marked 要改
1. Change the page so it says what the person asked. A finding they upheld (`F-n 对`) names its items: change the
   item or items that make the finding true. You may leave one of them as it is when changing another is enough; the
   next sheet shows it as unchanged and the person decides again.
2. Add a `decisions.md` row for each real decision the change makes. Who: `owner` when the person's line decided it
   (quote their words in Why), `agent` when you chose how. A row that replaces an old one says `取代 D-n` in its
   Decision. Never rewrite an old row.
3. Write one `brain-proposal` block, `type: correction`, for every item you changed, with the item id in `src`
   (`src: A-3`) (skill `brain-reflect`): what was wrong and what is right. A decision replaced by a new row counts as
   changed. An item you kept, or left because changing another was enough, gets no correction.

## When you think an item should stay
Keep the page as it is and add a `decisions.md` row that names the item and says why (`保留 D-19：…`, Who `agent`).
The next sheet shows the item as 没改 next to your row, and the person decides again. Do not argue in chat instead.

## Questions and unclear lines
A line the hook names as a question or unclear: answer or ask in chat before you change anything. A line that was not
recorded (the id was not first, or the words after it are none of the sheet's forms): ask the person to send it
again in the sheet's form, `A-3 改：原因` or `A-3 可以`.
Never type sheet lines yourself, also not as an example the person could take for theirs.

## Then
Run `lean stage done` again for each stage whose page changed (Lean marks it "edited since"), then
`lean request <KIND>` and `brain review`. Round 2 shows only what changed, what became of every line the person sent
back, and new findings. Show it as you showed the first sheet, and stop.

## When the person approves
Lean records it ("Recorded: INCEPTION approved …"). If the same message also sent items back, the hook's line says
so: the approval stands in Lean, but changing those items voids it. Tell the person, make the change, and request
again.
