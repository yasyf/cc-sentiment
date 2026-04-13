<svelte:head>
	<title>Get Started - cc-sentiment</title>
</svelte:head>

<div class="min-h-screen bg-bg px-4 py-8 sm:px-6">
	<div class="mx-auto max-w-3xl">
		<header class="mb-8">
			<a href="/" class="text-accent hover:text-accent-hover transition-colors text-sm">&larr; Dashboard</a>
			<h1 class="mt-4 text-2xl font-semibold text-text">Get Started</h1>
			<p class="mt-2 text-sm text-text-muted">
				Contribute your Claude Code sentiment data to the public dashboard.
				All scoring happens locally on your machine &mdash; only aggregated scores are uploaded.
			</p>
		</header>

		<section class="space-y-6">
			<div class="rounded-xl border border-border bg-bg-card p-6">
				<h2 class="text-lg font-semibold text-text">1. Install the CLI</h2>
				<p class="mt-2 text-sm text-text-muted">
					Requires Python 3.12+ and macOS with Apple Silicon (for local ML inference).
				</p>
				<pre class="mt-3 overflow-x-auto rounded-lg bg-bg p-4 text-sm text-text-muted"><code>pip install cc-sentiment</code></pre>
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-6">
				<h2 class="text-lg font-semibold text-text">2. Set up signing keys</h2>
				<p class="mt-2 text-sm text-text-muted">
					Your uploads are cryptographically signed with your GitHub SSH or GPG key.
					The setup wizard auto-detects your keys and GitHub username.
				</p>
				<pre class="mt-3 overflow-x-auto rounded-lg bg-bg p-4 text-sm text-text-muted"><code>cc-sentiment setup</code></pre>
				<p class="mt-3 text-sm text-text-muted">
					If you don't have a signing key on GitHub, the wizard can generate a GPG key
					and upload it for you.
				</p>
			</div>

			<div class="rounded-xl border border-border bg-bg-card p-6">
				<h2 class="text-lg font-semibold text-text">3. Scan & upload</h2>
				<p class="mt-2 text-sm text-text-muted">
					Discovers Claude Code transcripts in <code class="rounded bg-bg-hover px-1.5 py-0.5 font-mono text-xs text-accent">~/.claude/projects/</code>,
					scores each conversation bucket locally using Gemma on MLX, and uploads the results.
				</p>
				<pre class="mt-3 overflow-x-auto rounded-lg bg-bg p-4 text-sm text-text-muted"><code>cc-sentiment scan --upload</code></pre>
				<p class="mt-3 text-sm text-text-muted">
					Run this periodically to keep the dashboard up to date. Only new or modified
					transcripts are processed.
				</p>
			</div>
		</section>

		<section class="mt-10 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">How it works</h2>
			<div class="mt-4 space-y-4 text-sm text-text-muted">
				<div class="flex gap-3">
					<span class="mt-0.5 text-accent font-mono text-xs">1</span>
					<p>
						<strong class="text-text">Transcript discovery</strong> &mdash;
						Finds Claude Code JSONL session files on your machine.
					</p>
				</div>
				<div class="flex gap-3">
					<span class="mt-0.5 text-accent font-mono text-xs">2</span>
					<p>
						<strong class="text-text">Local sentiment scoring</strong> &mdash;
						Each conversation is split into time buckets and scored 1-5 using
						a local Gemma 4 model via MLX. Nothing leaves your machine at this stage.
					</p>
				</div>
				<div class="flex gap-3">
					<span class="mt-0.5 text-accent font-mono text-xs">3</span>
					<p>
						<strong class="text-text">Signed upload</strong> &mdash;
						Scores are signed with your GitHub SSH or GPG key and uploaded to the server.
						The server verifies signatures against your public keys on GitHub.
					</p>
				</div>
				<div class="flex gap-3">
					<span class="mt-0.5 text-accent font-mono text-xs">4</span>
					<p>
						<strong class="text-text">Dashboard</strong> &mdash;
						Aggregated data powers the charts you see on the dashboard.
						Individual transcripts are never uploaded &mdash; only numeric scores.
					</p>
				</div>
			</div>
		</section>

		<section class="mt-10 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">Other commands</h2>
			<div class="mt-4 space-y-3 text-sm">
				<div class="flex items-baseline justify-between border-b border-border pb-3">
					<code class="font-mono text-accent text-xs">cc-sentiment scan</code>
					<span class="text-text-muted">Score transcripts without uploading</span>
				</div>
				<div class="flex items-baseline justify-between border-b border-border pb-3">
					<code class="font-mono text-accent text-xs">cc-sentiment upload</code>
					<span class="text-text-muted">Upload previously scored results</span>
				</div>
				<div class="flex items-baseline justify-between border-b border-border pb-3">
					<code class="font-mono text-accent text-xs">cc-sentiment rescan</code>
					<span class="text-text-muted">Clear state and re-score everything</span>
				</div>
				<div class="flex items-baseline justify-between">
					<code class="font-mono text-accent text-xs">cc-sentiment benchmark</code>
					<span class="text-text-muted">Benchmark inference engines</span>
				</div>
			</div>
		</section>

		<section class="mt-10 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">Privacy</h2>
			<p class="mt-3 text-sm text-text-muted">
				Your conversation content never leaves your machine. The CLI runs sentiment
				analysis locally using MLX on Apple Silicon. Only numeric scores (1-5),
				timestamps, and your GitHub username are uploaded. All uploads are
				cryptographically signed so they can't be spoofed.
			</p>
		</section>

		<footer class="mt-12 border-t border-border pt-6 pb-8 text-center text-xs text-text-dim">
			<a href="https://github.com/yasyf/cc-sentiment" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">
				GitHub
			</a>
		</footer>
	</div>
</div>
