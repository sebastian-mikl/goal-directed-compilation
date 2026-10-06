# Spot-check sample

For each final label, up to 10 pairs drawn at random over all labelled pairs (random.Random(20261006) on the sorted pair ids, labels in the order SPECIFICATION, PROCEDURE, SCORING, UNCLEAR), plus every disagreement. Each entry gives the final label, the intended specification and the evidence; the packet is `review/error_classification/packets/<pair id>.md`.

## code_agent__employee__t4_02: SPECIFICATION (sample)

- Intended specification: Filter rows with Status exactly equal to 'Active'; group by Role; sum 'Equity Grant Value'; output columns Role and Equity Grant Value, sorted by the sum descending; no limit.
- Evidence: group 1: "active_employees = df[df['Status'] == 'Active']" under the comment "# Filter for active employees" shows the system defined active as the literal status 'Active'. The groupby-sum carries this out correctly and reproduces the wrong output (VP 4844741, Director 1761947, Manager 1124634, IC 754186). Every gold total is higher, so the gold counted a broader set of employees (most likely the current set: Active, On Leave, Sabbatical). The error is in how the term was read, not in how the code ran.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## code_agent__employee__t4_08: SPECIFICATION (sample)

- Intended specification: Single scalar: mean of 'Market Rate' over rows where 'Status' == 'Active' (exact literal match) and 'Role' == 'Director' (exact literal match); no grouping, no ordering/limit.
- Evidence: group 1: "# Active means Status is 'Active'" and "active_directors = df[(df['Status'] == 'Active') & (df['Role'] == 'Director')]" -- the system explicitly fixed 'active' to the exact literal Status value 'Active' (and Role to the exact literal 'Director') without inspecting the columns' values, and the code faithfully implements that, returning 215639.36842105264 identically in all three runs. Executing this stated specification reproduces the wrong answer, so the fault is the specification (status/role set too narrow), not the procedure.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## code_agent__employee__t4_10: SPECIFICATION (sample)

- Intended specification: Filter rows with exact match Status == 'Active'; group by Department; derive mean of 'Performance Score'; sort descending; take top 3; output columns Department and mean Performance Score.
- Evidence: group 1: "# Filter for active employees (Status = 'Active')" / "active_employees = df[df['Status'] == 'Active']" -- the system deliberately defined 'active employees' as an exact match on the literal 'Active'; the groupby/mean/sort/head(3) steps implement that intent faithfully, so correct execution reproduces the wrong values (Engineering 3.3409, Sales 3.1579, Operations 3.1417) rather than the gold (Engineering 3.2826, Operations 3.1515, Sales 3.14). The error lies in the filter set the system chose, not in how the steps were carried out.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## network__train_station__station_23: SPECIFICATION (sample)

- Intended specification: Output columns name, location, annual_entry_exit, annual_interchanges; grain one row per station; no filters; no derived values; order by annual_entry_exit desc, then annual_interchanges desc; no limit.
- Evidence: group 1: "order_by": [{"col": "annual_entry_exit", "dir": "desc"}, {"col": "annual_interchanges", "dir": "desc"}] -- the model chose descending, but the gold is ascending (East Croydon 20.551 first, London Waterloo 94.046 last), and the output is exactly the gold order reversed. The engine's step "select({'cols': ('annual_entry_exit', 'annual_interchanges', 'location', 'name')})" followed by the written ordering does exactly what the specification says, so running it correctly reproduces the wrong answer. The specification also adds the sort keys as output columns (["annual_entry_exit", "unknown"], ["annual_interchanges", "unknown"]), but the question asks only for names and locations.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## planner__bike_1__bike_04: SPECIFICATION (sample)

- Intended specification: Output: a single scalar, the count of bike_id values over all rows of the register (non-null row count, not distinct). Filters: none. Grouping: none (group_by []). Derived values: count(bike_id). Order/limit: none.
- Evidence: group 1: "group_by": [], "value_col": "bike_id", "agg": "count" -- the plan counts every bike_id entry across all trips with no dedup step (no group_by bike_id before counting), so it ignores the 'unique' in the question. Executing this plan correctly gives the per-row count, 9959.0, which is exactly the recorded output, not the distinct count 530.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## planner__employee__t4_01: SPECIFICATION (sample)

