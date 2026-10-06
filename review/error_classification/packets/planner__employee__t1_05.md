ITEM item_22

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
A step planner. In one model call, the model writes a list of steps over the table, each one of: filter (predicates on columns), select (keep columns), compute (a new column from an expression over two columns), aggregate (group_by columns, a value column and one of sum, mean, count, min, max) and order (sort keys and an optional row limit). Each step reads a named frame ('register' is the table) and writes the next one ('f0', 'f1', ...). The steps are then executed mechanically, and the frame named as final is the answer. TRACE is the model's reply; OUTPUT is what executing it returned.

QUESTION
List employees in the San Francisco office.

GOLD ANSWER
34 row(s):
  {"Location": "SF", "Name": "Person 103", "employee_id": "E1102"}
  {"Location": "SF", "Name": "Person 110", "employee_id": "E1109"}
  {"Location": "SF", "Name": "Person 113", "employee_id": "E1112"}
  {"Location": "SF", "Name": "Person 114", "employee_id": "E1113"}
  {"Location": "SF", "Name": "Person 115", "employee_id": "E1114"}
  {"Location": "SF", "Name": "Person 129", "employee_id": "E1128"}
  {"Location": "SF", "Name": "Person 132", "employee_id": "E1131"}
  {"Location": "SF", "Name": "Person 139", "employee_id": "E1138"}
  {"Location": "SF", "Name": "Person 145", "employee_id": "E1144"}
  {"Location": "SF", "Name": "Person 146", "employee_id": "E1145"}
  {"Location": "SF", "Name": "Person 15", "employee_id": "E1014"}
  {"Location": "SF", "Name": "Person 16", "employee_id": "E1015"}
  {"Location": "SF", "Name": "Person 24", "employee_id": "E1023"}
  {"Location": "SF", "Name": "Person 25", "employee_id": "E1024"}
  {"Location": "SF", "Name": "Person 26", "employee_id": "E1025"}
  {"Location": "SF", "Name": "Person 27", "employee_id": "E1026"}
  {"Location": "SF", "Name": "Person 29", "employee_id": "E1028"}
  {"Location": "SF", "Name": "Person 3", "employee_id": "E1002"}
  {"Location": "SF", "Name": "Person 30", "employee_id": "E1029"}
  {"Location": "SF", "Name": "Person 32", "employee_id": "E1031"}
  {"Location": "SF", "Name": "Person 33", "employee_id": "E1032"}
  {"Location": "SF", "Name": "Person 39", "employee_id": "E1038"}
  {"Location": "SF", "Name": "Person 4", "employee_id": "E1003"}
  {"Location": "SF", "Name": "Person 44", "employee_id": "E1043"}
  {"Location": "SF", "Name": "Person 51", "employee_id": "E1050"}
  {"Location": "SF", "Name": "Person 54", "employee_id": "E1053"}
  {"Location": "SF", "Name": "Person 57", "employee_id": "E1056"}
  {"Location": "SF", "Name": "Person 65", "employee_id": "E1064"}
  {"Location": "SF", "Name": "Person 66", "employee_id": "E1065"}
  {"Location": "SF", "Name": "Person 70", "employee_id": "E1069"}
  ... (34 rows in all; the first 30 are shown)

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
```json
{
  "plan": [
    {
      "op": "filter",
      "input": "register",
      "predicates": [
        {
          "col": "Location",
          "op": "==",
          "value": "San Francisco"
        }
      ]
    }
  ],
  "final_frame": "f0"
}
```
OUTPUT (scored wrong):
status completed; []

Write your reasoning first. Then end your answer with one JSON object, alone in a ```json fence,
in exactly this form (one entry in "cells" per cell group, in the order shown):

```json
{
  "item": "item_22",
  "intended_specification": "<output columns, filters, grouping, derived values, order/limit>",
  "cells": [
    {"group": 1, "label": "SPECIFICATION | PROCEDURE | SCORING | UNCLEAR", "also_procedure": false,
     "evidence": "<a quotation from the trace, in quotation marks, and why it shows the label>"}
  ],
  "notes": "<anything else; may be empty>"
}
```
