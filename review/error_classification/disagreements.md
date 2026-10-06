# Disagreements between passes 1 and 2, and their resolution

1 of 52 pairs. A disagreement is a pair on which the passes differ, in the pair label or in any cell's label (review/error_classification_prereg.md). Pass 3 is a fresh sub-agent that saw the packet and both labels.

## planner__wta_1__wta_24 (item_26)

- Cell group 1 (seeds 0, 1, 2, 3, 4): pass 1 **PROCEDURE**, pass 2 **SPECIFICATION** → resolved **SPECIFICATION**.

- Pass 1: Output: a single scalar, the number of distinct winner_id values. Filters: tourney_name == 'WTA Championships' AND winner_hand == 'L'. Grouping: none at the end (a distinct count over the filtered rows, which in this system needs group_by winner_id followed by count). Derived values: none. Order/limit: none.
  - Evidence: "select" ... "columns": ["winner_id"] followed by "group_by": [], "value_col": "winner_id", "agg": "count": the model cut the frame down to winner_id before counting, which only makes sense if it meant to count unique winners (as the question's "different winners" asks). But select does not dedupe, and the plan has no group_by winner_id step before the count, so it counted match rows (3) where unique winners (2) were wanted. The filters ("tourney_name" == "WTA Championships", "winner_hand" == "L") look right, so running the intended distinct count would give the gold answer of 2.
- Pass 2: Output: a single scalar count. Filters: tourney_name == 'WTA Championships' AND winner_hand == 'L'. Select winner_id. Aggregate: no group_by, count of winner_id (plain row count, no distinct/dedup step). No derived columns, no order/limit.
  - Evidence: "op": "aggregate", "input": "f1", "group_by": [], "value_col": "winner_id", "agg": "count" follows straight on from "columns": ["winner_id"], with no group_by on winner_id to deduplicate first. The plan counts matching rows (matches won), not distinct winners, so running it faithfully gives the recorded 3.0. The question asks for "different winners" (gold 2). The plan misread the question's distinct-ness, which the step language could express via group_by winner_id then count.
- Resolution: Output: a single scalar count. Filters: tourney_name == 'WTA Championships' AND winner_hand == 'L'. Select winner_id. Aggregate with no group_by: count of winner_id rows (a plain row count with no distinct or dedup step). No derived columns, no order or limit.
  - Evidence: "group_by": [], "value_col": "winner_id", "agg": "count" comes straight after "columns": ["winner_id"], with no group_by winner_id step to dedupe first. The plan states a count of matching match rows, not of distinct winners, and running it correctly gives the recorded 3.0. The question asks for "different winners" (gold 2). The step language could express a distinct count (group_by winner_id, then count), but the model did not specify one. That is a misreading of the question's distinctness, not a failed step.

