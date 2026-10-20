"""AI lab: a language model (or a scripted baseline) controls the simulation headlessly.

observe.py   the situation, as text for the model (and as a structure kept with each decision)
actions.py   the instruction set the model answers with, applied through the simulation's logged actions
agents.py    the model clients (RunPod serverless vLLM, any OpenAI-compatible server) and the baseline agent
runner.py    one headless run, in its own process: decisions, the run kept like an exercise (replayable in the
             debrief), its preview, its evaluation and its experience records
evaluate.py  the outcome of a run: a score, a label and a reward per decision, and the optional judge
"""
