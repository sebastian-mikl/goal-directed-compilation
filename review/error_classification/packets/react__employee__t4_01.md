ITEM item_25

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
A tool-use loop. The model calls table tools one at a time: filter (predicates on columns), select (keep columns), compute (a new column from an expression over two columns), aggregate (group_by columns, a value column and one of sum, mean, count, min, max) and order (sort keys and an optional row limit). Each tool reads a named frame ('register' is the table) and writes the next one ('f0', 'f1', ...). After each call it sees a short summary of the result, and it ends by naming a final frame. TRACE lists each tool call with the summary the model saw; OUTPUT is the final frame.

QUESTION
For active employees, what is the average Annual Salary per Department?

GOLD ANSWER
5 row(s):
  {"Department": "Engineering", "avg_salary": 182146.304348}
  {"Department": "Finance", "avg_salary": 168161.9}
  {"Department": "Marketing", "avg_salary": 200354.642857}
  {"Department": "Operations", "avg_salary": 184684.090909}
  {"Department": "Sales", "avg_salary": 188665.84}

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

CELL GROUP 1 (seeds 0, 1, 2; 3 wrong cell(s) with this trace and output)
TRACE:
1. filter({"input": "register", "predicates": [{"col": "Status", "op": "==", "value": "Active"}]})
   -> frame 'f0': 113 rows x 14 cols ['Employee ID', 'Name', 'Department', 'Role', 'Status', 'Location', 'Annual Salary', 'Bonus Target', 'Tenure (Years)', 'Manager ID', 'Performance Score', 'Market Rate', 
2. aggregate({"input": "f0", "group_by": "Department", "agg": "mean", "value_col": "Annual Salary"})
   -> frame 'f1': 5 rows x 2 cols ['Department', 'Annual Salary']
head: [{'Department': 'Engineering', 'Annual Salary': 177177.9090909091}, {'Department': 'Finance', 'Annual Salary': 169541.1923076923}, {'D
3. final_answer({"frame": "f1"})
   -> None
final frame: 'f1'
OUTPUT (scored wrong):
status completed; 5 row(s):
  {"Department": "Engineering", "Annual Salary": 177177.9090909091}
  {"Department": "Finance", "Annual Salary": 169541.1923076923}
  {"Department": "Marketing", "Annual Salary": 191788.27272727274}
  {"Department": "Operations", "Annual Salary": 182078.125}
  {"Department": "Sales", "Annual Salary": 167324.36842105264}

Write your reasoning first. Then end your answer with one JSON object, alone in a ```json fence,
in exactly this form (one entry in "cells" per cell group, in the order shown):

```json
{
  "item": "item_25",
  "intended_specification": "<output columns, filters, grouping, derived values, order/limit>",
  "cells": [
    {"group": 1, "label": "SPECIFICATION | PROCEDURE | SCORING | UNCLEAR", "also_procedure": false,
     "evidence": "<a quotation from the trace, in quotation marks, and why it shows the label>"}
  ],
  "notes": "<anything else; may be empty>"
}
```
