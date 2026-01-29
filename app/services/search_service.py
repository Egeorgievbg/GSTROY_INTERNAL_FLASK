from __future__ import annotations

import re
from functools import lru_cache
from typing import Iterable

from flask import current_app

try:
    from elasticsearch import Elasticsearch, helpers
    from elasticsearch import exceptions as es_exceptions
except ImportError:  # pragma: no cover - optional dependency
    Elasticsearch = None
    helpers = None
    es_exceptions = Exception


CODE_PATTERN = re.compile(r"^[0-9A-Za-z\\-_/\\.]{3,}$")

TRANSLIT_MAP = {
    "\u0410": "A",
    "\u0411": "B",
    "\u0412": "V",
    "\u0413": "G",
    "\u0414": "D",
    "\u0415": "E",
    "\u0416": "Zh",
    "\u0417": "Z",
    "\u0418": "I",
    "\u0419": "Y",
    "\u041a": "K",
    "\u041b": "L",
    "\u041c": "M",
    "\u041d": "N",
    "\u041e": "O",
    "\u041f": "P",
    "\u0420": "R",
    "\u0421": "S",
    "\u0422": "T",
    "\u0423": "U",
    "\u0424": "F",
    "\u0425": "H",
    "\u0426": "Ts",
    "\u0427": "Ch",
    "\u0428": "Sh",
    "\u0429": "Sht",
    "\u042a": "A",
    "\u042c": "",
    "\u042e": "Yu",
    "\u042f": "Ya",
    "\u0430": "a",
    "\u0431": "b",
    "\u0432": "v",
    "\u0433": "g",
    "\u0434": "d",
    "\u0435": "e",
    "\u0436": "zh",
    "\u0437": "z",
    "\u0438": "i",
    "\u0439": "y",
    "\u043a": "k",
    "\u043b": "l",
    "\u043c": "m",
    "\u043d": "n",
    "\u043e": "o",
    "\u043f": "p",
    "\u0440": "r",
    "\u0441": "s",
    "\u0442": "t",
    "\u0443": "u",
    "\u0444": "f",
    "\u0445": "h",
    "\u0446": "ts",
    "\u0447": "ch",
    "\u0448": "sh",
    "\u0449": "sht",
    "\u044a": "a",
    "\u044c": "",
    "\u044e": "yu",
    "\u044f": "ya",
}

SUGGEST_FIELDS = [
    "name_suggest",
    "brand_suggest",
    "category_suggest",
    "primary_group_suggest",
    "secondary_group_suggest",
    "name_translit_suggest",
    "brand_translit_suggest",
    "category_translit_suggest",
    "primary_group_translit_suggest",
    "secondary_group_translit_suggest",
]

