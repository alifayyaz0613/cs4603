# aurora_common.py
# ---------------------------------------------------------------------------
# Shared bootstrap + helpers for the wk4 CAPSTONE project: "Aurora" — an AI
# Customer Support Operations Center.
#
# Usage inside any capstone notebook (first cell):
#
#     %run aurora_common.py
#
# This gives you, in the notebook namespace:
#   - config, llm, llm_noreason, embeddings
#   - Postgres + PGVector connection strings (PGVECTOR_CONN, PG_CHECKPOINTER_CONN)
#   - relational helpers:  seed_orders_db(), get_orders_sqldatabase(), get_engine()
#   - knowledge base:      seed_knowledge_base(), get_knowledge_retriever()
#   - persistence:         create_pg_checkpointer(), make_thread_config()
#   - observability:       enable_mlflow_tracing()
#   - common imports:      create_agent, tool, HumanMessage/AIMessage/SystemMessage, ...
#
# Everything uses BRAND-NEW, dedicated Postgres databases for this project,
# created on the same Postgres server the rest of the course uses. Two DBs are
# kept separate on purpose so students can see each concern:
#   • aurora_db              -> relational store + PGVector vector store
#   • aurora_checkpoints_db  -> agent/graph checkpoints
# Use a Postgres database with the pgvector extension for both relational and
# vector-store needs. Call create_project_databases() once (the setup notebook
# does this) to provision them, with the pgvector extension on aurora_db.
# ---------------------------------------------------------------------------
from dataclasses import dataclass
import os
import uuid
import warnings

from dotenv import load_dotenv

from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough, RunnableLambda
from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_community.utilities import SQLDatabase
from langchain.agents import create_agent

from sqlalchemy import create_engine, text

from pprintpp import pprint as pp


# ---------------------------------------------------------------------------
# Connections — TWO brand-new project databases, kept separate on purpose so
# students can clearly see each concern:
#   • aurora_db              -> relational tables + PGVector vector store
#   • aurora_checkpoints_db  -> agent/graph checkpoints (conversation state)
# Both are created via create_project_databases() (the setup notebook does this).
# ---------------------------------------------------------------------------
PG_BASE = "langchain:langchain!@localhost:5432"
AURORA_DB = "aurora_db"                          # relational + vectors
AURORA_CHECKPOINT_DB = "aurora_checkpoints_db"   # agent checkpoints (isolated)

# A maintenance database to connect to when issuing CREATE DATABASE.
PG_ADMIN_CONN = f"postgresql://{PG_BASE}/lc_vector_db"

PGVECTOR_CONN = f"postgresql+psycopg://{PG_BASE}/{AURORA_DB}"          # vectors + relational
PG_CHECKPOINTER_CONN = f"postgresql://{PG_BASE}/{AURORA_CHECKPOINT_DB}"  # agent checkpoints

KB_COLLECTION = "aurora_help_center"
ORDERS_TABLES = ["customers", "products", "orders", "order_items"]


# ---------------------------------------------------------------------------
# Logging helpers (mirrors the rest of the course).
# ---------------------------------------------------------------------------
def enable_logging():
    import logging
    logging.disable(logging.NOTSET)
    logging.basicConfig(level=logging.DEBUG, force=True)


def disable_logging():
    import logging
    logging.disable(logging.CRITICAL)


# ---------------------------------------------------------------------------
# Databricks / model configuration.
# ---------------------------------------------------------------------------
class MissingEnvironmentVariableError(ValueError):
    """Raised when one or more required environment variables are missing."""


@dataclass(frozen=True)
class DatabricksConfig:
    token: str
    host: str
    model: str


def get_databricks_config(validate: bool = True) -> DatabricksConfig:
    """Load Databricks settings from the repo-root .env file."""
    load_dotenv()
    token = os.environ.get("DATABRICKS_TOKEN", "")
    host = os.environ.get("DATABRICKS_HOST", "")
    model = os.environ.get("DATABRICKS_MODEL", "")

    if validate:
        missing = [
            name
            for name, value in {
                "DATABRICKS_TOKEN": token,
                "DATABRICKS_HOST": host,
                "DATABRICKS_MODEL": model,
            }.items()
            if not value
        ]
        if missing:
            raise MissingEnvironmentVariableError(
                f"Missing required environment variable(s): {', '.join(missing)}"
            )
    return DatabricksConfig(token=token, host=host, model=model)


def create_clients(config: DatabricksConfig):
    """Return (llm, llm_noreason, embeddings) wired to Databricks serving endpoints."""
    base_url = f"{config.host}/serving-endpoints"

    llm = ChatOpenAI(
        model=config.model,
        api_key=config.token,
        base_url=base_url,
        temperature=0,
    )
    llm_noreason = ChatOpenAI(
        model=config.model,
        api_key=config.token,
        base_url=base_url,
        reasoning_effort="none",
        temperature=0,
    )
    embeddings = OpenAIEmbeddings(
        model="databricks-gte-large-en",
        api_key=config.token,
        base_url=base_url,
        check_embedding_ctx_length=False,
    )
    return llm, llm_noreason, embeddings