- Intended specification: Output columns: Department, mean Annual Salary. Filter: Status == 'Active' (exact match, single value). Grouping: Department. Derived values: mean of Annual Salary per group. No order or limit.
- Evidence: group 1: "predicates": [{"col": "Status", "op": "==", "value": "Active"}] followed by "group_by": ["Department"], "value_col": "Annual Salary", "agg": "mean". The plan was executed as written: filter before aggregation, correct input frames, final frame f1. Running it correctly gives the wrong output, so the error is the literal single-value reading of 'active employees'. The gold averages differ in every department (e.g. Engineering 182146.30 vs 177177.91), so the gold answer uses a different set of employees.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## react__employee__t4_02: SPECIFICATION (sample)

- Intended specification: Filter rows where Status == 'Active' (exact match); group by Role; sum Equity Grant Value per Role; output columns Role and summed Equity Grant Value; no derived columns, no ordering or limit.
- Evidence: group 1: "filter({"input": "register", "predicates": [{"col": "Status", "op": "==", "value": "Active"}]})" -> "113 rows", then "aggregate({... "group_by": "Role", "agg": "sum", "value_col": "Equity Grant Value"})". The two steps carry out the intended exact-match 'Active' filter and per-Role sum correctly, so doing that plan right reproduces the output. Every per-Role total is below the gold (e.g. Director 1761947 vs 2573124, VP 4844741 vs 6613726), which shows the gold counted more rows as 'active'. The mistake is the literal reading of 'active' as Status == 'Active', not a broken step.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## react__employee__t4_05: SPECIFICATION (sample)

- Intended specification: Filter Status == 'Active' AND Annual Salary > 150000; derive Salary Variance = Annual Salary - Market Rate per row; group by Role; output Role and mean(Salary Variance); no ordering or limit.
- Evidence: group 1: "filter({... {"col": "Status", "op": "==", "value": "Active"} ...})" -> "frame 'f0': 59 rows". The compute and mean-per-Role steps are carried out correctly, and Manager matches gold exactly (11490.461538). Director and VP differ because of which rows were included: the fractional parts imply 19/13/27 rows in the system's groups (total 59) against 29/13/36 in gold's (total 78). Gold therefore counts 19 more employees as 'active', most likely the briefing's 'current' set (Active, On Leave, Sabbatical). The system's literal exact-match reading of 'active' leaves them out, and running its intended plan reproduces the wrong answer.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## react__employee__t4_07: SPECIFICATION (sample)

- Intended specification: Output: a single scalar, the mean of a derived column 'Total Variable Comp' = 'Bonus Target' + 'Equity Grant Value'. Filters: 'Status' == 'Active' (exact single value) AND 'Department' in ['Engineering', 'Sales']. Grouping: none (group_by []). Order/limit: none.
- Evidence: group 1: "filter({\"input\": \"register\", \"predicates\": [{\"col\": \"Status\", \"op\": \"==\", \"value\": \"Active\"}, {\"col\": \"Department\", \"op\": \"in\", \"value\": [\"Engineering\", \"Sales\"]}]})" -> "41 rows", then "compute(... \"expr\": \"Bonus Target + Equity Grant Value\")" and "aggregate(... \"group_by\": [], \"agg\": \"mean\" ...)" -> "94603.0487804878". Every step does what the system intended, so running that specification correctly reproduces the wrong value. The gold (106445.708333, fractional part 17/24) implies a different row count from 41, so the error is in which employees the status filter keeps (a literal single-value reading of 'active'), not in how the steps were carried out.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## react__train_station__station_23: SPECIFICATION (sample)