BG_SYNONYMS = [
    "\u0433\u0438\u043F\u0441\u043E\u043A\u0430\u0440\u0442\u043E\u043D, \u0433\u043A, \u0433\u0438\u043F\u0441 \u043A\u0430\u0440\u0442\u043E\u043D",
    "\u043B\u0430\u043C\u0438\u043D\u0438\u0440\u0430\u043D \u043F\u0430\u0440\u043A\u0435\u0442, \u043B\u0430\u043C\u0438\u043D\u0430\u0442",
    "\u0431\u043E\u0440\u043C\u0430\u0448\u0438\u043D\u0430, \u0434\u0440\u0435\u043B\u043A\u0430",
    "\u044A\u0433\u043B\u043E\u0448\u043B\u0430\u0439\u0444, \u0444\u043B\u0435\u043A\u0441",
    "\u0432\u0438\u043D\u0442\u043E\u0432\u0435\u0440\u0442, \u0448\u0443\u0440\u0443\u043F\u043E\u0432\u0435\u0440\u0442, \u0448\u0443\u0440\u0442",
    "\u043F\u0435\u0440\u0444\u043E\u0440\u0430\u0442\u043E\u0440, \u043A\u044A\u0440\u0442\u0430\u0447, \u043A\u044A\u0440\u0442\u0430\u0447\u043A\u0430",
    "\u0432\u0435\u0440\u0438\u0436\u0435\u043D \u0442\u0440\u0438\u043E\u043D, \u043C\u043E\u0442\u043E\u0440\u0435\u043D \u0442\u0440\u0438\u043E\u043D, \u0440\u0435\u0437\u0430\u0447\u043A\u0430",
    "\u0446\u0438\u0440\u043A\u0443\u043B\u044F\u0440, \u0434\u0438\u0441\u043A\u043E\u0432 \u0442\u0440\u0438\u043E\u043D",
    "\u0441\u0442\u0438\u0440\u043E\u043F\u043E\u0440, eps, \u0435\u043A\u0441\u043F\u0430\u043D\u0434\u0438\u0440\u0430\u043D \u043F\u043E\u043B\u0438\u0441\u0442\u0438\u0440\u043E\u043B",
    "\u0444\u0438\u0431\u0440\u0430\u043D, xps, \u0435\u043A\u0441\u0442\u0440\u0443\u0434\u0438\u0440\u0430\u043D \u043F\u043E\u043B\u0438\u0441\u0442\u0438\u0440\u043E\u043B",
    "\u043C\u0438\u043D\u0435\u0440\u0430\u043B\u043D\u0430 \u0432\u0430\u0442\u0430, \u043A\u0430\u043C\u0435\u043D\u043D\u0430 \u0432\u0430\u0442\u0430, \u0441\u0442\u044A\u043A\u043B\u0435\u043D\u0430 \u0432\u0430\u0442\u0430",
    "\u0432\u0430\u0442\u0435\u0440\u043F\u0430\u0441, \u043D\u0438\u0432\u0435\u043B\u0438\u0440",
    "\u0433\u0440\u0443\u043D\u0434, \u043F\u0440\u0430\u0439\u043C\u0435\u0440",
    "\u0433\u0435\u0440\u043C\u0435\u0442\u0438\u043A, \u0441\u0438\u043B\u0438\u043A\u043E\u043D",
    "\u043B\u0435\u043F\u0438\u043B\u043E, \u0442\u0443\u0442\u043A\u0430\u043B",
    "\u043F\u0432\u0446, pvc",
]

LETTER_PATTERN = re.compile(r"[A-Za-z\u0400-\u04FF]")
DIGIT_PATTERN = re.compile(r"\d")
SEPARATOR_PATTERN = re.compile(r"[\s\u00A0]+")
CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
LEET_PATTERN = re.compile(r"[463qwQW]")
FV_SWAP_TABLE = str.maketrans(
    {
        "f": "v",
        "v": "f",
        "F": "V",
        "V": "F",
        "\u0444": "\u0432",
        "\u0432": "\u0444",
        "\u0424": "\u0412",
        "\u0412": "\u0424",
    }
)


