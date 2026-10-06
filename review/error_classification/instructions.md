# Labeller and resolver instructions

Each packet file is `ITEM <id>`, the labeller instructions, the packet and the output form, as below. A pass-3 file adds the resolver note and the two labels before the output form.

## Labeller instructions

```
You are labelling one wrong answer of an automated data-query system. The system answered the
question below over one table; its answer was checked against the gold answer and found wrong, and
the system gave no sign that anything had gone wrong. Decide where the error came from, using the
rubric below and nothing but what is in this file.

RUBRIC. For each error, first write down the specification the system evidently intended: its
output columns, filters, grouping and derived values, as shown by its own choices, comments or
reasoning. Then:
- SPECIFICATION: correctly executing that intended specification would reproduce the wrong answer.
  The system misread the question, the schema or a term (for example: wrong column, wrong status
  set, literal reading of "active", substring instead of exact match, wrong sort direction).
- PROCEDURE: correctly executing that intended specification would give the gold answer. The steps
  failed to implement the intent (for example: wrong order of operations, a dropped or duplicated
  step, a filter applied after aggregation, a variable mix-up, a pandas or type slip).
- SCORING: the answer is right in substance but was scored wrong (shape, dict or list form, an
  identifier the question did not ask for, a tie).
- UNCLEAR: the intent cannot be determined from the trace.
Quote the evidence for every label from the trace.

Two further rules:
- If the intended specification is itself wrong AND the steps also fail to implement it, correct
  steps could still not give the gold answer: label it SPECIFICATION and set "also_procedure": true.
- The wrong cells are repetitions of the same question. Cells with an identical trace and output
  are shown once, as one cell group with their seeds. Label every cell group.
```

## Output form

Write your reasoning first. Then end your answer with one JSON object, alone in a ~~~json fence,
in exactly this form (one entry in "cells" per cell group, in the order shown):

~~~json
{
  "item": "<the item id at the top of this file>",
  "intended_specification": "<output columns, filters, grouping, derived values, order/limit>",
  "cells": [
    {"group": 1, "label": "SPECIFICATION | PROCEDURE | SCORING | UNCLEAR", "also_procedure": false,
     "evidence": "<a quotation from the trace, in quotation marks, and why it shows the label>"}
  ],
  "notes": "<anything else; may be empty>"
}
~~~

## Resolver note (pass 3)

```
Two labellers labelled this wrong answer independently, with the rubric below, and disagreed. You
decide. Read the packet, judge it yourself under the rubric, and weigh both labellers' readings;
you may take either label, or another one if the trace supports it. Quote your own evidence from the
trace.
```

## The prompt each sub-agent received

```
Labelling task <item>. Read exactly one file, <path>, with the Read tool. It holds everything you need: the instructions, the rubric, the system's design, the question, the gold answer, the schema briefing, and the recorded trace and output of each wrong cell. Do not read, list or search any other file, and use no other tool. Follow the instructions in the file and end your answer with the JSON object it asks for.
```