- Intended specification: Output columns Name, Location; all 12 rows of register, no filter, no grouping, no derived values; ordered by Annual_entry_exit DESC then Annual_interchanges DESC; no limit.
- Evidence: group 1: "order({\"input\": \"f0\", \"order_by\": [{\"col\": \"Annual_entry_exit\", \"dir\": \"desc\"}, {\"col\": \"Annual_interchanges\", \"dir\": \"desc\"}]})" - the system chose descending order on purpose, and the steps carried that out correctly. The output (London Waterloo first ... East Croydon last) is the exact reverse of the gold answer, which uses the default ascending order (East Croydon first ... London Waterloo last). The columns, rows and sort keys are all correct; only the direction is wrong, so the error is in the specification.
- Passes: 1 SPECIFICATION, 2 SPECIFICATION

## code_agent__bike_1__bike_05: SCORING (sample)

- Intended specification: Output columns: start_station_name, start_station_id (the final run also added trip_count). Filter: start_date month == 8, any year. Group by start_station_name and start_station_id, count trips, sort by count descending, take the top 1.
- Evidence: group 1: Run 2 output "{'index': 'start_station_name', 50: 'San Francisco Caltrain 2 (330 Townsend)'}, {'index': 'start_station_id', 50: 69}" has the gold station name and id. The answer was scored wrong only because `.iloc[0][...]` returned a Series, giving a transposed index/value frame ("3 rows x 2 cols ['index', '50']"), and because the final run added a field the question did not ask for ("{'index': 'trip_count', 50: 643}").
- Passes: 1 SCORING, 2 SCORING

## code_agent__bike_1__bike_06: SCORING (sample)

- Intended specification: Filter rows where zip_code == 94002 (as integer after the string match was empty); group/count trips by bike_id via value_counts(); return the single bike_id with the highest trip count (first of sorted counts / idxmax), order by count descending, limit 1.
- Evidence: group 1: "# There's a tie between bike 252 and 679, both with 4 trips # Return the first one (bike 252)" - the system correctly found the 252/679 tie at 4 trips and returned 252, which the file lists as also correct under the tie rule; the answer is right in substance but scored wrong. | group 2: "Note that there's actually a tie - bike 679 also had 4 trips, but bike 252 appears first in the sorted results." - the intended spec was executed correctly and returned a tied top bike (252), listed as tie-correct. | group 3: "result = bike_counts.idxmax()" -> "result = np.int64(252)" - correct filter (zip_code == 94002) and per-bike count; idxmax picks 252, which ties with gold 679 at 4 trips and is listed as also correct under the tie rule. | group 4: "(Note: bike 679 also had 4 trips, but bike 252 appears first.)" - the system knew about the tie and returned one of the tied maxima (252), which is tie-correct. | group 5: "Note that there's actually a tie - bike 679 also had 4 trips, but bike 252 appears first." - spec and steps are correct; the returned 252 ties with gold 679 and is listed as also correct.
- Passes: 1 SCORING, 2 SCORING

## code_agent__employee__t3_01: SCORING (sample)

- Intended specification: Filter rows to Status in ['Active','On Leave','Sabbatical'] (the business-rule meaning of 'current'); group by Department; take the mean of 'Annual Salary'; output columns Department and the mean (left named 'Annual Salary'); sort by the mean, highest first; round to 2 decimals.
- Evidence: group 1: "current_employees = df[df['Status'].isin(['Active', 'On Leave', 'Sabbatical'])]" and "result = current_employees.groupby('Department')['Annual Salary'].mean().sort_values(ascending=False).round(2)". Before rounding, run 2 showed "{'Department': 'Marketing', 'Annual Salary': 200354.64285714287}" ... "{'Department': 'Finance', 'Annual Salary': 168161.9}", which is the gold answer for all 5 departments. The returned answer differs only in the column name ('Annual Salary' instead of 'avg_salary'), the row order (highest salary first instead of alphabetical) and rounding to 2 decimals (differences under 0.005). It is right in substance and was scored wrong on form.
- Passes: 1 SCORING, 2 SCORING

## code_agent__employee__t3_04: SCORING (sample)