def create_noreason_llm(model: str) -> ChatOpenAI:
    """Build a no-reasoning ChatOpenAI for a specific Databricks model name."""
    config = get_databricks_config(validate=True)
    return ChatOpenAI(
        model=model,
        api_key=config.token,
        base_url=f"{config.host}/serving-endpoints",
        reasoning_effort="none",
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Relational store — customers / products / orders / order_items in the
# dedicated project database (default `public` schema). Table access is
# restricted to these four via `include_tables`, so the PGVector tables
# (langchain_pg_*) stay hidden from the SQL agent.
# ---------------------------------------------------------------------------
def _create_database(name: str, enable_vector: bool = False) -> None:
    """Create a Postgres database by `name` if it doesn't exist (idempotent)."""
    import psycopg

    admin = psycopg.connect(PG_ADMIN_CONN, autocommit=True)
    try:
        exists = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (name,)
        ).fetchone()
        if exists:
            print(f"  • database '{name}' already exists")
        else:
            admin.execute(f'CREATE DATABASE "{name}" OWNER langchain')
            print(f"  • created database '{name}'")
    finally:
        admin.close()

    if enable_vector:
        proj = psycopg.connect(f"postgresql://{PG_BASE}/{name}", autocommit=True)
        try:
            proj.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print(f"  • ensured pgvector extension on '{name}'")
        finally:
            proj.close()


def create_data_database() -> None:
    """Create the `aurora_db` data database (relational + vectors) with pgvector."""
    _create_database(AURORA_DB, enable_vector=True)


def create_checkpoint_database() -> None:
    """Create the separate `aurora_checkpoints_db` database for agent state."""
    _create_database(AURORA_CHECKPOINT_DB, enable_vector=False)


def create_project_databases() -> None:
    """Provision BOTH project databases. Run once from the setup notebook."""
    print("Provisioning Aurora project databases:")
    create_data_database()
    create_checkpoint_database()
    print("Done.")


def get_engine():
    """SQLAlchemy engine for the project data database."""
    return create_engine(PGVECTOR_CONN)


_ORDERS_TABLES = ("order_items", "orders", "products", "customers")

_SEED_SQL = """
CREATE TABLE IF NOT EXISTS customers (
    customer_id   INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    tier          TEXT NOT NULL DEFAULT 'free'   -- 'free' | 'premium'
);

CREATE TABLE IF NOT EXISTS products (
    product_id    INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    price         NUMERIC(10,2) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id      INTEGER PRIMARY KEY,
    customer_id   INTEGER NOT NULL REFERENCES customers(customer_id),
    status        TEXT NOT NULL,                 -- placed | shipped | delivered | cancelled
    total         NUMERIC(10,2) NOT NULL,
    placed_at     DATE NOT NULL
);

CREATE TABLE IF NOT EXISTS order_items (
    order_id      INTEGER NOT NULL REFERENCES orders(order_id),
    product_id    INTEGER NOT NULL REFERENCES products(product_id),
    quantity      INTEGER NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
"""

_SEED_ROWS = {
    "customers": [
        (1, "Ada Lovelace", "ada@example.com", "premium"),
        (2, "Sam Carter", "sam@example.com", "free"),
        (3, "Mei Ling", "mei@example.com", "premium"),
    ],
    "products": [
        (10, "Widget-X Wireless Earbuds", 79.99),
        (11, "Aurora Smart Lamp", 49.50),
        (12, "TrailMate Backpack", 120.00),
    ],
    "orders": [
        (1001, 1, "shipped", 129.49, "2026-06-20"),
        (1002, 2, "delivered", 49.50, "2026-06-10"),
        (1003, 3, "placed", 240.00, "2026-06-29"),
    ],
    "order_items": [
        (1001, 10, 1),
        (1001, 11, 1),
        (1002, 11, 1),
        (1003, 12, 2),
    ],
}


def seed_orders_db(reset: bool = False) -> None:
    """Create the relational tables in the project database and insert sample rows.

    Idempotent: safe to run repeatedly. Pass reset=True to drop and rebuild.
    Ensures the `aurora_db` database exists first.
    """
    create_data_database()
    engine = get_engine()
    with engine.begin() as conn:
        if reset:
            for tbl in _ORDERS_TABLES:
                conn.execute(text(f"DROP TABLE IF EXISTS {tbl} CASCADE"))
        for stmt in filter(None, (s.strip() for s in _SEED_SQL.split(";"))):
            conn.execute(text(stmt))

        # Insert sample rows only if the tables are empty.
        existing = conn.execute(text("SELECT COUNT(*) FROM customers")).scalar()
        if existing:
            return

        conn.execute(
            text(
                "INSERT INTO customers (customer_id, name, email, tier) "
                "VALUES (:customer_id, :name, :email, :tier)"
            ),
            [dict(zip(("customer_id", "name", "email", "tier"), r)) for r in _SEED_ROWS["customers"]],
        )
        conn.execute(
            text(
                "INSERT INTO products (product_id, name, price) "
                "VALUES (:product_id, :name, :price)"
            ),
            [dict(zip(("product_id", "name", "price"), r)) for r in _SEED_ROWS["products"]],
        )
        conn.execute(
            text(
                "INSERT INTO orders (order_id, customer_id, status, total, placed_at) "
                "VALUES (:order_id, :customer_id, :status, :total, :placed_at)"
            ),
            [dict(zip(("order_id", "customer_id", "status", "total", "placed_at"), r)) for r in _SEED_ROWS["orders"]],
        )
        conn.execute(
            text(
                "INSERT INTO order_items (order_id, product_id, quantity) "
                "VALUES (:order_id, :product_id, :quantity)"
            ),
            [dict(zip(("order_id", "product_id", "quantity"), r)) for r in _SEED_ROWS["order_items"]],
        )


