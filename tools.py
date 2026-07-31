"""Tools: order lookup and policy retrieval.

Tools return facts — records, articles, or nothing. They never return prose
and never decide anything. The retrieval floor here fails closed: a query
that does not clearly match an article returns None, never the nearest one.
"""
from __future__ import annotations

import re
from typing import List, Optional

from store import ARTICLES, ORDERS, Article, Order

# A match below this many distinct keyword hits is a miss. Two is the floor
# because single-word overlap is how "customs for Ireland" once retrieved the
# domestic shipping article: one shared word is coincidence, not relevance.
MIN_KEYWORD_MATCHES = 2


def get_order(order_id: str) -> Optional[Order]:
    return ORDERS.get(order_id)


def orders_for_customer(customer_id: str) -> List[Order]:
    return [o for o in ORDERS.values() if o.customer_id == customer_id]


def delivered_orders(customer_id: str) -> List[Order]:
    """Return candidates for a return: only what has actually arrived."""
    return [
        o for o in orders_for_customer(customer_id) if o.status == "delivered"
    ]


def find_orders_by_title_word(customer_id: str, word: str) -> List[Order]:
    """Match a single title word against this customer's own orders only."""
    needle = word.lower()
    matches = []
    for order in orders_for_customer(customer_id):
        title_words = _tokenize(order.title)
        if needle in title_words:
            matches.append(order)
    return matches


def search_policy(query: str) -> Optional[Article]:
    """Whole-word keyword retrieval with a hard floor.

    Tokenizing the query (rather than substring matching) is what keeps
    "ship" from firing inside "shipping". Below the floor, or on a tie for
    best, we return None: a wrong-but-plausible article that reaches the
    customer is worse than an honest miss.
    """
    tokens = _tokenize(query)
    scored = []
    for article in ARTICLES:
        score = len(tokens & article.keywords)
        scored.append((score, article))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_article = scored[0]
    if best_score < MIN_KEYWORD_MATCHES:
        return None
    if len(scored) > 1 and scored[1][0] == best_score:
        return None
    return best_article


def _tokenize(text: str) -> set:
    """Lowercase whole words. Word boundaries come free with findall."""
    return set(re.findall(r"[a-z]+", text.lower()))