- Intended specification: Count rows per 'Manager ID' (df['Manager ID'].value_counts(), no status filter); take the top 5 by count descending (.head(5)); for each manager ID look up the matching 'Employee ID' row for Name, Department, Role; output columns Employee ID, Name, Department, Role, Direct Reports (the count).
- Evidence: group 1: Output "{'Employee ID': 'E1101', ... 'Direct Reports': 4}, {'Employee ID': 'E1108', ... 'Direct Reports': 3}, {'Employee ID': 'E1023', ... 3}, {'Employee ID': 'E1041', ... 3}, {'Employee ID': 'E1064', ... 3}". This set {E1101, E1023, E1041, E1064, E1108} with counts 4/3/3/3/3 is listed among the answers correct under the tie rule, so it is right in substance. The mismatch is only in form: the ID column is named 'employee_id' instead of 'Manager ID', the count column is 'Direct Reports' instead of 'count', and Name/Department/Role were added, which the question did not ask for. | group 2: "manager_report_counts = df['Manager ID'].value_counts().head(5)" then "result = top_managers.sort_values('Direct Reports', ascending=False)" gives E1101 (4), E1023, E1041, E1064, E1108 (3 each). This is the same tie-valid set as group 1 in a different order among tied rows. Again only the column names ('employee_id', 'Direct Reports') and the extra Name/Department/Role columns differ from gold.
- Passes: 1 SCORING, 2 SCORING

## code_agent__employee__t4_03: SCORING (sample)

- Intended specification: Filter rows where Status is in ['Active', 'On Leave', 'Sabbatical'] (the 'current' business rule); group by Department; output Department and the mean of Performance Score (column kept as 'Performance Score'); sort by the mean, highest first; round to 2 decimals.
- Evidence: group 1: Run 2 printed the unrounded averages "{'Department': 'Engineering', 'Performance Score': 3.282608695652174}" ... "{'Department': 'Marketing', 'Performance Score': 3.0928571428571425}", all five matching the gold values. The only change afterwards was presentation: "Let me format this as a cleaner result" and `.sort_values(ascending=False).round(2)`, which gave 3.28/3.15/3.14/3.14/3.09. The filter `isin(['Active', 'On Leave', 'Sabbatical'])` copies the 'current' rule exactly. The answer is right in substance and was scored wrong because of rounding precision, the column name ('Performance Score' vs 'avg_performance') and/or row order, none of which the question set.
- Passes: 1 SCORING, 2 SCORING

## code_agent__world_1__world_01: SCORING (sample)

- Intended specification: Output column: Name (country name). Filter: IndepYear > 1950 (rows with a missing IndepYear are excluded). No grouping, no derived values. Sorted by Name ascending, no limit.
- Evidence: group 1: "result = df[df['IndepYear'] > 1950]['Name'].sort_values().tolist()" gives "110 items in all", and the first 30 ('Algeria', 'Angola', 'Antigua and Barbuda', ... 'Côte d’Ivoire', 'Djibouti') match the gold's 110 rows in the same order. The filter and column are correct and the content matches. The only visible difference is shape: a flat list of strings against gold rows of {"Name": ...}. So the answer is right in substance and was scored wrong on form.
- Passes: 1 SCORING, 2 SCORING

## code_agent__wta_1__wta_17: SCORING (sample)

- Intended specification: No filters or grouping; take the single row with the maximum 'minutes' value (df.loc[df['minutes'].idxmax()]); output winner_name and loser_name from that row, plus an extra 'minutes' field (199); one row.
- Evidence: group 1: Output "{"winner_name": "Petra Kvitova", "loser_name": "Angelique Kerber", "minutes": "199"}" has exactly the gold winner and loser names; the only difference is the added field from "'minutes': max_minutes_row['minutes']", which the question ("find the names of loser and winner") did not ask for. The answer is right in substance and only its shape differs. | group 2: "max_minutes_match = df.loc[df['minutes'].idxmax()]" gives "{'winner_name': 'Petra Kvitova', 'loser_name': 'Angelique Kerber', 'minutes': np.int64(199)}": the names match the gold answer, and the only difference is the extra 'minutes' field the question did not ask for. The answer is right in substance and only its shape differs.
- Passes: 1 SCORING, 2 SCORING

