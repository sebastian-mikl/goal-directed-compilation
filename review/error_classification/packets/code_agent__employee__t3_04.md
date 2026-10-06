ITEM item_23

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

SYSTEM DESIGN
A code agent. The model writes Python (pandas) code against the table, loaded as the DataFrame `df`, sees the output of each run, may run more code, and ends by giving its final answer. TRACE lists each code run with the output the model saw, and its last message; OUTPUT is the answer it returned.

QUESTION
What are the top 5 managers by number of direct reports?

GOLD ANSWER
5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}

ALSO CORRECT UNDER THE TIE RULE (other answers that tie with gold on the ranking key, checked against the data)
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1005", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1023", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1041", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1050", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1057", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1063", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1064", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}
- 5 row(s):
  {"Manager ID": "E1101", "count": 4.0}
  {"Manager ID": "E1073", "count": 3.0}
  {"Manager ID": "E1089", "count": 3.0}
  {"Manager ID": "E1108", "count": 3.0}
  {"Manager ID": "E1128", "count": 3.0}

SCHEMA BRIEFING (what the system was told about the table)
DOMAIN: An employee directory. Each row is an employee, with their department, role, employment status, work location, compensation, and tenure.

COLUMN MEANINGS (advisory):
  - 'Role': The employee's role band (e.g. IC, Manager, Director, VP).
  - 'Status': Employment status (e.g. Active, On Leave, Terminated, Sabbatical).
  - 'Annual Salary': Gross annual base salary.
  - 'Bonus Target': Target annual bonus amount for the employee.
  - 'Tenure (Years)': Number of years the employee has been with the organisation.
  - 'Performance Score': Most recent performance review rating, 1.0 (lowest) to 5.0 (highest).
  - 'Market Rate': External market benchmark salary for the employee's role.
  - 'Equity Grant Value': Current value of the employee's equity grant.
  - 'Time in Current Role (Years)': Number of years the employee has held their current role (always <= Tenure (Years)).
  - 'Manager ID': The Employee ID of this employee's line manager.

BUSINESS RULES (authoritative — these terms have a fixed meaning):
  - 'current': 'Status' in ['Active', 'On Leave', 'Sabbatical']

TABLE COLUMNS (verbatim):
'Employee ID', 'Name', 'Department', 'Role', 'Status', 'Location', 'Annual Salary', 'Bonus Target', 'Tenure (Years)', 'Manager ID', 'Performance Score', 'Market Rate', 'Equity Grant Value', 'Time in Current Role (Years)'

CELL GROUP 1 (seeds 0, 2; 2 wrong cell(s) with this trace and output)
TRACE:
--- run 1 (run_python) ---
# Count direct reports for each manager
# Manager ID column contains the employee ID of each person's manager
direct_reports_count = df['Manager ID'].value_counts().head(5)

# Get the manager details for these top 5
top_manager_ids = direct_reports_count.index.tolist()

# Create a result DataFrame with manager information
result_data = []
for manager_id in top_manager_ids:
    manager_info = df[df['Employee ID'] == manager_id]
    if not manager_info.empty:
        result_data.append({
            'Employee ID': manager_id,
            'Name': manager_info['Name'].values[0],
            'Department': manager_info['Department'].values[0],
            'Role': manager_info['Role'].values[0],
            'Direct Reports': direct_reports_count[manager_id]
        })
    else:
        # Manager might not be in the dataset
        result_data.append({
            'Employee ID': manager_id,
            'Name': 'Unknown',
            'Department': 'Unknown',
            'Role': 'Unknown',
            'Direct Reports': direct_reports_count[manager_id]
        })

