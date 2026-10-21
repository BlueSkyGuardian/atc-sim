# Conflict-free solution sets for en-route ATC — the solver paper

`paper.pdf` and its LaTeX source (`paper.tex`, with `results.tex`; `numbers.tex` and `tab_*.tex` are generated).

Reproduce (paths in `scripts/common.py` point at a working folder; adjust `FIG` and the data paths):

```
python -m atcsim.solve.hj 5 7          # reachability tables (ATC_DATA_DIR/hj)
python scripts/fig_a.py                # worked example: figures 1, 3, 4
python scripts/fig_b2.py               # CPA vs sampling, solver statistics (frozen situations)
python scripts/hjval.py 2500           # reachability table vs simulated turns
python scripts/fig_c.py                # figures 5, 6 (reachability)
python scripts/fig_d.py                # the solution space of the worked example
python scripts/fig_e0.py               # its traffic picture
python scripts/fig_e.py s10_busy_mixed 360 AFR11 RAM602   # joint heading space of a pair
OUT=data/runs2.json python scripts/eval_runs.py <scenarios,...> 1200   # closed-loop runs, baseline vs solver
python scripts/gen.py                  # numbers.tex, tables, figures 2 and 7
pdflatex paper.tex && pdflatex paper.tex
```
