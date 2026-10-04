"""Create, inspect and tear down the policy corpus.

The corpus is ephemeral by design (BUILD_PLAN 2.6):

    uv run python scripts/rag_corpus.py up | status | down | unprovision

Nothing here hardcodes a corpus ID. IDs change on every `up`, so the corpus is
always resolved by its stable display name.

SDK note: this uses the `agentplatform` client rather than the older
`vertexai.rag`, which is deprecated and, more importantly, cannot express
serverless mode -- the only mode available to new projects in several regions,
and the one without a provisioned Spanner instance. All SDK calls live in this
file and app/retrieval.py.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agentplatform
from agentplatform._genai import types as ap

from app import config

DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"


def _client() -> agentplatform.Client:
    if not config.PROJECT_ID:
        sys.exit("GOOGLE_CLOUD_PROJECT is not set. See .env.")
    # RAG Engine needs a real region. GOOGLE_CLOUD_LOCATION is deliberately not
    # used -- "global" is a model-serving value, not a corpus region.
    return agentplatform.Client(
        project=config.PROJECT_ID, location=config.RAG_CORPUS_LOCATION
    )


def _find_corpus(client):
    # list_corpora returns a response object; .rag_corpora is None when empty.
    for corpus in client.rag.list_corpora().rag_corpora or []:
        if corpus.display_name == config.RAG_CORPUS_DISPLAY_NAME:
            return corpus
    return None


def _files(client, corpus_name):
    return client.rag.list_files(name=corpus_name).rag_files or []


def _policy_pdfs() -> list[Path]:
    pdfs = sorted(DOCS_DIR / name for name in config.RETURNS_POLICY_DOCS
                  if (DOCS_DIR / name).is_file())
    if not pdfs:
        sys.exit(f"No PDFs in {DOCS_DIR}. Drop the policy documents there first.")
    return pdfs


def _mode(client) -> str:
    """Which RagManagedDb mode this project+location is in.

    This is what bills, and it is a project-level singleton -- not a property of
    our corpus. Deleting the corpus does not change it. See BUILD_PLAN 2.5.
    """
    try:
        cfg = client.rag.get_config()
    except Exception as exc:
        return f"unknown ({type(exc).__name__})"
    db = getattr(cfg, "rag_managed_db_config", None)
    if db is None:
        return "unknown (no config returned)"
    for name in ("unprovisioned", "serverless", "scaled", "enterprise", "basic"):
        if getattr(db, name, None) is not None:
            return name
    if getattr(db, "spanner", None) is not None:
        return "spanner"
    return "unknown"


def _cost_note(mode: str) -> str:
    if mode == "serverless":
        return "serverless -- billed per use, no provisioned instance"
    if mode == "unprovisioned":
        return "not billing"
    if mode.startswith("unknown"):
        return "could not read the tier; do not assume either way"
    return f"{mode} -- PROVISIONED INSTANCE, BILLING CONTINUOUSLY"


def up(args: argparse.Namespace) -> None:
    client = _client()
    pdfs = _policy_pdfs()

    corpus = _find_corpus(client)
    if corpus is None:
        print(f"Creating corpus '{config.RAG_CORPUS_DISPLAY_NAME}' ...")
        corpus = client.rag.create_corpus(
            rag_corpus=ap.RagCorpus(
                display_name=config.RAG_CORPUS_DISPLAY_NAME,
                description=(
                    "Tarnfield customer service policy documents. "
                    "Ephemeral -- see BUILD_PLAN 2.6."
                ),
            )
        )
    else:
        print(f"Corpus already exists: {corpus.name}")

    existing = {f.display_name for f in _files(client, corpus.name)}
    for pdf in pdfs:
        if pdf.name in existing:
            print(f"  = {pdf.name} (already ingested)")
            continue
        print(f"  + {pdf.name} ...", flush=True)
        # display_name is what the retrieval tools scope on (BUILD_PLAN 2.4),
        # so it must stay equal to the filename in .env.
        client.rag.upload_file(
            corpus_name=corpus.name, path=str(pdf), display_name=pdf.name
        )

    mode = _mode(client)
    print(f"\nCorpus ready: {corpus.name}")
    print(f"Billing mode: {mode}  ({_cost_note(mode)})")
    if mode not in ("serverless", "unprovisioned"):
        print("Run `make rag-down` AND `make rag-unprovision` when you stop working.")


def status(args: argparse.Namespace) -> None:
    client = _client()
    mode = _mode(client)
    print(f"Project  : {config.PROJECT_ID}")
    print(f"Location : {config.RAG_CORPUS_LOCATION}")
    print(f"Mode     : {mode}  ({_cost_note(mode)})")

    corpus = _find_corpus(client)
    if corpus is None:
        print(f"Corpus   : '{config.RAG_CORPUS_DISPLAY_NAME}' does not exist")
        if mode not in ("serverless", "unprovisioned"):
            print("\nNote: no corpus does NOT mean no bill. The mode above is")
            print("project-wide and bills independently. See `make rag-unprovision`.")
        return

    print(f"Corpus   : {corpus.name}")
    files = _files(client, corpus.name)
    print(f"Files    : {len(files)}")
    for f in files:
        print(f"  - {f.display_name}")


def down(args: argparse.Namespace) -> None:
    client = _client()
    corpus = _find_corpus(client)
    if corpus is None:
        print(f"No corpus named '{config.RAG_CORPUS_DISPLAY_NAME}'. Nothing to delete.")
    else:
        print(f"Deleting {corpus.name} ...")
        client.rag.delete_corpus(name=corpus.name)
        print("Deleted.")

    mode = _mode(client)
    print(f"\nBilling mode is still: {mode}  ({_cost_note(mode)})")
    if mode not in ("serverless", "unprovisioned"):
        print(
            "\nDeleting the corpus does not unprovision the RAG Engine managed\n"
            "database. Run `make rag-unprovision` to halt it -- that is destructive\n"
            f"across all of {config.PROJECT_ID} / {config.RAG_CORPUS_LOCATION}."
        )


def unprovision(args: argparse.Namespace) -> None:
    client = _client()
    target = f"{config.PROJECT_ID} / {config.RAG_CORPUS_LOCATION}"
    print(
        "This sets the RAG Engine managed DB to Unprovisioned.\n"
        f"It deletes ALL RAG Engine data in {target} -- every corpus in that\n"
        "location, including any not created by this project. It cannot be undone."
    )
    if not args.yes:
        typed = input(f"\nType the location to confirm ({config.RAG_CORPUS_LOCATION}): ")
        if typed.strip() != config.RAG_CORPUS_LOCATION:
            sys.exit("Aborted.")

    client.rag.update_config(
        updated_config=ap.RagEngineConfig(
            rag_managed_db_config=ap.RagManagedDbConfig(
                unprovisioned=ap.RagManagedDbConfigUnprovisioned()
            )
        )
    )
    print(f"Done. Mode is now: {_mode(client)}")


def serverless(args: argparse.Namespace) -> None:
    """Switch this project+location to serverless mode.

    Serverless is billed per use with no provisioned instance, which removes the
    standing cost that 2.5/2.6 are built around. It is also the only mode open to
    new projects in several regions -- Spanner mode is allowlist-only there.

    Changes project-level config, so it is a deliberate command rather than
    something `up` does silently.
    """
    client = _client()
    before = _mode(client)
    print(f"Mode is currently: {before}")
    if before == "serverless":
        print("Already serverless. Nothing to do.")
        return
    corpus = _find_corpus(client)
    if corpus is not None:
        print(
            f"\nA corpus exists ({corpus.name}).\n"
            "Switching modes can discard RAG Engine data. Run `make rag-down` first."
        )
        if not args.yes:
            sys.exit("Aborted.")
    client.rag.update_config(
        updated_config=ap.RagEngineConfig(
            rag_managed_db_config=ap.RagManagedDbConfig(
                serverless=ap.RagManagedDbConfigServerless()
            )
        )
    )
    print(f"Mode is now: {_mode(client)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("up", help="create the corpus and ingest docs/*.pdf").set_defaults(func=up)
    sub.add_parser("status", help="show corpus contents and billing mode").set_defaults(func=status)
    sub.add_parser("down", help="delete the corpus").set_defaults(func=down)
    srv = sub.add_parser("serverless", help="switch to serverless mode (no standing instance)")
    srv.add_argument("--yes", action="store_true", help="proceed even if a corpus exists")
    srv.set_defaults(func=serverless)
    unp = sub.add_parser("unprovision", help="halt RAG Engine billing (destructive)")
    unp.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    unp.set_defaults(func=unprovision)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