result = pd.DataFrame(result_data)
result
--- output the model saw ---
frame 'result': 5 rows x 5 cols ['Employee ID', 'Name', 'Department', 'Role', 'Direct Reports']
head: [{'Employee ID': 'E1101', 'Name': 'Person 102', 'Department': 'Marketing', 'Role': 'VP', 'Direct Reports': 4}, {'Employee ID': 'E1108', 'Name': 'Person 109', 'Department': 'Finance', 'Role': 'IC', 'Direct Reports': 3}, {'Employee ID': 'E1023', 'Name': 'Person 24', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1041', 'Name': 'Person 42', 'Department': 'Marketing', 'Role': 'Director', 'Direct Reports': 3}, {'Employee ID': 'E1064', 'Name': 'Person 65', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}]
--- run 2 (final_answer) ---
# Count direct reports for each manager
direct_reports_count = df['Manager ID'].value_counts().head(5)

# Get the manager details for these top 5
top_manager_ids = direct_reports_count.index.tolist()

# Create a result DataFrame with manager information
result_data = []
for manager_id in top_manager_ids:
    manager_info = df[df['Employee ID'] == manager_id]
    if not manager_info.empty:
        result_data.append({
            'Employee ID': manager_id,
            'Name': manager_info['Name'].values[0],
            'Department': manager_info['Department'].values[0],
            'Role': manager_info['Role'].values[0],
            'Direct Reports': direct_reports_count[manager_id]
        })
    else:
        result_data.append({
            'Employee ID': manager_id,
            'Name': 'Unknown',
            'Department': 'Unknown',
            'Role': 'Unknown',
            'Direct Reports': direct_reports_count[manager_id]
        })

result = pd.DataFrame(result_data)
--- output the model saw ---
frame 'result': 5 rows x 5 cols ['Employee ID', 'Name', 'Department', 'Role', 'Direct Reports']
head: [{'Employee ID': 'E1101', 'Name': 'Person 102', 'Department': 'Marketing', 'Role': 'VP', 'Direct Reports': 4}, {'Employee ID': 'E1108', 'Name': 'Person 109', 'Department': 'Finance', 'Role': 'IC', 'Direct Reports': 3}, {'Employee ID': 'E1023', 'Name': 'Person 24', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1041', 'Name': 'Person 42', 'Department': 'Marketing', 'Role': 'Director', 'Direct Reports': 3}, {'Employee ID': 'E1064', 'Name': 'Person 65', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}]
--- last message ---
Perfect! I've identified the top 5 managers by number of direct reports. Let me provide this as the final answer.
OUTPUT (scored wrong):
status completed; 5 row(s):
  {"employee_id": "E1101", "Name": "Person 102", "Department": "Marketing", "Role": "VP", "Direct Reports": 4}
  {"employee_id": "E1108", "Name": "Person 109", "Department": "Finance", "Role": "IC", "Direct Reports": 3}
  {"employee_id": "E1023", "Name": "Person 24", "Department": "Operations", "Role": "Manager", "Direct Reports": 3}
  {"employee_id": "E1041", "Name": "Person 42", "Department": "Marketing", "Role": "Director", "Direct Reports": 3}
  {"employee_id": "E1064", "Name": "Person 65", "Department": "Operations", "Role": "Manager", "Direct Reports": 3}

CELL GROUP 2 (seeds 1; 1 wrong cell(s) with this trace and output)
TRACE:
--- run 1 (run_python) ---
# Count direct reports for each manager
# Manager ID column contains the employee ID of each person's manager
manager_report_counts = df['Manager ID'].value_counts().head(5)

# Get the manager details for these top 5
top_manager_ids = manager_report_counts.index.tolist()
top_managers = df[df['Employee ID'].isin(top_manager_ids)][['Employee ID', 'Name', 'Department', 'Role']]

# Merge with the counts
top_managers = top_managers.merge(
    manager_report_counts.rename('Direct Reports'),
    left_on='Employee ID',
    right_index=True
)

# Sort by number of direct reports descending
result = top_managers.sort_values('Direct Reports', ascending=False)
result
--- output the model saw ---
frame 'result': 5 rows x 5 cols ['Employee ID', 'Name', 'Department', 'Role', 'Direct Reports']
head: [{'Employee ID': 'E1101', 'Name': 'Person 102', 'Department': 'Marketing', 'Role': 'VP', 'Direct Reports': 4}, {'Employee ID': 'E1023', 'Name': 'Person 24', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1041', 'Name': 'Person 42', 'Department': 'Marketing', 'Role': 'Director', 'Direct Reports': 3}, {'Employee ID': 'E1064', 'Name': 'Person 65', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1108', 'Name': 'Person 109', 'Department': 'Finance', 'Role': 'IC', 'Direct Reports': 3}]
--- run 2 (final_answer) ---
# Count direct reports for each manager
manager_report_counts = df['Manager ID'].value_counts().head(5)

# Get the manager details for these top 5
top_manager_ids = manager_report_counts.index.tolist()
top_managers = df[df['Employee ID'].isin(top_manager_ids)][['Employee ID', 'Name', 'Department', 'Role']]

# Merge with the counts
top_managers = top_managers.merge(
    manager_report_counts.rename('Direct Reports'),
    left_on='Employee ID',
    right_index=True
)

# Sort by number of direct reports descending
result = top_managers.sort_values('Direct Reports', ascending=False)
--- output the model saw ---
frame 'result': 5 rows x 5 cols ['Employee ID', 'Name', 'Department', 'Role', 'Direct Reports']
head: [{'Employee ID': 'E1101', 'Name': 'Person 102', 'Department': 'Marketing', 'Role': 'VP', 'Direct Reports': 4}, {'Employee ID': 'E1023', 'Name': 'Person 24', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1041', 'Name': 'Person 42', 'Department': 'Marketing', 'Role': 'Director', 'Direct Reports': 3}, {'Employee ID': 'E1064', 'Name': 'Person 65', 'Department': 'Operations', 'Role': 'Manager', 'Direct Reports': 3}, {'Employee ID': 'E1108', 'Name': 'Person 109', 'Department': 'Finance', 'Role': 'IC', 'Direct Reports': 3}]
--- last message ---
Perfect! I've identified the top 5 managers by number of direct reports. Let me provide the final answer.
OUTPUT (scored wrong):
status completed; 5 row(s):
  {"employee_id": "E1101", "Name": "Person 102", "Department": "Marketing", "Role": "VP", "Direct Reports": 4}
  {"employee_id": "E1023", "Name": "Person 24", "Department": "Operations", "Role": "Manager", "Direct Reports": 3}
  {"employee_id": "E1041", "Name": "Person 42", "Department": "Marketing", "Role": "Director", "Direct Reports": 3}
  {"employee_id": "E1064", "Name": "Person 65", "Department": "Operations", "Role": "Manager", "Direct Reports": 3}
  {"employee_id": "E1108", "Name": "Person 109", "Department": "Finance", "Role": "IC", "Direct Reports": 3}

Write your reasoning first. Then end your answer with one JSON object, alone in a ```json fence,
in exactly this form (one entry in "cells" per cell group, in the order shown):

```json
{
  "item": "item_23",
  "intended_specification": "<output columns, filters, grouping, derived values, order/limit>",
  "cells": [
    {"group": 1, "label": "SPECIFICATION | PROCEDURE | SCORING | UNCLEAR", "also_procedure": false,
     "evidence": "<a quotation from the trace, in quotation marks, and why it shows the label>"}
  ],
  "notes": "<anything else; may be empty>"
}
```
