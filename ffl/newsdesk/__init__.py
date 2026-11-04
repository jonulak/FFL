"""The news desk (beads ffl-0gv): a catalog of football news that reaches the league's rosters.

The pipeline, in one paragraph: collectors pull items from the sources (feeds, podcasts, clips) into
the news tables; `players.match` finds which players each item names (a full name league-wide, a
surname or garbled name only among the clubs the item is about, and an ambiguous one not at all);
The tagger (`rules.py`, no model) tags each item (what happened, how much it matters); items about the same event are grouped into
a story; Haiku writes the story's one-line summary; and the stories that touch my roster or a
waiver target go out as notices. Each stage keeps its own table, so a stage reruns without the
ones before it.

A grouping or prompt change is tried on the bench (`bench.py`, `python -m ffl.newsdesk bench`): a frozen slice of
collected news replayed in time order with the tagger's and Haiku's answers saved, checked against `bench_cases.py`.
"""