## network__bike_1__bike_06: SCORING (sample)

- Intended specification: Output columns bike_id and trip_count; filter zip_code == '94002'; group by bike_id; derive trip_count = count(bike_id); order by trip_count desc, limit 1.
- Evidence: group 1: The output "{"bike_id": 252, "trip_count": 4}" comes from a correct plan ("where": [["zip_code", "==", "94002"]], "grouped_by": ["bike_id"], "count(bike_id)", order_by trip_count desc, limit 1), and the engine ran it as written. Bike 252 is listed as also correct under the tie rule, so the answer is right; it was marked wrong because of the tie with gold bike 679 and/or the extra trip_count column.
- Passes: 1 SCORING, 2 SCORING

## planner__bike_1__bike_06: SCORING (sample)

- Intended specification: Filter register to zip_code == '94002' (exact match); group by bike_id; count trips (count of id); order by the count descending; limit 1; output bike_id (plus the count column named 'id').
- Evidence: group 1: "group_by": ["bike_id"], "value_col": "id", "agg": "count" ... "order_by": [{"col": "id", "dir": "desc"}], "limit": 1 gives OUTPUT {"bike_id": 252, "id": 4}. The plan correctly finds the most-used bike in zip 94002, and 252 is listed as ALSO CORRECT UNDER THE TIE RULE (it ties gold 679 on trip count). So the answer is right in substance and was marked wrong because of the tie and/or the dict shape with an extra count column. | group 2: {"col": "zip_code", "op": "==", "value": "94002"}, then "group_by": ["bike_id"], "agg": "count", then "order_by": [{"col": "id", "dir": "desc"}], "limit": 1 gives OUTPUT {"bike_id": 252, "id": 4}. This is the same correct plan and output as group 1. Bike 252 ties gold 679 under the tie rule, so the miss comes from tie handling or the dict-with-count-column shape, not from the plan or its execution.
- Passes: 1 SCORING, 2 SCORING

## react__bike_1__bike_06: SCORING (sample)

- Intended specification: Output column: bike_id. Filter: zip_code == 94002. Grouping: by bike_id, with the number of trips (count of id) as the derived value. Order: count descending, limit 1 (the most-used bike in that zip code).
- Evidence: group 1: "order({\"input\": \"f1\", \"order_by\": [{\"col\": \"id\", \"dir\": \"desc\"}], \"limit\": 1})" -> "{'bike_id': 252, 'id': 4}", final output "252.0". The filter ("zip_code" == "94002"), the per-bike count ("group_by": "bike_id", "agg": "count") and the descending top-1 all match the question correctly. 252 is listed under ALSO CORRECT UNDER THE TIE RULE: it ties with gold 679 on the trip count, and limit 1 just picked the other tied bike. The answer is correct in substance and was scored wrong because of the tie, and possibly also because of the float format '252.0'.
- Passes: 1 SCORING, 2 SCORING

## planner__wta_1__wta_24: SPECIFICATION (disagreement)

- Intended specification: Output: a single scalar count. Filters: tourney_name == 'WTA Championships' AND winner_hand == 'L'. Select winner_id. Aggregate with no group_by: count of winner_id rows (a plain row count with no distinct or dedup step). No derived columns, no order or limit.
- Evidence: group 1: "group_by": [], "value_col": "winner_id", "agg": "count" comes straight after "columns": ["winner_id"], with no group_by winner_id step to dedupe first. The plan states a count of matching match rows, not of distinct winners, and running it correctly gives the recorded 3.0. The question asks for "different winners" (gold 2). The step language could express a distinct count (group_by winner_id, then count), but the model did not specify one. That is a misreading of the question's distinctness, not a failed step.
- Passes: 1 PROCEDURE, 2 SPECIFICATION, 3 SPECIFICATION