def _safe_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _safe_float(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def transliterate_bg_to_latin(value):
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    result = "".join(TRANSLIT_MAP.get(char, char) for char in text)
    result = result.strip()
    return result or None


@lru_cache(maxsize=1)
def _build_synonym_filters() -> tuple[list[str], list[str]]:
    bg_synonyms = [line for line in BG_SYNONYMS if line]
    latin_synonyms = []
    seen = set()
    for line in bg_synonyms:
        translit = transliterate_bg_to_latin(line)
        if not translit:
            continue
        normalized = translit.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        latin_synonyms.append(normalized)
    return bg_synonyms, latin_synonyms


def _letter_count(value):
    return len(LETTER_PATTERN.findall(value))


def _digit_count(value):
    return len(DIGIT_PATTERN.findall(value))


def _looks_like_code(value):
    if not value:
        return False
    if value.isdigit():
        return True
    if any(char in "-_/." for char in value):
        return True
    digit_count = _digit_count(value)
    if digit_count >= 3:
        return True
    if digit_count >= 1 and value == value.upper() and " " not in value:
        return True
    return False


def _normalize_query(value):
    if not value:
        return None
    normalized = SEPARATOR_PATTERN.sub(" ", value).strip()
    if not normalized or _looks_like_code(normalized):
        return normalized or None
    normalized = re.sub(r"[-_/\\.]+", " ", normalized)
    normalized = SEPARATOR_PATTERN.sub(" ", normalized).strip()
    return normalized or None


def _swap_fv(value):
    swapped = value.translate(FV_SWAP_TABLE)
    return swapped if swapped != value else None


def _replace_leet_digits(value):
    if "6" not in value and "4" not in value:
        return None
    replaced = re.sub(r"(?<!\\d)6(?!\\d)", "sh", value)
    replaced = re.sub(r"(?<!\\d)4(?!\\d)", "ch", replaced)
    return replaced if replaced != value else None


def _normalize_leet(value):
    if not value or not LEET_PATTERN.search(value):
        return None
    normalized = value.lower()
    normalized = normalized.replace("4", "ch")
    normalized = normalized.replace("6", "sh")
    normalized = normalized.replace("3", "z")
    normalized = normalized.replace("q", "ya")
    normalized = normalized.replace("w", "v")
    return normalized if normalized != value else None


def _latin_to_cyrillic_simple(value):
    if not value:
        return None
    text = value.lower()
    if CYRILLIC_PATTERN.search(text):
        return None
    replacements = [
        ("sht", "щ"),
        ("zh", "ж"),
        ("ch", "ч"),
        ("sh", "ш"),
        ("ts", "ц"),
        ("yu", "ю"),
        ("ya", "я"),
        ("a", "а"),
        ("b", "б"),
        ("v", "в"),
        ("g", "г"),
        ("d", "д"),
        ("e", "е"),
        ("z", "з"),
        ("i", "и"),
        ("y", "й"),
        ("k", "к"),
        ("l", "л"),
        ("m", "м"),
        ("n", "н"),
        ("o", "о"),
        ("p", "п"),
        ("r", "р"),
        ("s", "с"),
        ("t", "т"),
        ("u", "у"),
        ("f", "ф"),
        ("h", "х"),
    ]
    for latin, cyr in replacements:
        text = text.replace(latin, cyr)
    return text if text != value else None


def _should_expand_translit_variants(value):
    if not value:
        return False
    if len(value) > 40:
        return False
    if _looks_like_code(value):
        return False
    if _letter_count(value) < 2:
        return False
    return True


def _expand_latin_translit_variants(value, limit=6):
    if not value or CYRILLIC_PATTERN.search(value):
        return []
    rules = [
        (re.compile(r"ch", re.IGNORECASE), ["c", "4"]),
        (re.compile(r"ts", re.IGNORECASE), ["c", "tc"]),
        (re.compile(r"tc", re.IGNORECASE), ["ts", "c"]),
        (re.compile(r"4"), ["ch", "c"]),
        (re.compile(r"c(?!h)", re.IGNORECASE), ["ch", "ts"]),
    ]
    variants = []
    seen = {value}
    queue = [value]
    while queue and len(variants) < limit:
        current = queue.pop(0)
        for pattern, replacements in rules:
            if not pattern.search(current):
                continue
            for replacement in replacements:
                candidate = pattern.sub(replacement, current)
                if candidate in seen:
                    continue
                seen.add(candidate)
                variants.append(candidate)
                if len(variants) >= limit:
                    break
                queue.append(candidate)
            if len(variants) >= limit:
                break
    return variants


def _should_expand_typos(value):
    if not value:
        return False
    if len(value) > 40:
        return False
    if _looks_like_code(value):
        return False
    if _letter_count(value) < 3:
        return False
    return True


def expand_query_variants(value, limit=6):
    text = _safe_text(value)
    if not text:
        return []
    variants = []

    def add(variant):
        if variant and variant not in variants:
            variants.append(variant)

    add(text)
    normalized = _normalize_query(text)
    if normalized and normalized != text:
        add(normalized)
    translit = transliterate_bg_to_latin(text)
    if translit and translit != text:
        add(translit)
    leet_normalized = _normalize_leet(text)
    if leet_normalized and leet_normalized != text:
        add(leet_normalized)
        leet_cyrillic = _latin_to_cyrillic_simple(leet_normalized)
        add(leet_cyrillic)
    if not CYRILLIC_PATTERN.search(text):
        text_cyrillic = _latin_to_cyrillic_simple(text)
        add(text_cyrillic)
    if _should_expand_translit_variants(text):
        for variant in _expand_latin_translit_variants(text, limit=limit):
            add(variant)
    if _should_expand_typos(text):
        add(_swap_fv(text))
        leet_source = translit if translit and translit != text else text
        add(_replace_leet_digits(leet_source))
    return variants[:limit]


def expand_suggest_fields(fields):
    expanded = []
    for field in fields:
        expanded.append(field)
        expanded.append(f"{field}._2gram")
        expanded.append(f"{field}._3gram")
    return expanded


def _index_settings():
    bg_synonyms, latin_synonyms = _build_synonym_filters()
    return {
        "settings": {
            "analysis": {
                "char_filter": {
                    "bg_leet_filter": {
                        "type": "mapping",
                        "mappings": [
                            "4 => ch",
                            "6 => sh",
                            "3 => z",
                            "q => ya",
                            "w => v",
                            "v => v",
                        ],
                    }
                },
                "filter": {
                    "bg_stop": {"type": "stop", "stopwords": "_bulgarian_"},
                    "bg_stemmer": {"type": "stemmer", "language": "bulgarian"},
                    "bg_synonyms": {
                        "type": "synonym_graph",
                        "synonyms": bg_synonyms,
                    },
                    "latin_synonyms": {
                        "type": "synonym_graph",
                        "synonyms": latin_synonyms,
                    },
                    "bg_shingle": {
                        "type": "shingle",
                        "min_shingle_size": 2,
                        "max_shingle_size": 3,
                        "output_unigrams": True,
                    },
                    "code_edge_ngram": {
                        "type": "edge_ngram",
                        "min_gram": 2,
                        "max_gram": 20,
                    },
                    "translit_edge_ngram": {
                        "type": "edge_ngram",
                        "min_gram": 3,
                        "max_gram": 15,
                    },
                },
                "analyzer": {
                    "bg_index": {
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "asciifolding",
                            "bg_stop",
                            "bg_stemmer",
                            "bg_shingle",
                        ],
                    },
                    "custom_search_analyzer": {
                        "char_filter": ["bg_leet_filter"],
                        "tokenizer": "standard",
                        "filter": [
                            "lowercase",
                            "asciifolding",
                            "bg_synonyms",
                            "bg_stop",
                            "bg_stemmer",
                        ],
                    },
                    "latin_text": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding"],
                    },
                    "latin_search": {
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "latin_synonyms"],
                    },
                    "translit_index": {
                        "char_filter": ["bg_leet_filter"],
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "translit_edge_ngram"],
                    },
                    "translit_search": {
                        "char_filter": ["bg_leet_filter"],
                        "tokenizer": "standard",
                        "filter": ["lowercase", "asciifolding", "latin_synonyms"],
                    },
                    "code_edge": {
                        "tokenizer": "keyword",
                        "filter": ["lowercase", "code_edge_ngram"],
                    },
                    "code_search": {
                        "tokenizer": "keyword",
                        "filter": ["lowercase"],
                    },
                },
            }
        },
        "mappings": {
            "properties": {
                "id": {"type": "integer"},
                "item_number": {
                    "type": "keyword",
                    "fields": {
                        "edge": {
                            "type": "text",
                            "analyzer": "code_edge",
                            "search_analyzer": "code_search",
                        }
                    },
                },
                "barcode": {
                    "type": "keyword",
                    "fields": {
                        "edge": {
                            "type": "text",
                            "analyzer": "code_edge",
                            "search_analyzer": "code_search",
                        }
                    },
                },
                "catalog_number": {
                    "type": "keyword",
                    "fields": {
                        "edge": {
                            "type": "text",
                            "analyzer": "code_edge",
                            "search_analyzer": "code_search",
                        }
                    },
                },
                "name": {
                    "type": "text",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                    "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
                },
                "name_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "name_translit": {
                    "type": "text",
                    "analyzer": "translit_index",
                    "search_analyzer": "translit_search",
                },
                "name_translit_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "translit_index",
                    "search_analyzer": "translit_search",
                },
                "short_description": {
                    "type": "text",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "long_description": {
                    "type": "text",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "meta_description": {
                    "type": "text",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "brand": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "brand_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "brand_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "brand_translit_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "category": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "category_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "category_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "category_translit_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "primary_group": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "primary_group_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "primary_group_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "primary_group_translit_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "secondary_group": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "secondary_group_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "bg_index",
                    "search_analyzer": "custom_search_analyzer",
                },
                "secondary_group_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "secondary_group_translit_suggest": {
                    "type": "search_as_you_type",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "tertiary_group": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "tertiary_group_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "quaternary_group": {
                    "type": "keyword",
                    "fields": {
                        "text": {
                            "type": "text",
                            "analyzer": "bg_index",
                            "search_analyzer": "custom_search_analyzer",
                        }
                    },
                },
                "quaternary_group_translit": {
                    "type": "text",
                    "analyzer": "latin_text",
                    "search_analyzer": "latin_search",
                },
                "category_id": {"type": "integer"},
                "brand_id": {"type": "integer"},
                "is_active": {"type": "boolean"},
                "effective_price": {"type": "float"},
            }
        },
    }