def get_orders_sqldatabase() -> SQLDatabase:
    """A SQLDatabase scoped to the four Aurora tables for SQL agents."""
    return SQLDatabase.from_uri(
        PGVECTOR_CONN,
        include_tables=ORDERS_TABLES,
        sample_rows_in_table_info=2,
    )


# ---------------------------------------------------------------------------
# Knowledge base — help-center policies embedded into PGVector (Postgres).
# ---------------------------------------------------------------------------
_HELP_CENTER_DOCS = [
    ("Returns are accepted within 30 days of delivery for unused items in original "
     "packaging. Opened electronics can be returned within 14 days.", {"topic": "returns"}),
    ("Refunds are issued to the original payment method within 5-7 business days once "
     "the returned item is received. Refunds over $100 require manager approval.", {"topic": "refunds"}),
    ("Standard shipping takes 3-5 business days. Express shipping takes 1-2 business days. "
     "Orders marked 'shipped' include a carrier tracking number.", {"topic": "shipping"}),
    ("All products include a 1-year limited warranty covering manufacturing defects. "
     "Accidental damage is not covered.", {"topic": "warranty"}),
    ("Premium members receive free express shipping, priority support, and a 60-day "
     "return window instead of 30 days.", {"topic": "membership"}),
    ("To cancel an order, it must still be in the 'placed' status. Orders that have "
     "shipped cannot be cancelled but can be returned after delivery.", {"topic": "cancellations"}),
]


def seed_knowledge_base(reset: bool = True) -> PGVector:
    """Embed the help-center policy snippets into a PGVector collection and return it."""
    create_data_database()
    texts = [t for t, _ in _HELP_CENTER_DOCS]
    metadatas = [m for _, m in _HELP_CENTER_DOCS]
    return PGVector.from_texts(
        texts=texts,
        embedding=embeddings,
        metadatas=metadatas,
        collection_name=KB_COLLECTION,
        connection=PGVECTOR_CONN,
        use_jsonb=True,
        pre_delete_collection=reset,
    )


def get_knowledge_retriever(k: int = 4):
    """Retriever over the existing help-center PGVector collection (no re-embedding)."""
    store = PGVector(
        embeddings=embeddings,
        collection_name=KB_COLLECTION,
        connection=PGVECTOR_CONN,
        use_jsonb=True,
    )
    return store.as_retriever(search_kwargs={"k": k})


# ---------------------------------------------------------------------------
# Persistence — Postgres checkpointer (one thread per support ticket).
# ---------------------------------------------------------------------------
def create_pg_checkpointer():
    """Persistent Postgres checkpointer for durable, resumable ticket threads.

    Uses the separate `aurora_checkpoints_db` database so agent state is cleanly
    isolated from the relational + vector data.
    """
    from psycopg import Connection
    from langgraph.checkpoint.postgres import PostgresSaver

    create_checkpoint_database()
    conn = Connection.connect(PG_CHECKPOINTER_CONN, autocommit=True, prepare_threshold=0)
    checkpointer = PostgresSaver(conn)
    checkpointer.setup()
    return checkpointer


def new_ticket_id() -> str:
    return f"ticket-{uuid.uuid4()}"


def make_thread_config(ticket_id: str | None = None) -> dict:
    """Config carrying a stable thread_id so a ticket's state persists across turns."""
    return {"configurable": {"thread_id": ticket_id or new_ticket_id()}}


# ---------------------------------------------------------------------------
# Observability — MLflow tracing for end-to-end ticket runs.
# ---------------------------------------------------------------------------
def enable_mlflow_tracing(experiment: str = "aurora-capstone"):
    """Turn on MLflow autologging/tracing for LangChain + LangGraph."""
    import mlflow

    mlflow.set_experiment(experiment)
    mlflow.langchain.autolog()
    return mlflow


# ---------------------------------------------------------------------------
# Bootstrap.
# ---------------------------------------------------------------------------
def bootstrap(validate: bool = True):
    """Return (config, (llm, llm_noreason), embeddings)."""
    config = get_databricks_config(validate=validate)
    llm, llm_noreason, embeddings = create_clients(config)
    return config, (llm, llm_noreason), embeddings


warnings.filterwarnings("ignore", module="pydantic")
try:
    from pydantic.warnings import PydanticDeprecatedSince20
    warnings.filterwarnings("ignore", category=PydanticDeprecatedSince20)
except Exception:
    pass

# When run via `%run aurora_common.py`, expose the ready-to-use objects.
config, (llm, llm_noreason), embeddings = bootstrap()
