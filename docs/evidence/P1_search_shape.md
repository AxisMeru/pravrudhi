# The shape of the search: branching, and whether selection had a choice

Rendered from the ledger alone. Two measurements, both structural rather than statistical: how the
candidate graph branches, and how often the budget actually forced the controller to leave something out.

## Ancestry

| quantity | value |
|---|---|
| candidates proposed | 189 |
| roots | 1 |
| distinct parents in the whole history | 2 |
| deepest lineage | 2 generation(s) |
| widest parent | `c-0000`, 101 children |

Every candidate is proposed with its parent recorded, and until now nothing read the field back. The
graph it describes is a star: the whole recorded history descends from a very small number of ancestors,
because a candidate's parent is always whichever incumbent was standing when it was built.

## Selection pressure

A selection rule earns its keep only when the live pool exceeds what the budget can run. The live pool is
every candidate proposed so far that has not been pruned or promoted, restricted to the evaluation benches
the night worked on; a candidate measured on another pool is not an alternative to one measured on this
pool, and counting it would inflate the apparent choice.

| night | live pool | ran | declined |
|---|---|---|---|
| 1 | 32 | 23 | 9 |
| 2 | 35 | 33 | 2 |
| 3 | 40 | 27 | 13 |
| 4 | 33 | 31 | 2 |
| 5 | 41 | 26 | 15 |
| 6 | 50 | 29 | 21 |
| 7 | 8 | 8 | 0 |
| 8 | 8 | 8 | 0 |
| 9 | 8 | 8 | 0 |
| 10 | 8 | 8 | 0 |
| 11 | 14 | 12 | 2 |
| 12 | 20 | 20 | 0 |
| 13 | 12 | 12 | 0 |
| 14 | 7 | 7 | 0 |
| 15 | 2 | 2 | 0 |
| 16 | 1 | 1 | 0 |

The budget forced a choice on 7 of 16 nights, declining 64 candidate-nights in total.

From night 7 onward that falls to 1 of 10 nights, declining 2. On those nights the loop proposed about as many candidates as it
could afford to run, so the pool equalled the budget and the controller ranked a set it was going to
run in full regardless.

## Tensions

The live pool is reconstructed here from propose, prune and promote rows rather than read from the
controller, so it is this renderer's reading of what was available rather than the pool the controller
itself held. A candidate pruned during a night is counted as live for that night, because pruning follows
evaluation and the candidate was available when the night chose.

Selection pressure says nothing about whether the ranking was good. It says only how often ranking
mattered. A night whose budget covered its whole pool cannot distinguish any two selection rules, and most
recent nights are of that kind, which bounds what any retrospective comparison of arms can show.
