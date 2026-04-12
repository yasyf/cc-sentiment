from __future__ import annotations

import anyio
import click

from client.models import AppState, ClientConfig


@click.group()
def main() -> None:
    pass


@main.command()
def setup() -> None:
    from client.signing import KeyDiscovery

    username = click.prompt("GitHub username")
    click.echo(f"Fetching SSH keys for {username}...")

    key_path = KeyDiscovery.match_github_key(username)
    click.echo(f"Matched key: {key_path}")

    state = AppState.load()
    state.config = ClientConfig(github_username=username, key_path=key_path)
    state.save()
    click.echo("Configuration saved.")


@main.command()
@click.option("--upload", "do_upload", is_flag=True, help="Upload results after scan")
def scan(do_upload: bool) -> None:
    from client.pipeline import Pipeline

    state = AppState.load()
    click.echo("Scanning for new transcripts...")

    records = anyio.run(Pipeline.run, state)

    if not records:
        click.echo("No new transcripts found.")
        return

    click.echo(f"Scored {len(records)} conversation buckets.")

    for record in records:
        click.echo(
            f"  {record.conversation_id} bucket {record.bucket_index}: "
            f"score {record.sentiment_score}"
        )

    if do_upload:
        _do_upload(state, records)


@main.command()
def upload() -> None:
    from client.upload import Uploader

    state = AppState.load()
    assert state.config is not None, "Run 'cc-sentiment setup' first"

    uploader = Uploader()
    pending = uploader.pending_records(state)

    if not pending:
        click.echo("No pending records to upload.")
        return

    click.echo(f"Uploading {len(pending)} sessions...")

    records = _collect_pending_records(state)
    anyio.run(_upload_records, uploader, records, state)
    click.echo("Upload complete.")


def _do_upload(state: AppState, records: list) -> None:
    from client.upload import Uploader

    assert state.config is not None, "Run 'cc-sentiment setup' first"

    uploader = Uploader()
    click.echo(f"Uploading {len(records)} records...")
    anyio.run(_upload_records, uploader, records, state)
    click.echo("Upload complete.")


def _collect_pending_records(state: AppState) -> list:
    from client.transcripts import ConversationBucketer, TranscriptDiscovery, TranscriptParser
    from client.models import SentimentRecord, SessionId

    pending_sessions = {
        sid for sid, info in state.processed.items() if not info.uploaded
    }
    all_records: list[SentimentRecord] = []

    for path in TranscriptDiscovery.find_transcripts():
        if SessionId(path.stem) in pending_sessions:
            messages = TranscriptParser.parse_file(path)
            buckets = ConversationBucketer.bucket_messages(messages)
            all_records.extend(
                SentimentRecord(
                    time=b.bucket_start,
                    conversation_id=b.session_id,
                    bucket_index=b.bucket_index,
                    sentiment_score=state.processed[b.session_id].buckets,
                )
                for b in buckets
            )

    return all_records


async def _upload_records(uploader, records: list, state: AppState) -> None:
    await uploader.upload(records, state)
