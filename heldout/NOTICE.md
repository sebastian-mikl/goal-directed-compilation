# Spider data notice

The Spider-derived files in this directory are licensed under the Creative
Commons Attribution-ShareAlike 4.0 International licence (CC BY-SA 4.0,
https://creativecommons.org/licenses/by-sa/4.0/), not under this repository's
MIT licence.

Source: Spider, by Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga, Dongxu
Wang, Zifan Li, James Ma, Irene Li, Qingning Yao, Shanelle Roman, Zilin Zhang
and Dragomir Radev, "Spider: A Large-Scale Human-Labeled Dataset for Complex
and Cross-Domain Semantic Parsing and Text-to-SQL Task", EMNLP 2018.
https://yale-lily.github.io/spider (CC BY-SA 4.0).

The held-out run uses two Spider databases that no development step used:
`employee_hire_evaluation` and `train_station`. Changes made here:

- `employee_hire_evaluation/shop.pkl` and `train_station/station.pkl`: one table
  of each database (`shop`, `station`), stored as a pandas DataFrame pickle.
- `employee_hire_evaluation/schema.json` and `train_station/schema.json`: that
  table's column names and types, with a description added by a fixed rule.
- `eval_corpus_spider_employee_hire_evaluation.json` and
  `eval_corpus_spider_train_station.json`: Spider's questions and gold SQL for
  the database, with a tier label, an expected behaviour and a reason added to
  each entry.
- `gold/`: answers computed by executing the gold SQL, and their digests.
- `tie_alternatives_heldout.json`: the ranking-tie alternatives found in those
  answers.
- `results/`: the recorded cells, which quote the questions and table values.

The scripts, the pre-registration and the analysis outputs are written for this
repository and are under its MIT licence. The analysis outputs under
`results/analysis/` quote question identifiers only.
