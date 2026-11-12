"""AI-013 BM25 retrieval without article caps, isolated from baseline."""
from collections import Counter
import math
import re

def tokenize(text):
    return re.findall(r'[а-яёa-z0-9]+', text.lower())

def retrieve(text, history, version, kb):
    docs = [item for item in kb if item['status'] == 'approved' and item['version'] == version]
    if not docs:
        return []
    query = set(tokenize(text + ' ' + ' '.join(item['text'] for item in history)))
    bags = [Counter(tokenize(item['title'] + ' ' + item['excerpt'])) for item in docs]
    average = sum(sum(bag.values()) for bag in bags) / len(bags)
    frequency = Counter(term for bag in bags for term in bag)
    ranked = []
    for doc, bag in zip(docs, bags):
        score = sum(math.log(1 + (len(docs) - frequency[t] + .5) / (frequency[t] + .5)) *
                    bag[t] * 2.5 / (bag[t] + 1.5 * (.25 + .75 * sum(bag.values()) / average))
                    for t in query & bag.keys())
        if score > 0:
            ranked.append((score, doc['source_id'], doc))
    return [doc for _, _, doc in sorted(ranked, key=lambda value: (-value[0], value[1]))]
