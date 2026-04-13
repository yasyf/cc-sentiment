<svelte:head>
	<title>Get Started -- cc-sentiment</title>
	<meta name="description" content="Contribute your Claude Code sentiment data. Local ML scoring, cryptographic signing, public dashboard." />
</svelte:head>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border">
		<div class="mx-auto flex max-w-5xl items-baseline justify-between px-6 py-5">
			<h1 class="text-lg font-semibold tracking-tight text-text">
				<a href="/" class="hover:text-accent transition-colors">cc-sentiment</a>
			</h1>
			<nav class="flex items-center gap-5 text-sm text-text-muted">
				<a href="/" class="hover:text-text transition-colors">Dashboard</a>
				<a href="https://github.com/yasyf/cc-sentiment" class="hover:text-text transition-colors" target="_blank" rel="noopener">GitHub</a>
			</nav>
		</div>
	</header>

	<div class="mx-auto max-w-2xl px-6 py-10">
		<h2 class="text-3xl font-semibold tracking-tight text-text">Get Started</h2>
		<p class="mt-2 text-text-muted">
			Contribute your Claude Code sentiment data to the public dashboard.
			All scoring happens locally -- only numeric scores are uploaded.
		</p>

		<div class="mt-10 space-y-8">
			<section>
				<div class="flex items-center gap-3">
					<span class="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-xs font-medium text-white">1</span>
					<h3 class="text-base font-medium text-text">Install the CLI</h3>
				</div>
				<p class="mt-2 pl-9 text-sm text-text-muted">Requires Python 3.12+ and macOS with Apple Silicon.</p>
				<pre class="mt-2 ml-9 overflow-x-auto rounded border border-border bg-bg-code px-4 py-3 font-mono text-sm text-text-secondary"><code>pip install cc-sentiment</code></pre>
			</section>

			<section>
				<div class="flex items-center gap-3">
					<span class="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-xs font-medium text-white">2</span>
					<h3 class="text-base font-medium text-text">Set up signing</h3>
				</div>
				<p class="mt-2 pl-9 text-sm text-text-muted">
					The wizard auto-detects your GitHub SSH/GPG keys.
					It can generate a GPG key if you don't have one.
				</p>
				<pre class="mt-2 ml-9 overflow-x-auto rounded border border-border bg-bg-code px-4 py-3 font-mono text-sm text-text-secondary"><code>cc-sentiment setup</code></pre>
			</section>

			<section>
				<div class="flex items-center gap-3">
					<span class="flex h-6 w-6 items-center justify-center rounded-full bg-accent text-xs font-medium text-white">3</span>
					<h3 class="text-base font-medium text-text">Scan & upload</h3>
				</div>
				<p class="mt-2 pl-9 text-sm text-text-muted">
					Discovers transcripts in <code class="rounded bg-bg-code px-1 py-0.5 font-mono text-xs">~/.claude/projects/</code>,
					scores them locally with Gemma 4 on MLX, and uploads.
				</p>
				<pre class="mt-2 ml-9 overflow-x-auto rounded border border-border bg-bg-code px-4 py-3 font-mono text-sm text-text-secondary"><code>cc-sentiment scan --upload</code></pre>
			</section>
		</div>

		<hr class="my-10 border-border" />

		<section>
			<h3 class="text-base font-medium text-text">How it works</h3>
			<ol class="mt-4 space-y-3 pl-5 text-sm text-text-muted list-decimal marker:text-text-dim">
				<li>Finds Claude Code JSONL session files on your machine</li>
				<li>Splits conversations into time buckets and scores each 1-5 using a local Gemma 4 model via MLX</li>
				<li>Signs scores with your GitHub SSH or GPG key</li>
				<li>Server verifies signatures against your public keys on GitHub</li>
				<li>Aggregated scores power the <a href="/" class="text-accent hover:text-accent-hover transition-colors">dashboard</a></li>
			</ol>
		</section>

		<hr class="my-10 border-border" />

		<section>
			<h3 class="text-base font-medium text-text">Commands</h3>
			<div class="mt-4 space-y-2 text-sm">
				{#each [
					['cc-sentiment scan', 'Score transcripts without uploading'],
					['cc-sentiment upload', 'Upload previously scored results'],
					['cc-sentiment rescan', 'Clear state and re-score everything'],
					['cc-sentiment benchmark', 'Benchmark inference engines']
				] as [cmd, desc]}
					<div class="flex items-baseline justify-between py-1.5">
						<code class="font-mono text-xs text-text-secondary">{cmd}</code>
						<span class="text-text-dim">{desc}</span>
					</div>
				{/each}
			</div>
		</section>

		<hr class="my-10 border-border" />

		<section>
			<h3 class="text-base font-medium text-text">Privacy</h3>
			<p class="mt-2 text-sm text-text-muted">
				Conversation content never leaves your machine. Only numeric scores (1-5),
				timestamps, and your GitHub username are uploaded. All uploads are cryptographically
				signed.
			</p>
		</section>
	</div>

	<footer class="border-t border-border">
		<div class="mx-auto max-w-5xl px-6 py-6 text-center text-xs text-text-dim">
			<a href="https://github.com/yasyf/cc-sentiment" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">GitHub</a>
		</div>
	</footer>
</div>
