"""Lightweight search projection for lexical and embedding-based recall."""

import json
import re
import sqlite3
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Protocol

import numpy as np
import sqlite_vec
from langchain_openai import OpenAIEmbeddings

from human_chat.memory.models import MemoryItem
from human_chat.memory.repository import MemoryNamespace, namespace_key


def lexical_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text.casefold())
    tokens: list[str] = []
    for word in words:
        if re.fullmatch(r"[\u4e00-\u9fff]+", word):
            tokens.extend(word)
            tokens.extend(word[index:index + 2] for index in range(len(word) - 1))
        else:
            tokens.append(word)
    return list(dict.fromkeys(tokens))


def _lexical_text(text: str) -> str:
    return " ".join(lexical_tokens(text))


def _identity_key(assertion) -> str | None:
    if assertion is None:
        return None
    return json.dumps(assertion.identity(), ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class SearchDocument:
    id: str
    parent_id: str
    parent_version: int
    version: int
    text: str
    status: str
    assertion: dict | None
    observed_at: str | None
    ended_at: str | None = None
    identity_key: str | None = None

    def payload(self) -> dict:
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "version": self.version,
            "text": self.text,
            "status": self.status,
            "assertion": self.assertion,
            "observed_at": self.observed_at,
            "ended_at": self.ended_at,
        }


def documents_for_item(item: MemoryItem) -> list[SearchDocument]:
    if item.status == "deleted":
        return []
    status = "current" if item.usable_now() else "revoked" if item.status == "retracted" else "expired"
    documents = [SearchDocument(
        id=f"{item.id}@{item.version}",
        parent_id=item.id,
        parent_version=item.version,
        version=item.version,
        text=item.text,
        status=status,
        assertion=item.assertion.model_dump(mode="json") if item.assertion else None,
        observed_at=str(item.observed_at) if item.observed_at else None,
        identity_key=_identity_key(item.assertion),
    )]
    documents.extend(SearchDocument(
        id=f"{item.id}@{version.version}",
        parent_id=item.id,
        parent_version=item.version,
        version=version.version,
        text=version.text,
        status=version.status,
        assertion=version.assertion.model_dump(mode="json") if version.assertion else None,
        observed_at=str(version.observed_at) if version.observed_at else None,
        ended_at=str(version.ended_at) if version.ended_at else None,
        identity_key=_identity_key(version.assertion),
    ) for version in item.history)
    return documents