class ProductSearchService:
    def __init__(self, app=None):
        self.app = app or current_app

    def is_enabled(self) -> bool:
        if Elasticsearch is None:
            return False
        return bool(self.app.config.get("ELASTICSEARCH_ENABLED", False))

    def _client(self):
        if Elasticsearch is None:
            return None
        url = self.app.config.get("ELASTICSEARCH_URL")
        if not url:
            return None
        timeout = self.app.config.get("ELASTICSEARCH_TIMEOUT", 5)
        verify_certs = bool(self.app.config.get("ELASTICSEARCH_VERIFY_CERTS", False))
        username = self.app.config.get("ELASTICSEARCH_USERNAME")
        password = self.app.config.get("ELASTICSEARCH_PASSWORD")
        kwargs = {
            "request_timeout": timeout,
            "verify_certs": verify_certs,
        }
        if username and password:
            kwargs["basic_auth"] = (username, password)
        return Elasticsearch(url, **kwargs)

    def ping(self) -> bool:
        client = self._client()
        if client is None:
            return False
        try:
            ok = bool(client.ping())
        except es_exceptions.ElasticsearchException:
            ok = False
        self.app.config["ELASTICSEARCH_AVAILABLE"] = ok
        return ok

    def _index_name(self) -> str:
        return self.app.config.get("ELASTICSEARCH_INDEX", "gstroy-products")

    def ensure_index(self) -> bool:
        if not self.is_enabled():
            return False
        client = self._client()
        if client is None:
            return False
        index = self._index_name()
        try:
            if client.indices.exists(index=index):
                self.app.config["ELASTICSEARCH_AVAILABLE"] = True
                return True
            client.indices.create(index=index, **_index_settings())
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
            return True
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch index setup failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return False

    def mapping_has_fields(self, fields: list[str]) -> bool:
        if not fields:
            return True
        client = self._client()
        if client is None:
            return False
        index = self._index_name()
        try:
            mapping = client.indices.get_mapping(index=index)
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch mapping check failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return False
        index_mapping = mapping.get(index, {}).get("mappings", {}).get("properties", {})
        self.app.config["ELASTICSEARCH_AVAILABLE"] = True
        return all(field in index_mapping for field in fields)

    def rebuild_index(self) -> bool:
        if not self.is_enabled():
            return False
        client = self._client()
        if client is None:
            return False
        index = self._index_name()
        try:
            if client.indices.exists(index=index):
                client.indices.delete(index=index)
            client.indices.create(index=index, **_index_settings())
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
            return True
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch rebuild failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return False

    def count_documents(self) -> int | None:
        if not self.is_enabled():
            return None
        client = self._client()
        if client is None:
            return None
        try:
            response = client.count(index=self._index_name())
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
            return int(response.get("count", 0))
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch count failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return None

    def build_document(self, product) -> dict:
        effective_price = (
            product.promo_price_unit_1
            or product.visible_price_unit_1
            or product.price_unit_1
            or product.price_unit_2
        )
        return {
            "id": product.id,
            "item_number": _safe_text(product.item_number),
            "barcode": _safe_text(product.barcode),
            "catalog_number": _safe_text(product.catalog_number),
            "name": _safe_text(product.name),
            "name_suggest": _safe_text(product.name),
            "name_translit": transliterate_bg_to_latin(product.name),
            "name_translit_suggest": transliterate_bg_to_latin(product.name),
            "short_description": _safe_text(product.short_description),
            "long_description": _safe_text(product.long_description),
            "meta_description": _safe_text(product.meta_description),
            "brand": _safe_text(product.brand),
            "brand_suggest": _safe_text(product.brand),
            "brand_translit": transliterate_bg_to_latin(product.brand),
            "brand_translit_suggest": transliterate_bg_to_latin(product.brand),
            "category": _safe_text(product.category),
            "category_suggest": _safe_text(product.category),
            "category_translit": transliterate_bg_to_latin(product.category),
            "category_translit_suggest": transliterate_bg_to_latin(product.category),
            "primary_group": _safe_text(product.primary_group),
            "primary_group_suggest": _safe_text(product.primary_group),
            "primary_group_translit": transliterate_bg_to_latin(product.primary_group),
            "primary_group_translit_suggest": transliterate_bg_to_latin(product.primary_group),
            "secondary_group": _safe_text(product.secondary_group),
            "secondary_group_suggest": _safe_text(product.secondary_group),
            "secondary_group_translit": transliterate_bg_to_latin(product.secondary_group),
            "secondary_group_translit_suggest": transliterate_bg_to_latin(product.secondary_group),
            "tertiary_group": _safe_text(product.tertiary_group),
            "tertiary_group_translit": transliterate_bg_to_latin(product.tertiary_group),
            "quaternary_group": _safe_text(product.quaternary_group),
            "quaternary_group_translit": transliterate_bg_to_latin(product.quaternary_group),
            "category_id": product.category_id,
            "brand_id": product.brand_id,
            "is_active": bool(product.is_active),
            "effective_price": _safe_float(effective_price),
        }

    def bulk_index(self, products: Iterable) -> int:
        if not self.is_enabled():
            return 0
        client = self._client()
        if client is None:
            return 0
        index = self._index_name()
        actions = (
            {
                "_index": index,
                "_id": product.id,
                "_source": self.build_document(product),
            }
            for product in products
        )
        try:
            success, _ = helpers.bulk(client, actions, raise_on_error=False)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
            return success or 0
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch bulk index failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return 0

    def search(
        self,
        query: str | None,
        item_number: str | None,
        brand: str | None,
        main_group: str | None,
        page: int,
        per_page: int,
        category_ids: list[int] | None = None,
        price_min: float | None = None,
        price_max: float | None = None,
        sort: str | None = None,
    ) -> tuple[list[int], int] | None:
        if not self.is_enabled():
            return None
        client = self._client()
        if client is None:
            return None

        text_query = _safe_text(query)
        code_query = _safe_text(item_number) or (text_query if text_query else None)
        text_len = len(text_query) if text_query else 0

        must = []
        filters = []

        if text_query:
            suggest_fields = expand_suggest_fields(SUGGEST_FIELDS)
            should = []
            for idx, variant in enumerate(expand_query_variants(text_query)):
                boost = 3.0 if idx == 0 else 1.0
                should.append(
                    {
                        "multi_match": {
                            "query": variant,
                            "type": "bool_prefix",
                            "fields": suggest_fields,
                            "boost": boost,
                        }
                    }
                )
            if text_len >= 4:
                should.append(
                    {
                        "multi_match": {
                            "query": text_query,
                            "type": "best_fields",
                            "tie_breaker": 0.3,
                            "fields": [
                                "name^5",
                                "name_translit^3",
                                "short_description^2",
                                "long_description",
                                "meta_description",
                                "item_number.edge^6",
                                "barcode.edge^6",
                                "catalog_number.edge^6",
                                "brand.text^2",
                                "brand_translit^2",
                                "category.text",
                                "category_translit",
                                "primary_group.text",
                                "primary_group_translit",
                                "secondary_group_translit",
                            ],
                            "operator": "and",
                            "minimum_should_match": "2<75%",
                            "fuzziness": "AUTO:3,6",
                        }
                    }
                )
            if code_query and CODE_PATTERN.match(code_query):
                should.extend(
                    [
                        {"term": {"item_number": {"value": code_query, "boost": 10.0}}},
                        {"term": {"barcode": {"value": code_query, "boost": 10.0}}},
                        {"term": {"catalog_number": {"value": code_query, "boost": 10.0}}},
                        {"match": {"item_number.edge": {"query": code_query, "boost": 5.0}}},
                        {"match": {"barcode.edge": {"query": code_query, "boost": 5.0}}},
                        {
                            "match": {
                                "catalog_number.edge": {"query": code_query, "boost": 5.0}
                            }
                        },
                    ]
                )
            should.extend(
                [
                    {
                        "match_phrase": {
                            "name": {"query": text_query, "boost": 6.0, "slop": 2}
                        }
                    },
                    {
                        "match_phrase": {
                            "name_translit": {
                                "query": text_query,
                                "boost": 4.0,
                                "slop": 2,
                            }
                        }
                    },
                    {
                        "match": {
                            "name_translit": {
                                "query": text_query,
                                "fuzziness": "AUTO:3,6",
                                "prefix_length": 1,
                                "boost": 2.5,
                            }
                        }
                    },
                ]
            )
            must.append({"bool": {"should": should, "minimum_should_match": 1}})

        if brand:
            filters.append({"term": {"brand": brand}})

        if main_group:
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"term": {"primary_group": main_group}},
                            {"term": {"category": main_group}},
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )

        if category_ids:
            filters.append({"terms": {"category_id": category_ids}})

        if price_min is not None:
            filters.append({"range": {"effective_price": {"gte": price_min}}})
        if price_max is not None:
            filters.append({"range": {"effective_price": {"lte": price_max}}})

        if sort == "newest":
            sort_clause = [{"id": "desc"}]
        elif sort == "price_asc":
            sort_clause = [{"effective_price": {"order": "asc", "missing": "_last"}}]
        elif sort == "price_desc":
            sort_clause = [{"effective_price": {"order": "desc", "missing": "_last"}}]
        elif text_query:
            sort_clause = [{"_score": "desc"}, {"name.keyword": "asc"}]
        else:
            sort_clause = [{"name.keyword": "asc"}]

        filters.append({"term": {"is_active": True}})
        base_query = {"bool": {"must": must or [{"match_all": {}}], "filter": filters}}
        body = {
            "from": max(page - 1, 0) * per_page,
            "size": per_page,
            "query": {
                "function_score": {
                    "query": base_query,
                    "functions": [
                        {"filter": {"term": {"is_active": True}}, "weight": 1.1}
                    ],
                    "score_mode": "sum",
                    "boost_mode": "sum",
                }
            },
            "sort": sort_clause,
        }

        try:
            response = client.search(index=self._index_name(), body=body)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch search failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return None

        hits = response.get("hits", {})
        total = hits.get("total", {}).get("value", 0)
        ids = [int(hit["_id"]) for hit in hits.get("hits", []) if hit.get("_id")]
        return ids, int(total)

    def suggest(self, query: str, limit: int = 8) -> list[dict]:
        if not self.is_enabled():
            return []
        client = self._client()
        if client is None:
            return []
        text_query = _safe_text(query)
        if not text_query:
            return []
        suggest_fields = expand_suggest_fields(SUGGEST_FIELDS)
        should = []
        for idx, variant in enumerate(expand_query_variants(text_query, limit=4)):
            boost = 3.0 if idx == 0 else 1.0
            should.append(
                {
                    "multi_match": {
                        "query": variant,
                        "type": "bool_prefix",
                        "fields": suggest_fields,
                        "boost": boost,
                    }
                }
            )
        body = {
            "size": max(1, min(limit, 20)),
            "_source": ["id", "item_number", "name", "brand", "category"],
            "query": {
                "bool": {
                    "should": should
                    + [
                        {"prefix": {"item_number": text_query}},
                        {"prefix": {"barcode": text_query}},
                        {"prefix": {"catalog_number": text_query}},
                    ],
                    "minimum_should_match": 1,
                }
            },
            "sort": [{"_score": "desc"}, {"name.keyword": "asc"}],
        }
        try:
            response = client.search(index=self._index_name(), body=body)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = True
        except es_exceptions.ElasticsearchException as exc:
            self.app.logger.warning("Elasticsearch suggest failed: %s", exc)
            self.app.config["ELASTICSEARCH_AVAILABLE"] = False
            return []
        hits = response.get("hits", {}).get("hits", [])
        results = []
        for hit in hits:
            source = hit.get("_source", {})
            results.append(
                {
                    "id": source.get("id"),
                    "item_number": source.get("item_number"),
                    "name": source.get("name"),
                    "brand": source.get("brand"),
                    "category": source.get("category"),
                }
            )
        return results
