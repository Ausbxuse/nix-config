# Global preferences (all projects)

## Explaining concepts

When answering conceptual "why" questions (loss behavior, training dynamics,
mechanisms, algorithms), lead with a tiny concrete worked example using my own
artifacts and real numbers — a few aligned monospace lines, e.g.:

```
                         value at row 8
the truth (what training grades against):   -1.75
guess from 0.3 s ago:                        -1.70   -> miss: 0.05 too high
guess now:                                   -1.80   -> miss: 0.05 too low
gap visible in the figure:                    0.10
```

Then one plain-language takeaway sentence. Generalizations and analogies come
after the numbers, never instead of them. Use the vocabulary of my own
figures/data (the dotted curve, row 8, the clamp edge), not abstract notation.

## Division of labor: plan on the expensive model, execute on Opus

To save tokens, when the session runs on Fable: Fable drafts the plan and the
briefs; execution legwork (file reading, remote shell iteration, mechanical
edits, long builds) goes to subagents launched with `model: "opus"`
(general-purpose agents — forks inherit the parent model and don't count).
Each brief must be self-contained: context, exact paths, constraints,
verification steps, and the deliverable format. Fable reviews results, owns
decisions, plan files, and anything safety- or contract-sensitive. Small
one-shot lookups stay direct rather than spawning an agent.
