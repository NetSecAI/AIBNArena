"""A local web interface for launching one manual campaign episode.

The episode it runs is the one `experiments/run_langchain_baseline.sh` runs by
hand: start a subject's A2A server, prove the process and model answering are the
ones just started, run `benchmarks/run.py` against it, then stop the subject
whatever the outcome. Nothing here judges anything; the judge stays
`benchmarks/run.py`, called as its own process exactly as a person would call it.
"""
