"""(P17) Long-term user memory + ReasoningBank on SQLite (FTS5 trigram) and the quick-tier LLM.

``store`` (rows + FTS kept in sync), ``engine`` (``MemoryEngine``: recall / learn with
deterministic guards), ``bank`` (``ReasoningBank``: distill / retrieve / record_use / settle),
``tools`` (``user_memory_search``). Contracts: docs/contracts.md §13.
"""
