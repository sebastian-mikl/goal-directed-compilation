# Spider data notice

The files in this directory are derived from the Spider dataset and are
licensed under the Creative Commons Attribution-ShareAlike 4.0 International
licence (CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/), not
under this repository's MIT licence.

Source: Spider, by Tao Yu, Rui Zhang, Kai Yang, Michihiro Yasunaga, Dongxu
Wang, Zifan Li, James Ma, Irene Li, Qingning Yao, Shanelle Roman, Zilin Zhang
and Dragomir Radev, "Spider: A Large-Scale Human-Labeled Dataset for Complex
and Cross-Domain Semantic Parsing and Text-to-SQL Task", EMNLP 2018.
https://yale-lily.github.io/spider (CC BY-SA 4.0).

Changes made here:

- `matches.pkl`: the `matches` table of Spider's `wta_1` database, stored as a
  pandas DataFrame pickle.
- `schema.json`: that table's column names and types, with a short domain
  description added.
- `eval_corpus_spider_wta_1.json`: a selection of Spider's questions and gold SQL
  for the `wta_1` database, with a tier label, an expected behaviour and a
  reason added to each entry.

Files elsewhere in this repository that are built from these (the
`gold_spider_wta_1` gold key and digest under `evidence/gold/`, and the
recorded Spider results under `results/`, which quote the questions and
table values) are under the same licence. See the README's "Third-party
data" section.
