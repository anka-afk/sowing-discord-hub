from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field


@dataclass
class _ACNode:
    children: dict[str, _ACNode] = field(default_factory=dict)
    fail: _ACNode | None = None
    outputs: list[str] = field(default_factory=list)


class AhoCorasickFilter:
    """Pure-Python Aho-Corasick multi-pattern matcher for fast sensitive word screening."""

    def __init__(self, words: list[str] | None = None, *, case_insensitive: bool = True) -> None:
        self._case_insensitive = case_insensitive
        self._root = _ACNode()
        self._built = False
        self._word_count = 0
        if words:
            for w in words:
                self.add_word(w)
            self.build()

    @property
    def word_count(self) -> int:
        return self._word_count

    def _normalize(self, text: str) -> str:
        return text.lower() if self._case_insensitive else text

    def add_word(self, word: str) -> None:
        cleaned = word.strip()
        if not cleaned:
            return
        norm = self._normalize(cleaned)
        node = self._root
        for ch in norm:
            nxt = node.children.get(ch)
            if nxt is None:
                nxt = _ACNode()
                node.children[ch] = nxt
            node = nxt
        if cleaned not in node.outputs:
            node.outputs.append(cleaned)
            self._word_count += 1
        self._built = False

    def build(self) -> None:
        queue: deque[_ACNode] = deque()
        self._root.fail = self._root
        for child in self._root.children.values():
            child.fail = self._root
            queue.append(child)

        while queue:
            current = queue.popleft()
            for ch, nxt in current.children.items():
                fail_node = current.fail
                while fail_node is not None and fail_node is not self._root and ch not in fail_node.children:
                    fail_node = fail_node.fail
                if fail_node is not None and ch in fail_node.children and fail_node.children[ch] is not nxt:
                    nxt.fail = fail_node.children[ch]
                else:
                    nxt.fail = self._root
                if nxt.fail and nxt.fail.outputs:
                    nxt.outputs = list(dict.fromkeys(nxt.outputs + nxt.fail.outputs))
                queue.append(nxt)

        self._built = True

    def search(self, text: str) -> list[str]:
        if self._word_count == 0 or not text:
            return []
        if not self._built:
            self.build()

        hits: list[str] = []
        seen: set[str] = set()
        node = self._root
        for ch in self._normalize(text):
            while node is not self._root and ch not in node.children:
                node = node.fail or self._root
            if ch in node.children:
                node = node.children[ch]
            for word in node.outputs:
                if word not in seen:
                    seen.add(word)
                    hits.append(word)
        return hits

    def contains_sensitive(self, text: str) -> bool:
        return bool(self.search(text))