class MemorySearch(Protocol):
    @property
    def semantic_enabled(self) -> bool:
        ...

    def upsert_item(self, item: MemoryItem) -> None:
        ...

    def search_lexical(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        ...

    def search_semantic(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        ...

    def search_identity(self, identity: tuple[str, ...], statuses: tuple[str, ...]) -> list[SearchDocument]:
        ...

    def current_documents(self) -> list[SearchDocument]:
        ...

    def has_documents(self) -> bool:
        ...


class SqliteMemorySearch:
    """Embedded search projection: FTS5 for BM25 and sqlite-vec for KNN."""

    def __init__(self, path: Path | None, embeddings=None, configuration: str = ""):
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.embeddings = embeddings
        self.configuration = sha256(configuration.encode()).hexdigest() if configuration else ""
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = RLock()
        self._db.execute("PRAGMA journal_mode=WAL")
        if embeddings is not None:
            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
            self._db.enable_load_extension(False)
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS search_documents (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                view_id TEXT NOT NULL UNIQUE,
                parent_id TEXT NOT NULL,
                parent_version INTEGER NOT NULL,
                view_version INTEGER NOT NULL,
                text TEXT NOT NULL,
                lexical_text TEXT NOT NULL,
                status TEXT NOT NULL,
                identity_key TEXT,
                assertion_json TEXT,
                observed_at TEXT,
                ended_at TEXT
            );
            CREATE INDEX IF NOT EXISTS search_documents_parent ON search_documents(parent_id);
            CREATE INDEX IF NOT EXISTS search_documents_identity ON search_documents(identity_key,status);
            CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(lexical_text);
            CREATE TABLE IF NOT EXISTS search_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        self._db.commit()

    @property
    def semantic_enabled(self) -> bool:
        return self.embeddings is not None

    def _vector_table_exists(self) -> bool:
        return self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='search_vectors'"
        ).fetchone() is not None

    def _ensure_vector_table(self, dimension: int) -> None:
        stored = dict(self._db.execute("SELECT key,value FROM search_metadata").fetchall())
        if stored:
            if stored.get("configuration") != self.configuration or int(stored.get("dimension", 0)) != dimension:
                raise RuntimeError("Memory search embedding configuration changed; rebuild the search projection explicitly")
            return
        self._db.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS search_vectors USING vec0(embedding float[{int(dimension)}] distance_metric=cosine)"
        )
        self._db.executemany(
            "INSERT INTO search_metadata(key,value) VALUES(?,?)",
            (("configuration", self.configuration), ("dimension", str(dimension))),
        )

    def upsert_item(self, item: MemoryItem) -> None:
        desired = {document.id: document for document in documents_for_item(item)}
        with self._lock:
            existing_rows = self._db.execute(
                "SELECT row_id,view_id,text FROM search_documents WHERE parent_id=?",
                (item.id,),
            ).fetchall()
            existing = {row["view_id"]: row for row in existing_rows}
            vector_rows = set()
            if self.semantic_enabled and self._vector_table_exists() and existing_rows:
                placeholders = ",".join("?" for _ in existing_rows)
                vector_rows = {row[0] for row in self._db.execute(
                    f"SELECT rowid FROM search_vectors WHERE rowid IN ({placeholders})",
                    tuple(row["row_id"] for row in existing_rows),
                ).fetchall()}

        needs_embedding = [document for document in desired.values()
                           if document.id not in existing
                           or existing[document.id]["text"] != document.text
                           or existing[document.id]["row_id"] not in vector_rows]
        vectors = []
        if self.semantic_enabled and needs_embedding:
            vectors = self.embeddings.embed_documents([document.text for document in needs_embedding])
            if len(vectors) != len(needs_embedding):
                raise ValueError("Embedding response count does not match memory documents")
        vector_by_view = dict(zip((document.id for document in needs_embedding), vectors))

        with self._lock, self._db:
            if vectors:
                self._ensure_vector_table(len(vectors[0]))
            for view_id, row in existing.items():
                if view_id in desired:
                    continue
                if self._vector_table_exists():
                    self._db.execute("DELETE FROM search_vectors WHERE rowid=?", (row["row_id"],))
                self._db.execute("DELETE FROM search_fts WHERE rowid=?", (row["row_id"],))
                self._db.execute("DELETE FROM search_documents WHERE row_id=?", (row["row_id"],))
            for document in desired.values():
                row = existing.get(document.id)
                values = (
                    document.parent_id, document.parent_version, document.version,
                    document.text, _lexical_text(document.text), document.status,
                    document.identity_key,
                    json.dumps(document.assertion, ensure_ascii=False) if document.assertion else None,
                    document.observed_at, document.ended_at,
                )
                if row:
                    row_id = row["row_id"]
                    self._db.execute("""UPDATE search_documents SET
                        parent_id=?,parent_version=?,view_version=?,text=?,lexical_text=?,status=?,
                        identity_key=?,assertion_json=?,observed_at=?,ended_at=? WHERE row_id=?""",
                        (*values, row_id))
                    self._db.execute("DELETE FROM search_fts WHERE rowid=?", (row_id,))
                else:
                    cursor = self._db.execute("""INSERT INTO search_documents(
                        view_id,parent_id,parent_version,view_version,text,lexical_text,status,
                        identity_key,assertion_json,observed_at,ended_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (document.id, *values))
                    row_id = cursor.lastrowid
                self._db.execute(
                    "INSERT INTO search_fts(rowid,lexical_text) VALUES(?,?)",
                    (row_id, _lexical_text(document.text)),
                )
                if document.id in vector_by_view:
                    if row and self._vector_table_exists():
                        self._db.execute("DELETE FROM search_vectors WHERE rowid=?", (row_id,))
                    self._db.execute(
                        "INSERT INTO search_vectors(rowid,embedding) VALUES(?,?)",
                        (row_id, sqlite_vec.serialize_float32(vector_by_view[document.id])),
                    )

    def search_lexical(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        tokens = lexical_tokens(query)
        if not tokens:
            return []
        match = " OR ".join(f'"{token}"' for token in tokens)
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._db.execute(
                f"""SELECT d.* FROM search_fts
                    JOIN search_documents d ON d.row_id=search_fts.rowid
                    WHERE search_fts MATCH ? AND d.status IN ({placeholders})
                    ORDER BY bm25(search_fts) LIMIT ?""",
                (match, *statuses, limit),
            ).fetchall()
        return [_sqlite_document(row) for row in rows]

    def search_semantic(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        if not self.semantic_enabled or not self._vector_table_exists():
            return []
        vector = self.embeddings.embed_query(query)
        placeholders = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._db.execute(
                f"""WITH nearest AS (
                        SELECT rowid,distance FROM search_vectors
                        WHERE embedding MATCH ? AND k = ?
                    )
                    SELECT d.*,nearest.distance FROM nearest
                    JOIN search_documents d ON d.row_id=nearest.rowid
                    WHERE d.status IN ({placeholders})
                    ORDER BY nearest.distance LIMIT ?""",
                (sqlite_vec.serialize_float32(vector), limit * 4, *statuses, limit),
            ).fetchall()
        return [_sqlite_document(row) for row in rows]

    def search_identity(self, identity: tuple[str, ...], statuses: tuple[str, ...]) -> list[SearchDocument]:
        placeholders = ",".join("?" for _ in statuses)
        key = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM search_documents WHERE identity_key=? AND status IN ({placeholders})",
                (key, *statuses),
            ).fetchall()
        return [_sqlite_document(row) for row in rows]

    def current_documents(self) -> list[SearchDocument]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM search_documents WHERE status='current' ORDER BY row_id"
            ).fetchall()
        return [_sqlite_document(row) for row in rows]

    def has_documents(self) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM search_documents LIMIT 1").fetchone() is not None

    def close(self) -> None:
        self._db.close()


class PostgresMemorySearch:
    """Production search projection using PostgreSQL FTS and pgvector HNSW."""

    def __init__(self, uri: str, namespace: MemoryNamespace, embeddings=None, configuration: str = ""):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as exc:
            raise RuntimeError("PostgreSQL memory search requires psycopg and pgvector") from exc
        self.embeddings = embeddings
        self.namespace = namespace_key(namespace)
        self.configuration = sha256(configuration.encode()).hexdigest() if configuration else ""
        self._lock = RLock()
        self._db = psycopg.connect(uri, autocommit=True)
        with self._db.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._db)
        with self._db.cursor() as cursor:
            cursor.execute("""CREATE TABLE IF NOT EXISTS human_chat_memory_search_documents (
                row_id BIGSERIAL PRIMARY KEY,
                namespace TEXT NOT NULL,
                view_id TEXT NOT NULL,
                parent_id TEXT NOT NULL,
                parent_version INTEGER NOT NULL,
                view_version INTEGER NOT NULL,
                text TEXT NOT NULL,
                lexical_text TEXT NOT NULL,
                status TEXT NOT NULL,
                identity_key TEXT,
                assertion_json JSONB,
                observed_at TEXT,
                ended_at TEXT,
                UNIQUE(namespace,view_id)
            )""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS human_chat_memory_search_fts
                ON human_chat_memory_search_documents
                USING GIN(to_tsvector('simple',lexical_text))""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS human_chat_memory_search_parent
                ON human_chat_memory_search_documents(namespace,parent_id)""")
            cursor.execute("""CREATE INDEX IF NOT EXISTS human_chat_memory_search_identity
                ON human_chat_memory_search_documents(namespace,identity_key,status)""")
            cursor.execute("""CREATE TABLE IF NOT EXISTS human_chat_memory_search_metadata (
                key TEXT PRIMARY KEY,value TEXT NOT NULL
            )""")

    @property
    def semantic_enabled(self) -> bool:
        return self.embeddings is not None

    def _vector_table_exists(self, cursor) -> bool:
        cursor.execute("SELECT to_regclass('human_chat_memory_search_vectors')")
        return cursor.fetchone()[0] is not None

    def _ensure_vector_table(self, cursor, dimension: int) -> None:
        cursor.execute("SELECT key,value FROM human_chat_memory_search_metadata")
        stored = dict(cursor.fetchall())
        if stored:
            if stored.get("configuration") != self.configuration or int(stored.get("dimension", 0)) != dimension:
                raise RuntimeError("Memory search embedding configuration changed; rebuild the search projection explicitly")
            return
        cursor.execute(f"""CREATE TABLE human_chat_memory_search_vectors (
            row_id BIGINT PRIMARY KEY REFERENCES human_chat_memory_search_documents(row_id) ON DELETE CASCADE,
            embedding vector({int(dimension)}) NOT NULL
        )""")
        cursor.execute("""CREATE INDEX human_chat_memory_search_hnsw
            ON human_chat_memory_search_vectors USING hnsw(embedding vector_cosine_ops)""")
        cursor.executemany(
            "INSERT INTO human_chat_memory_search_metadata(key,value) VALUES(%s,%s)",
            (("configuration", self.configuration), ("dimension", str(dimension))),
        )

    def upsert_item(self, item: MemoryItem) -> None:
        desired = {document.id: document for document in documents_for_item(item)}
        with self._lock, self._db.cursor() as cursor:
            cursor.execute("""SELECT row_id,view_id,text FROM human_chat_memory_search_documents
                WHERE namespace=%s AND parent_id=%s""", (self.namespace, item.id))
            existing = {row[1]: {"row_id": row[0], "text": row[2]} for row in cursor.fetchall()}
            vector_rows = set()
            if self.semantic_enabled and self._vector_table_exists(cursor) and existing:
                cursor.execute("SELECT row_id FROM human_chat_memory_search_vectors WHERE row_id=ANY(%s)",
                               ([row["row_id"] for row in existing.values()],))
                vector_rows = {row[0] for row in cursor.fetchall()}
        needs_embedding = [document for document in desired.values()
                           if document.id not in existing
                           or existing[document.id]["text"] != document.text
                           or existing[document.id]["row_id"] not in vector_rows]
        vectors = self.embeddings.embed_documents([document.text for document in needs_embedding]) \
            if self.semantic_enabled and needs_embedding else []
        if len(vectors) != len(needs_embedding) and self.semantic_enabled:
            raise ValueError("Embedding response count does not match memory documents")
        vector_by_view = dict(zip((document.id for document in needs_embedding), vectors))

        with self._lock, self._db.transaction(), self._db.cursor() as cursor:
            if vectors:
                self._ensure_vector_table(cursor, len(vectors[0]))
            removed = [row["row_id"] for view_id, row in existing.items() if view_id not in desired]
            if removed:
                cursor.execute("DELETE FROM human_chat_memory_search_documents WHERE row_id=ANY(%s)", (removed,))
            for document in desired.values():
                cursor.execute("""INSERT INTO human_chat_memory_search_documents(
                    namespace,view_id,parent_id,parent_version,view_version,text,lexical_text,status,
                    identity_key,assertion_json,observed_at,ended_at)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT(namespace,view_id) DO UPDATE SET
                      parent_id=excluded.parent_id,parent_version=excluded.parent_version,
                      view_version=excluded.view_version,text=excluded.text,lexical_text=excluded.lexical_text,
                      status=excluded.status,identity_key=excluded.identity_key,
                      assertion_json=excluded.assertion_json,observed_at=excluded.observed_at,ended_at=excluded.ended_at
                    RETURNING row_id""",
                    (self.namespace, document.id, document.parent_id, document.parent_version,
                     document.version, document.text, _lexical_text(document.text), document.status,
                     document.identity_key, json.dumps(document.assertion) if document.assertion else None,
                     document.observed_at, document.ended_at))
                row_id = cursor.fetchone()[0]
                if document.id in vector_by_view:
                    cursor.execute("""INSERT INTO human_chat_memory_search_vectors(row_id,embedding)
                        VALUES(%s,%s) ON CONFLICT(row_id) DO UPDATE SET embedding=excluded.embedding""",
                        (row_id, np.asarray(vector_by_view[document.id], dtype=np.float32)))

    def search_lexical(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        tokens = lexical_tokens(query)
        if not tokens:
            return []
        tsquery = " | ".join(tokens)
        with self._lock, self._db.cursor() as cursor:
            cursor.execute("""SELECT view_id,parent_id,parent_version,view_version,text,status,
                assertion_json,observed_at,ended_at,identity_key
                FROM human_chat_memory_search_documents
                WHERE namespace=%s AND status=ANY(%s)
                  AND to_tsvector('simple',lexical_text) @@ to_tsquery('simple',%s)
                ORDER BY ts_rank_cd(to_tsvector('simple',lexical_text),to_tsquery('simple',%s)) DESC
                LIMIT %s""", (self.namespace, list(statuses), tsquery, tsquery, limit))
            rows = cursor.fetchall()
        return [_postgres_document(row) for row in rows]

    def search_semantic(self, query: str, limit: int, statuses: tuple[str, ...]) -> list[SearchDocument]:
        if not self.semantic_enabled:
            return []
        vector = np.asarray(self.embeddings.embed_query(query), dtype=np.float32)
        with self._lock, self._db.cursor() as cursor:
            if not self._vector_table_exists(cursor):
                return []
            cursor.execute("""SELECT d.view_id,d.parent_id,d.parent_version,d.view_version,d.text,d.status,
                d.assertion_json,d.observed_at,d.ended_at,d.identity_key
                FROM human_chat_memory_search_vectors v
                JOIN human_chat_memory_search_documents d ON d.row_id=v.row_id
                WHERE d.namespace=%s AND d.status=ANY(%s)
                ORDER BY v.embedding <=> %s LIMIT %s""",
                (self.namespace, list(statuses), vector, limit))
            rows = cursor.fetchall()
        return [_postgres_document(row) for row in rows]

    def search_identity(self, identity: tuple[str, ...], statuses: tuple[str, ...]) -> list[SearchDocument]:
        key = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._db.cursor() as cursor:
            cursor.execute("""SELECT view_id,parent_id,parent_version,view_version,text,status,
                assertion_json,observed_at,ended_at,identity_key
                FROM human_chat_memory_search_documents
                WHERE namespace=%s AND identity_key=%s AND status=ANY(%s)""",
                (self.namespace, key, list(statuses)))
            rows = cursor.fetchall()
        return [_postgres_document(row) for row in rows]

    def current_documents(self) -> list[SearchDocument]:
        with self._lock, self._db.cursor() as cursor:
            cursor.execute("""SELECT view_id,parent_id,parent_version,view_version,text,status,
                assertion_json,observed_at,ended_at,identity_key
                FROM human_chat_memory_search_documents
                WHERE namespace=%s AND status='current' ORDER BY row_id""", (self.namespace,))
            rows = cursor.fetchall()
        return [_postgres_document(row) for row in rows]

    def has_documents(self) -> bool:
        with self._lock, self._db.cursor() as cursor:
            cursor.execute("SELECT 1 FROM human_chat_memory_search_documents WHERE namespace=%s LIMIT 1",
                           (self.namespace,))
            return cursor.fetchone() is not None

    def close(self) -> None:
        self._db.close()


def _sqlite_document(row) -> SearchDocument:
    return SearchDocument(
        id=row["view_id"], parent_id=row["parent_id"], parent_version=row["parent_version"],
        version=row["view_version"], text=row["text"], status=row["status"],
        assertion=json.loads(row["assertion_json"]) if row["assertion_json"] else None,
        observed_at=row["observed_at"], ended_at=row["ended_at"], identity_key=row["identity_key"],
    )


def _postgres_document(row) -> SearchDocument:
    assertion = row[6]
    if isinstance(assertion, str):
        assertion = json.loads(assertion)
    return SearchDocument(
        id=row[0], parent_id=row[1], parent_version=row[2], version=row[3], text=row[4], status=row[5],
        assertion=assertion, observed_at=row[7], ended_at=row[8], identity_key=row[9],
    )


def create_embeddings(settings):
    return OpenAIEmbeddings(
        model=settings.memory_embedding_model,
        base_url=settings.memory_embedding_base_url or settings.llm_base_url,
        api_key=settings.memory_embedding_api_key or settings.openai_api_key,
        check_embedding_ctx_length=False,
        chunk_size=10,
        max_retries=1,
        request_timeout=settings.llm_timeout_seconds,
        model_kwargs={"encoding_format": "float"},
    )
