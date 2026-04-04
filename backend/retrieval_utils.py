"""
retrieval_utils.py

Shared retrieval / ranking utilities used by both ResearchContext and
SearchResultStore.  Extracting these here eliminates the duplicated TF-IDF
implementation that previously existed in context.py and
search_result_store.py.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import List

import numpy as np


def tfidf_similarity(query: str, corpus: List[str]) -> List[float]:
    """
    Return cosine similarity scores between *query* and each text in *corpus*
    using TF-IDF vectors computed over the combined query+corpus vocabulary.

    Uses only ``numpy`` — no ML model or external dependencies required.
    Effective for domain-specific research text where vocabulary overlap is a
    reliable signal of topical relevance.

    Parameters
    ----------
    query:
        The search / step-description string to score against.
    corpus:
        List of document strings to rank.

    Returns
    -------
    List[float]
        Cosine similarity scores in the same order as *corpus*.
        All values are in [0, 1].  Returns a list of zeros if either
        *query* or all corpus documents are empty.
    """
    texts = [query] + corpus  # row 0 = query, rows 1..N = corpus docs

    def _tokenize(s: str) -> List[str]:
        return re.findall(r"[a-z0-9]+", s.lower())

    tokenized = [_tokenize(t) for t in texts]
    vocab = sorted({tok for doc in tokenized for tok in doc})
    if not vocab:
        return [0.0] * len(corpus)

    vocab_idx = {tok: i for i, tok in enumerate(vocab)}
    n, v = len(texts), len(vocab)

    # Term-frequency matrix
    tf = np.zeros((n, v), dtype=np.float32)
    for i, doc_tokens in enumerate(tokenized):
        if not doc_tokens:
            continue
        counts = Counter(doc_tokens)
        for tok, cnt in counts.items():
            j = vocab_idx.get(tok)
            if j is not None:
                tf[i, j] = cnt / len(doc_tokens)

    # Smooth IDF: log((N+1) / (df+1)) + 1
    df = np.sum(tf > 0, axis=0).astype(np.float32)
    idf = np.log((n + 1) / (df + 1)) + 1

    # TF-IDF, then L2 normalize each row
    tfidf = tf * idf
    norms = np.linalg.norm(tfidf, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    tfidf_norm = tfidf / norms

    # Cosine similarity: query row vs every corpus row
    return tfidf_norm[1:].dot(tfidf_norm[0]).tolist()
